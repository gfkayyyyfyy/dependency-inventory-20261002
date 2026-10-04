"""同一 JSON 对象内重复键整份拒绝的回归测试。

标准 JSON 文本层面允许同一对象书写两个相同键名，多数解析器只保留最后
一个值；本次收紧要求：同一对象内出现两个解码后完全相同的键（按 JSON
字符串解码后的完整文本区分大小写比较，不做 Unicode 规范化），无论两个
值是否相同，整份输入即作废。规则覆盖：

- 顶层对象、packages、根条目、包条目、dependencies；
- 任意额外元数据与数组内的对象；
- 根节点不可达包内的任意层级（--reachable 等筛选不能绕过）；
- diff 任意一侧输入。

重复只在同一个对象内部判断：不同包条目各有 version、数组内不同对象各有
同名键均可接受。"alpha" 与 "\\u0061lpha" 解码后相同算重复，"alpha" 与
"Alpha" 不算；字符串值里的相同文字不参与判断。

公开函数 depinventory.load_lockfile 必须统一抛 depinventory.InputError
（不得逃逸 ValueError），且不返回根依赖或包映射；命令行 list / why /
why --from / parents / ancestors / descendants / diff / sbom 必须为
退出码 2、标准错误仅 "INPUT_ERROR\\n"、标准输出为空，不出现堆栈或部分
结果。输入校验先于查询包名是否存在；diff 任一侧有重复键即整体失败，
交换新旧文件结果相同。

无重复键的合法文件保持原样结果；既有结构限制及 NaN、Infinity、-Infinity
拒绝规则继续有效，合法数字 1e999 仍可读取。不以命令行结果代替函数验收：
异常类型用直接调用核对，CLI 单独核对退出码与输出流。处理前后输入文件
字节保持一致。仅使用标准库、全程离线。
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

# 任务示例：数组内对象出现重复键 tag，整份拒绝；改成 other 后合法。
TASK_EXAMPLE_BAD = (
    '{"lockfileVersion":3,"packages":{"":{}},'
    '"metadata":[{"tag":1,"tag":1}]}'
)
TASK_EXAMPLE_GOOD = (
    '{"lockfileVersion":3,"packages":{"":{}},'
    '"metadata":[{"tag":1,"other":1}]}'
)


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
    """公开读取入口：同一对象内重复键必须抛 InputError（函数级验收）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_input_error(self, content, name="lock.json"):
        path = write_text(self.tmp, content, name)
        with self.assertRaises(InputError) as ctx:
            load_lockfile(path)
        # 必须是解析阶段拒绝重复键导致的 InputError；ValueError 不得逃逸，
        # 异常类型必须恰为 InputError，而不是某个恰巧失败的其他异常。
        self.assertIs(type(ctx.exception), InputError)
        self.assertIsInstance(ctx.exception.__cause__, ValueError)
        self.assertNotIsInstance(ctx.exception, ValueError)

    def test_task_example_metadata_array_object(self):
        self.assert_input_error(TASK_EXAMPLE_BAD)

    def test_duplicate_key_at_top_level(self):
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"lockfileVersion":3}'
        )

    def test_duplicate_key_in_packages_object(self):
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{"":{},"":{}}}'
        )

    def test_duplicate_key_in_root_entry(self):
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{"":{"name":"a","name":"a"}}}'
        )

    def test_duplicate_key_in_root_dependencies(self):
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0","alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0"}'
            "}}"
        )

    def test_duplicate_key_in_package_entry_and_its_dependencies(self):
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{"":{},'
            '"node_modules/alpha":{"version":"1.0.0","version":"1.0.0"}'
            "}}"
        )
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0",'
            '"dependencies":{"beta":"2.0.0","beta":"2.0.0"}},'
            '"node_modules/beta":{"version":"2.0.0"}'
            "}}"
        )

    def test_duplicate_key_with_identical_values_still_rejected(self):
        # 两个键的值完全相同同样拒绝，不是"只留下一个值"。
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"meta":{"k":[1,2],"k":[1,2]}}'
        )

    def test_duplicate_key_deep_in_extra_metadata(self):
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"meta":{"a":[{"b":{"c":{"k":1,"k":2}}}]}}'
        )

    def test_duplicate_key_inside_unreachable_package(self):
        # orphan 已安装但不被任何节点引用：其内容不可达也必须整份拒绝。
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0"},'
            '"node_modules/orphan":{"version":"9.9.9",'
            '"deep":[[{"k":1,"k":1}]]}'
            "}}"
        )

    def test_escaped_and_plain_spellings_of_same_key_are_duplicate(self):
        # 键名按解码后的完整文本比较："alpha" 与 "lpha" 解码后相同。
        self.assert_input_error(
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"meta":{"alpha":1,"\\u0061lpha":2}}'
        )

    def test_no_root_deps_or_package_map_returned(self):
        # 抛异常即不会产生任何结果；这里再核对调用方拿不到部分结构。
        path = write_text(self.tmp, TASK_EXAMPLE_BAD)
        result = None
        with self.assertRaises(InputError):
            result = load_lockfile(path)
        self.assertIsNone(result)


class SameNameInDifferentObjectsAccepted(unittest.TestCase):
    """重复只在同一对象内部判断；跨对象同名键、大小写不同键均可接受。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, content):
        return load_lockfile(write_text(self.tmp, content))

    def test_same_key_in_different_package_entries(self):
        # 不同包条目各有 version / dependencies 键，互不构成重复。
        root_deps, packages_map = self.load(
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0",'
            '"dependencies":{"beta":"2.0.0"}},'
            '"node_modules/beta":{"version":"2.0.0",'
            '"dependencies":{}}'
            "}}"
        )
        self.assertEqual(root_deps, ["alpha"])
        self.assertEqual(packages_map["alpha"]["deps"], ["beta"])
        self.assertEqual(packages_map["beta"]["version"], "2.0.0")

    def test_same_key_in_different_array_objects(self):
        # 数组内不同对象各有 tag 键：不是同一对象内的重复。
        root_deps, packages_map = self.load(
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":[{"tag":1},{"tag":2}]}'
        )
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, {})

    def test_case_different_keys_are_not_duplicates(self):
        # 区分大小写："alpha" 与 "Alpha" 不是重复键。
        root_deps, packages_map = self.load(
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"meta":{"alpha":1,"Alpha":2,"ALPHA":3}}'
        )
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, {})

    def test_same_text_in_string_values_not_considered(self):
        # 字符串值里的相同文字不参与键重复判断。
        root_deps, packages_map = self.load(
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"meta":{"tag":"tag","other":["tag","tag"]}}'
        )
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, {})

    def test_task_example_fixed_version_succeeds(self):
        # 任务示例把第二个 tag 改为 other：list 成功输出 []，退出码 0，
        # 标准错误为空，标准输出末尾恰有一个换行。
        path = write_text(self.tmp, TASK_EXAMPLE_GOOD)
        result = run_cli(["list", path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")


class CliRejectsDuplicateKeys(unittest.TestCase):
    """各命令的退出码与输出流（不替代函数级验收）。"""

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

    def assert_cli_input_error(self, argv):
        result = run_cli(argv)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_all_commands_reject_task_example(self):
        path = write_text(self.tmp, TASK_EXAMPLE_BAD)
        for argv in (
            ["list", path],
            ["list", path, "--reachable"],
            ["list", path, "--unreachable"],
            ["why", path, "alpha"],
            ["why", path, "ghost"],
            ["why", path, "alpha", "--from", "beta"],
            ["parents", path, "alpha"],
            ["parents", path, "alpha", "--reachable"],
            ["ancestors", path, "alpha"],
            ["ancestors", path, "alpha", "--reachable"],
            ["descendants", path, "alpha"],
            ["sbom", path],
            ["sbom", path, "--reachable"],
            ["sbom", path, "--with-paths", "--with-dependencies", "--with-purl"],
        ):
            with self.subTest(argv=argv):
                self.assert_cli_input_error(argv)

    def test_unreachable_package_duplicate_not_bypassed_by_reachable(self):
        # 重复键落在不可达包内：--reachable 筛选发生在加载成功之后，
        # 不能绕过解析阶段的整份拒绝。
        path = write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1.0.0"}},'
            '"node_modules/alpha":{"version":"1.0.0"},'
            '"node_modules/orphan":{"version":"9.9.9","k":{"x":1,"x":1}}'
            "}}",
        )
        for argv in (
            ["list", path],
            ["list", path, "--reachable"],
            ["sbom", path, "--reachable"],
            ["ancestors", path, "alpha", "--reachable"],
            ["parents", path, "alpha", "--reachable"],
        ):
            with self.subTest(argv=argv):
                self.assert_cli_input_error(argv)

    def test_input_error_precedes_not_found(self):
        # 即使查询的包未安装，输入错误先于 NOT_FOUND：退出码 2 而非 1。
        path = write_text(self.tmp, TASK_EXAMPLE_BAD)
        result = run_cli(["why", path, "not-installed"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotEqual(result.stderr, "NOT_FOUND\n")

    def test_diff_either_side_invalid_and_swap_invariant(self):
        # 任一侧含重复键即整体失败，不产生部分差异；交换新旧文件结果相同。
        bad = write_text(self.tmp, TASK_EXAMPLE_BAD, "bad.json")
        self.assert_cli_input_error(["diff", bad, self.good])
        self.assert_cli_input_error(["diff", self.good, bad])
        self.assert_cli_input_error(["diff", bad, bad])
        self.assert_cli_input_error(["diff", bad, self.good, "--reachable"])
        self.assert_cli_input_error(["diff", self.good, bad, "--reachable"])

    def test_input_file_bytes_unchanged_after_failures(self):
        path = write_text(self.tmp, TASK_EXAMPLE_BAD)
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
        self.assertEqual(before, TASK_EXAMPLE_BAD.encode("utf-8"))


class ExistingBehaviorUnaffected(unittest.TestCase):
    """无重复键的既有合法文件保持原样结果；既有拒绝规则继续有效。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

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

    def test_bare_constants_still_rejected(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                path = write_text(
                    self.tmp,
                    '{"lockfileVersion":3,"packages":{"":{}},'
                    '"metadata":' + constant + "}",
                )
                with self.assertRaises(InputError):
                    load_lockfile(path)

    def test_valid_number_1e999_still_loads(self):
        path = write_text(
            self.tmp,
            '{"lockfileVersion":3,"packages":{"":{}},'
            '"metadata":{"big":1e999}}',
        )
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, {})

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
