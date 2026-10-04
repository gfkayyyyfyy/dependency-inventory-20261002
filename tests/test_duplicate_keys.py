"""JSON 对象重复键收紧判定的回归测试（仅标准库、全程离线）。

规则：同一 JSON 对象内出现两个按 JSON 字符串解码后完全相同的键，整份
输入作废，即使两个值相同也拒绝。覆盖：

- 顶层对象、packages 对象、空串根条目、每个包条目、dependencies 对象；
- 额外元数据对象，以及数组内嵌套的对象；
- 自根节点不可达包内任意层级的重复键（--reachable 等筛选不能绕过）；
- diff 任意一侧输入。

重复只在同一个对象内部判断：不同包条目各有 version、数组内不同对象各
有同名键仍合法。键名按解码后的完整文本区分大小写比较，不做 Unicode
规范化：键 alpha 与其转义写法（首字母 a 写作反斜杠 u0061）算重复，
alpha 与 Alpha 不算；字符串值里的同文文本不参与判断。

公开函数 depinventory.load_lockfile 必须统一抛 depinventory.InputError
（不得逃逸 ValueError），且不返回根依赖或包映射；命令行 list / why /
why --from / diff / sbom 必须为退出码 2、标准错误仅 "INPUT_ERROR\\n"、
标准输出为空，不出现堆栈或部分结果。输入校验仍先于查询包名是否存在。
合法既有文件结果保持原样；裸 NaN/Infinity/-Infinity 仍拒绝，合法数字
1e999 仍可读取。处理前后输入文件字节保持一致。
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

# JSON 字符串里的 alpha 转义写法（不在本源码里直接写反斜杠 u，以免歧义）。
ALPHA_ESCAPED = '"' + chr(92) + "u0061lpha" + '"'


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


class LoadLockfileRejectsDuplicateKeys(unittest.TestCase):
    """公开读取入口：同一对象内的重复键必须抛 InputError（函数级验收）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_input_error(self, content, name="lock.json"):
        path = write_text(self.tmp, content, name)
        with self.assertRaises(InputError) as ctx:
            load_lockfile(path)
        # 必须是解析阶段重复键回调抛出的 ValueError 被包成 InputError；
        # ValueError 不得逃逸，异常类型必须恰为 InputError。
        self.assertIs(type(ctx.exception), InputError)
        self.assertIsInstance(ctx.exception.__cause__, ValueError)
        self.assertNotIsInstance(ctx.exception, ValueError)

    def test_task_example_metadata_array_object_duplicate(self):
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":[{"tag":1,"tag":1}]}'
        )

    def test_duplicate_rejected_even_when_values_identical(self):
        # 两个值都是 1 仍拒绝；字符串值相同也不例外。
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":[{"tag":"x","tag":"x"}]}'
        )

    def test_duplicate_at_every_object_location(self):
        cases = [
            # 顶层对象重复键。
            '{"lockfileVersion":3,"packages":{"":{}},"lockfileVersion":3}',
            # packages 对象重复键（空串根条目写两次）。
            '{"lockfileVersion":3,"packages":{"":{},"":{}}}',
            # 根条目内部重复键。
            '{"lockfileVersion":3,"packages":{"":{"x":1,"x":2}}}',
            # 已安装包条目内部重复键。
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0","v":1,"v":2}}}',
            # dependencies 对象内部重复键（值相同也拒绝）。
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0","alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0"}}}',
            # 额外元数据对象内部重复键。
            '{"lockfileVersion":3,"packages":{"":{}},"metadata":{"a":1,"a":2}}',
            # 数组内对象重复键。
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":[{"ok":1},{"deep":[{"k":1,"k":2}]}]}',
            # 根条目深层嵌套对象内的重复键。
            '{"lockfileVersion":3,"packages":{"":{"a":[{"b":{"c":1,"c":2}}]}}}',
        ]
        for content in cases:
            with self.subTest(content=content):
                self.assert_input_error(content)

    def test_duplicate_inside_unreachable_package_rejected(self):
        # orphan 已安装但不被任何节点引用：其重复键同样使整份输入失败。
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0"},'
            '"node_modules/orphan":{"version":"9.9.9","deep":[{"k":1,"k":2}]}'
            "}}"
        )

    def test_escaped_key_spelling_counts_as_duplicate(self):
        # 键 alpha 与其 JSON 转义写法（首字母 a 写作反斜杠 u0061）
        # 解码后为同一文本，落在同一对象内算重复。
        content = (
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":{"alpha":1,"ESC":2}}'
        ).replace('"ESC"', ALPHA_ESCAPED)
        # 守护测试意图：源文本确实是带反斜杠 u 转义的 JSON 键写法。
        self.assertEqual(ALPHA_ESCAPED, '"\\u0061lpha"')
        self.assert_input_error(content)

    def test_case_difference_is_not_duplicate(self):
        # 区分大小写：alpha 与 Alpha 是两个键，合法。
        path = write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":{"alpha":1,"Alpha":2}}',
            "case.json",
        )
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual((root_deps, packages_map), ([], {}))

    def test_same_key_in_distinct_objects_is_allowed(self):
        # 不同包条目各有 version 本就如此；这里再核对数组内不同对象的
        # 同名键、转义写法落在另一个对象中也合法。
        content = (
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"a":"1"}},'
            '"node_modules/a":{"version":"1","alpha":1},'
            '"node_modules/b":{"version":"2","ESC":2}'
            "}}"
        ).replace('"ESC"', ALPHA_ESCAPED)
        path = write_text(self.tmp, content, "distinct.json")
        _, packages_map = load_lockfile(path)
        self.assertEqual(set(packages_map), {"a", "b"})

    def test_duplicate_string_values_are_not_keys(self):
        # 两个字符串值文本相同不触发重复键判定。
        path = write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"a":"alpha","b":"alpha","c":["alpha","alpha"]}',
            "values.json",
        )
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual((root_deps, packages_map), ([], {}))

    def test_no_root_deps_or_package_map_returned(self):
        result = None
        path = write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":[{"tag":1,"tag":1}]}',
        )
        with self.assertRaises(InputError):
            result = load_lockfile(path)
        self.assertIsNone(result)


class CliRejectsDuplicateKeys(unittest.TestCase):
    """list / why / why --from / diff / sbom 的退出码与输出流。"""

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

    def task_bad_file(self, name="dup.json"):
        return write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":[{"tag":1,"tag":1}]}',
            name,
        )

    def assert_cli_input_error(self, argv):
        result = run_cli(argv)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_task_example_list_fails(self):
        self.assert_cli_input_error(["list", self.task_bad_file()])

    def test_task_example_second_tag_renamed_succeeds_empty(self):
        path = write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":[{"tag":1,"other":1}]}',
            "renamed.json",
        )
        result = run_cli(["list", path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    def test_list_why_sbom_reject_duplicate(self):
        path = self.task_bad_file("all.json")
        for argv in (
            ["list", path],
            ["why", path, "alpha"],
            ["why", path, "ghost"],
            ["why", path, "alpha", "--from", "beta"],
            ["sbom", path],
        ):
            with self.subTest(argv=argv):
                self.assert_cli_input_error(argv)

    def test_validation_precedes_package_existence_check(self):
        # 即使查询的包未安装，重复键输入错误先于 NOT_FOUND：退出码 2。
        path = self.task_bad_file("miss.json")
        result = run_cli(["why", path, "not-installed"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotEqual(result.stderr, "NOT_FOUND\n")

    def test_reachable_filter_does_not_bypass_duplicate(self):
        # 重复键落在自根节点不可达的 orphan 内：任何筛选都无法绕过。
        path = write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0"},'
            '"node_modules/orphan":{"version":"9.9.9","k":[{"v":1,"v":2}]}'
            "}}",
            "orphan.json",
        )
        for argv in (
            ["list", path],
            ["list", "--reachable", path],
            ["list", "--unreachable", path],
            ["why", path, "alpha"],
            ["why", path, "orphan"],
            ["sbom", "--reachable", path],
        ):
            with self.subTest(argv=argv):
                self.assert_cli_input_error(argv)

    def test_diff_either_side_invalid_and_swap_invariant(self):
        bad = self.task_bad_file("bad.json")
        self.assert_cli_input_error(["diff", bad, self.good])
        self.assert_cli_input_error(["diff", self.good, bad])

    def test_input_file_bytes_unchanged_after_failures(self):
        path = self.task_bad_file("immutable.json")
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
            after = handle.read()
        self.assertEqual(after, before)
        self.assertEqual(
            hashlib.sha256(after).hexdigest(), hashlib.sha256(before).hexdigest()
        )


class ExistingRulesAndOutputsUnchanged(unittest.TestCase):
    """收紧后既有合法文件结果保持原样，原有解析规则继续有效。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_constants_still_rejected_and_big_number_still_reads(self):
        nan_path = write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{"":{}},"metadata":NaN}',
            "nan.json",
        )
        with self.assertRaises(InputError):
            load_lockfile(nan_path)
        big_path = write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{"":{}},"metadata":{"big":1e999}}',
            "big.json",
        )
        root_deps, packages_map = load_lockfile(big_path)
        self.assertEqual((root_deps, packages_map), ([], {}))

    def test_demo_fixtures_unchanged_and_not_mutated(self):
        for path in (DEMO_LOCK, NEW_LOCK):
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            load_lockfile(path)
            with open(path, "rb") as handle:
                self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), digest)

    def test_demo_list_succeeds(self):
        result = run_cli(["list", DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )


if __name__ == "__main__":
    unittest.main()
