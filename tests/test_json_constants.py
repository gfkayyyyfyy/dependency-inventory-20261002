"""非标准 JSON 数值常量（NaN / Infinity / -Infinity）收紧判定的回归测试。

标准 JSON 不允许这三个裸常量，Python json 默认把它们当作合法数值扩展
接受；本次修复要求整份输入作废，无论常量位于：

- 顶层额外元数据（如 metadata，不参与依赖分析也不能放过）；
- 根条目、已安装包条目内部任意层级的对象或数组；
- 根节点不可达包内的任意层级；
- diff 任意一侧输入。

公开函数 depinventory.load_lockfile 必须统一抛 depinventory.InputError
（不得逃逸 ValueError），且不返回根依赖或包映射；命令行 list / why /
why --from / diff / sbom 必须为退出码 2、标准错误仅 "INPUT_ERROR\\n"、
标准输出为空，不出现堆栈或部分结果。why 查询未安装包或带 --from 时也
先报输入错误；diff 交换新旧文件不改变错误结果。

合法 JSON 文本不受影响：字符串值 "NaN"/"Infinity"/"-Infinity"、含这些
文字的描述、同名对象键均按原字段语义处理；忽略元数据中的整数、负数、
小数、指数数字（含 1e999 这样转换后超出浮点范围的合法文本）仍可读取。

不以命令行结果代替函数验收：异常类型用直接调用核对，CLI 单独核对退出码
与输出流。处理前后输入文件字节保持一致。仅使用标准库、全程离线。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")

CONSTANTS = ["NaN", "Infinity", "-Infinity"]

# 结构最小合法锁文件，常量在顶层额外元数据中（不参与任何分析）。
ROOT_ONLY = '{"lockfileVersion": 3, "packages": {"": {}}}'


def write_text(directory, content, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


class LoadLockfileRejectsConstants(unittest.TestCase):
    """公开读取入口：常量在任意值位置出现都必须抛 InputError（函数级验收）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_input_error(self, content, name="lock.json"):
        path = write_text(self.tmp, content, name)
        with self.assertRaises(InputError) as ctx:
            load_lockfile(path)
        # 必须是解析阶段拒绝常量导致的 InputError；ValueError 不得逃逸，
        # 异常类型必须恰为 InputError，而不是某个恰巧失败的其他异常。
        self.assertIs(type(ctx.exception), InputError)
        self.assertIsInstance(ctx.exception.__cause__, ValueError)
        self.assertNotIsInstance(ctx.exception, ValueError)

    def test_task_example_top_level_metadata(self):
        for constant in CONSTANTS:
            with self.subTest(constant=constant):
                self.assert_input_error(
                    '{"lockfileVersion":3,"packages":{"":{}},"metadata":' + constant + "}"
                )

    def test_constants_inside_root_entry_nested_objects_and_arrays(self):
        templates = [
            '{"lockfileVersion":3,"packages":{"":{"meta":{"deep":{}}}}}',
            '{"lockfileVersion":3,"packages":{"":{"meta":[{"deep":[{}]}]}}}',
            '{"lockfileVersion":3,"packages":{"":{"a":[{"b":[[{}]]}]}}}',
        ]
        for constant in CONSTANTS:
            for template in templates:
                with self.subTest(constant=constant, template=template):
                    self.assert_input_error(template.replace("{}", constant))

    def test_constants_inside_reachable_installed_package(self):
        # 常量落在已安装包条目的忽略字段深处，不能跳过无关字段继续分析。
        template = (
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0","meta":[{"k":{}}]}'
            "}}"
        )
        for constant in CONSTANTS:
            with self.subTest(constant=constant):
                self.assert_input_error(template.replace("{}", constant))

    def test_constants_inside_unreachable_package(self):
        # orphan 已安装但不被任何节点引用：其内容不可达也必须整份拒绝。
        template = (
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0"},'
            '"node_modules/orphan":{"version":"9.9.9","deep":[[{"k":{}}]]}'
            "}}"
        )
        for constant in CONSTANTS:
            with self.subTest(constant=constant):
                self.assert_input_error(template.replace("{}", constant))

    def test_constant_inside_dependency_spec_object_value_position(self):
        # dependencies 的值是字符串，字符串 "NaN" 合法；裸常量非法。
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":' + "NaN" + "}},"
            '"node_modules/alpha":{"version":"1.0.0"}'
            "}}"
        )

    def test_no_root_deps_or_package_map_returned(self):
        # 抛异常即不会产生任何结果；这里再核对调用方拿不到部分结构。
        path = write_text(self.tmp, ROOT_ONLY[:-1] + ',"metadata":NaN}')
        result = None
        with self.assertRaises(InputError):
            result = load_lockfile(path)
        self.assertIsNone(result)


class CliRejectsConstants(unittest.TestCase):
    """list / why / why --from / diff / sbom 的退出码与输出流（不替代函数验收）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.good = write_text(
            self.tmp,
            json.dumps(
                {
                    "lockfileVersion": 3,
                    "packages": {
                        "": {"dependencies": {"alpha": "1.0.0"}},
                        "node_modules/alpha": {
                            "version": "1.0.0",
                            "dependencies": {"beta": "2.0.0"},
                        },
                        "node_modules/beta": {"version": "2.0.0"},
                        "node_modules/orphan": {"version": "9.9.9"},
                    },
                }
            ),
            "good.json",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def bad_file(self, name="bad.json", constant="NaN", where="metadata"):
        if where == "metadata":
            content = ROOT_ONLY[:-1] + ',"metadata":' + constant + "}"
        elif where == "unreachable":
            content = (
                '{"lockfileVersion":3,"packages":{'
                '"":{"dependencies":{"alpha":"1.0.0"}},'
                '"node_modules/alpha":{"version":"1.0.0"},'
                '"node_modules/orphan":{"version":"9.9.9","k":[['
                + constant
                + "]]}}}"
            )
        else:  # root entry deep nesting
            content = (
                '{"lockfileVersion":3,"packages":{"":{"a":[{"b":'
                + constant
                + "}]}}}"
            )
        return write_text(self.tmp, content, name)

    def assert_cli_input_error(self, argv):
        result = run_cli(argv)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_list_why_and_sbom_reject_each_constant(self):
        for constant in CONSTANTS:
            path = self.bad_file(constant + ".json", constant)
            for argv in (
                ["list", path],
                ["why", path, "alpha"],
                ["why", path, "ghost"],
                ["why", path, "alpha", "--from", "beta"],
                ["why", path, "ghost", "--from", "alpha"],
                ["why", path, "alpha", "--from", "ghost"],
                ["sbom", path],
            ):
                with self.subTest(constant=constant, argv=argv):
                    self.assert_cli_input_error(argv)

    def test_why_missing_package_reports_input_error_first(self):
        # 即使查询的包未安装，输入错误先于 NOT_FOUND：退出码 2 而非 1。
        path = self.bad_file("miss.json", "Infinity")
        result = run_cli(["why", path, "not-installed"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotEqual(result.stderr, "NOT_FOUND\n")

    def test_unreachable_package_constant_rejected_by_all_entry_points(self):
        path = self.bad_file("orphan.json", "-Infinity", where="unreachable")
        for argv in (
            ["list", path],
            ["why", path, "alpha"],
            ["why", path, "orphan"],
            ["sbom", path],
        ):
            with self.subTest(argv=argv):
                self.assert_cli_input_error(argv)

    def test_root_entry_nested_constant_rejected(self):
        path = self.bad_file("root.json", "NaN", where="root")
        self.assert_cli_input_error(["list", path])

    def test_diff_either_side_invalid_and_swap_invariant(self):
        bad = self.bad_file("bad.json", "NaN")
        # 任一侧含常量即整体失败；交换新旧文件结果相同。
        self.assert_cli_input_error(["diff", bad, self.good])
        self.assert_cli_input_error(["diff", self.good, bad])
        for constant in ("Infinity", "-Infinity"):
            other = self.bad_file(constant + ".json", constant)
            self.assert_cli_input_error(["diff", other, self.good])
            self.assert_cli_input_error(["diff", self.good, other])

    def test_input_file_bytes_unchanged_after_failures(self):
        path = self.bad_file("immutable.json", "NaN")
        with open(path, "rb") as handle:
            before = handle.read()
        for argv in (
            ["list", path],
            ["why", path, "ghost"],
            ["sbom", path],
            ["diff", path, self.good],
            ["diff", self.good, path],
        ):
            self.assert_cli_input_error(argv)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(
            before, b'{"lockfileVersion": 3, "packages": {"": {}},"metadata":NaN}'
        )


class StringsAndValidNumbersUnaffected(unittest.TestCase):
    """合法 JSON 字符串、同名键及合法数字（含 1e999）不受本次收紧影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_constant_spellings_as_string_values_load(self):
        for spelling in CONSTANTS:
            data = {
                "lockfileVersion": 3,
                "packages": {"": {}},
                "metadata": spelling,
            }
            path = os.path.join(self.tmp, "s.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            root_deps, packages_map = load_lockfile(path)
            self.assertEqual(root_deps, [])
            self.assertEqual(packages_map, {})

    def test_task_example_string_nan_list_succeeds_empty(self):
        # 把 metadata 的值改成 "NaN"：list 成功输出 []，退出码 0、stderr 空。
        path = os.path.join(self.tmp, "nan-str.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"lockfileVersion":3,"packages":{"":{}},"metadata":"NaN"}')
        result = run_cli(["list", path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    def test_constant_words_in_descriptions_and_as_keys_load(self):
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"NaN": 1, "Infinity": 2, "-Infinity": 3},
                "node_modules/alpha": {
                    "version": "1.0.0",
                    "description": "values like NaN, Infinity and -Infinity in prose",
                    "NaN": "Infinity",
                    "-Infinity": ["NaN", {"Infinity": "-Infinity"}],
                },
            },
        }
        path = os.path.join(self.tmp, "words.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        _, packages_map = load_lockfile(path)
        self.assertEqual(packages_map["alpha"]["version"], "1.0.0")

    def test_valid_number_forms_in_ignored_metadata_load(self):
        # 整数、负数、小数、指数形式均为合法 JSON 文本；1e999 转换后为
        # 浮点 Infinity，但文本合法，不能仅因超出浮点范围而拒绝。
        path = os.path.join(self.tmp, "nums.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                '{"lockfileVersion":3,"packages":{"":{}},'
                '"metadata":{"i":1,"neg":-5,"frac":-0.25,"exp":1.5e3,"big":1e999,'
                '"tiny":-1e-999,"arr":[1e999,-1e999]}}'
            )
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, {})

    def test_bare_constant_but_string_elsewhere_is_still_rejected(self):
        # 同一文件里即使大量同名字符串存在，单个裸常量仍必须整份拒绝。
        path = os.path.join(self.tmp, "mix.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                '{"lockfileVersion":3,"packages":{"":{}},'
                '"ok":["NaN","Infinity","-Infinity"],"bad":Infinity}'
            )
        with self.assertRaises(InputError):
            load_lockfile(path)


class ExistingOutputsUnchanged(unittest.TestCase):
    """收紧后既有样例的 list / why / diff / sbom 输出保持原状。"""

    def test_demo_list_why_diff_sbom_unchanged(self):
        listed = run_cli(["list", DEMO_LOCK])
        self.assertEqual(listed.returncode, 0)
        self.assertEqual(
            json.loads(listed.stdout),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )
        why = run_cli(["why", DEMO_LOCK, "beta"])
        self.assertEqual(
            json.loads(why.stdout),
            {"name": "beta", "path": ["$root", "alpha", "beta"]},
        )
        diff = run_cli(["diff", DEMO_LOCK, NEW_LOCK])
        self.assertEqual(
            json.loads(diff.stdout),
            [
                {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
                {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"},
                {"name": "gamma", "change": "added", "before": None, "after": "3.0.0"},
            ],
        )
        sbom = run_cli(["sbom", DEMO_LOCK])
        components = json.loads(sbom.stdout)["components"]
        self.assertEqual(
            [(c["license"], c["securityStatus"]) for c in components],
            [("unknown", "unknown"), ("unknown", "unknown")],
        )

    def test_demo_fixture_files_unchanged(self):
        # 修复只在读取侧加判定，不改写夹具。
        for path in (DEMO_LOCK, NEW_LOCK):
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            load_lockfile(path)
            with open(path, "rb") as handle:
                self.assertEqual(
                    hashlib.sha256(handle.read()).hexdigest(), digest
                )


if __name__ == "__main__":
    unittest.main()
