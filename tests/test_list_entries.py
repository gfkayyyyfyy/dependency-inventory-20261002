"""四种清单（完整/直接/可达/不可达）共用条目结构的回归测试（仅标准库、离线）。

lockfile.py 中 list_items、direct_items、reachable_items、unreachable_items
统一经 _build_items 构造条目，本文件固定重构后必须保留的既有行为：

- 四种结果均为数组，每项仅含 name、version、direct；版本字符串原样保留，
  根项目不进入结果；按完整包名 Unicode 码点升序排列，区分大小写，
  作用域包视为一个名称；
- direct 只表示根 dependencies 是否直接声明该包，其他包的引用不改变它；
- demo-lock.json 验收：完整与可达结果依次为 alpha@1.0.0、beta@2.0.0
  （direct 为 true、false），直接结果只有 alpha，不可达结果为 []；
- --direct 与 --reachable 合用不改变直接结果，与 --unreachable 合用返回
  []；--reachable 与 --unreachable 同时出现在读取文件前失败，附加
  --direct 也一样；
- 互斥冲突、文件不可读、JSON 或结构校验失败均退出码 2、stdout 为空、
  stderr 仅 "INPUT_ERROR\\n"；成功时退出码 0、stderr 为空、stdout 为
  单个 JSON 文档加末尾一个换行；
- 实际输出含孤立代理码点时同样按输入错误处理，被筛选排除的版本字符串
  不影响成功结果；
- 根 dependencies 省略或为空时直接与可达结果为 []，不可达结果等于完整
  清单；调用不修改 root_deps、packages_map 及其内的 deps 列表；
- 函数结果与对应 CLI 输出逐项一致。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import (
    InputError,
    list_items,
    load_lockfile,
    reachable_items,
    unreachable_items,
)
from depinventory.lockfile import direct_items

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
SURROGATE_LOCK = os.path.join(REPO_ROOT, "surrogate-lock.json")

DEMO_FULL = [
    {"name": "alpha", "version": "1.0.0", "direct": True},
    {"name": "beta", "version": "2.0.0", "direct": False},
]
DEMO_DIRECT = [
    {"name": "alpha", "version": "1.0.0", "direct": True},
]


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def assert_input_error(test, result):
    test.assertEqual(result.returncode, 2)
    test.assertEqual(result.stdout, "")
    test.assertEqual(result.stderr, "INPUT_ERROR\n")


def assert_success_doc(test, result):
    test.assertEqual(result.returncode, 0)
    test.assertEqual(result.stderr, "")
    # 单个 JSON 文档加末尾一个换行。
    test.assertTrue(result.stdout.endswith("\n"))
    test.assertEqual(result.stdout.count("\n"), 1)
    return json.loads(result.stdout)


class DemoAcceptance(unittest.TestCase):
    """demo-lock.json：四种清单的函数结果与 CLI 输出逐项一致。"""

    def setUp(self):
        self.root_deps, self.packages_map = load_lockfile(DEMO_LOCK)

    def test_function_results(self):
        self.assertEqual(list_items(self.root_deps, self.packages_map), DEMO_FULL)
        self.assertEqual(
            reachable_items(self.root_deps, self.packages_map), DEMO_FULL
        )
        self.assertEqual(direct_items(self.root_deps, self.packages_map), DEMO_DIRECT)
        self.assertEqual(unreachable_items(self.root_deps, self.packages_map), [])

    def test_entries_carry_only_name_version_direct(self):
        for func in (list_items, direct_items, reachable_items, unreachable_items):
            for item in func(self.root_deps, self.packages_map):
                self.assertEqual(set(item.keys()), {"name", "version", "direct"})

    def test_cli_matches_function_results(self):
        cases = [
            (["list", DEMO_LOCK], DEMO_FULL),
            (["list", DEMO_LOCK, "--reachable"], DEMO_FULL),
            (["list", DEMO_LOCK, "--direct"], DEMO_DIRECT),
            (["list", DEMO_LOCK, "--unreachable"], []),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                result = run_cli(argv)
                doc = assert_success_doc(self, result)
                self.assertEqual(doc, expected)
                self.assertEqual(
                    result.stdout, json.dumps(expected, ensure_ascii=False) + "\n"
                )


class FilterCombinations(unittest.TestCase):
    """--direct 与可达性筛选的组合语义及互斥冲突的提前失败。"""

    def test_direct_with_reachable_equals_direct(self):
        result = run_cli(["list", DEMO_LOCK, "--direct", "--reachable"])
        self.assertEqual(assert_success_doc(self, result), DEMO_DIRECT)

    def test_direct_with_unreachable_is_empty(self):
        result = run_cli(["list", DEMO_LOCK, "--direct", "--unreachable"])
        doc = assert_success_doc(self, result)
        self.assertEqual(doc, [])
        self.assertEqual(result.stdout, "[]\n")

    def test_reachable_unreachable_conflict_fails_before_reading(self):
        # 路径不存在也报互斥冲突而非文件错误：冲突判定在读取文件之前。
        missing = os.path.join(REPO_ROOT, "no-such-lock.json")
        for extra in ([], ["--direct"]):
            argv = ["list", missing, "--reachable", "--unreachable", *extra]
            with self.subTest(argv=argv):
                assert_input_error(self, run_cli(argv))


class EmptyRootDependencies(unittest.TestCase):
    """根 dependencies 省略或为空：直接/可达为 []，不可达等于完整清单。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def data(self, root_node):
        return {
            "lockfileVersion": 3,
            "packages": {
                "": root_node,
                "node_modules/alpha": {
                    "version": "1.0.0",
                    "dependencies": {"beta": "*"},
                },
                "node_modules/beta": {"version": "2.0.0"},
            },
        }

    def check(self, root_node, name):
        path = write_lockfile(self.tmp, self.data(root_node), name)
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, [])

        full = [
            {"name": "alpha", "version": "1.0.0", "direct": False},
            {"name": "beta", "version": "2.0.0", "direct": False},
        ]
        self.assertEqual(list_items(root_deps, packages_map), full)
        self.assertEqual(direct_items(root_deps, packages_map), [])
        self.assertEqual(reachable_items(root_deps, packages_map), [])
        self.assertEqual(unreachable_items(root_deps, packages_map), full)

        self.assertEqual(assert_success_doc(self, run_cli(["list", path])), full)
        self.assertEqual(
            run_cli(["list", path, "--direct"]).stdout, "[]\n"
        )
        self.assertEqual(
            run_cli(["list", path, "--reachable"]).stdout, "[]\n"
        )
        self.assertEqual(
            assert_success_doc(self, run_cli(["list", path, "--unreachable"])),
            full,
        )

    def test_root_dependencies_omitted(self):
        self.check({}, "omitted.json")

    def test_root_dependencies_empty_object(self):
        self.check({"dependencies": {}}, "empty.json")


class SortingAndDirectSemantics(unittest.TestCase):
    """码点升序、区分大小写、作用域包整体排序；direct 只看根声明。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_codepoint_order_and_direct_flag(self):
        # "@" (0x40) < "A" (0x41) < "a" (0x61)：作用域包整体参与排序。
        # beta 被 alpha 引用但未被根声明：direct 仍为 false。
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "*", "Zulu": "*"}},
                "node_modules/alpha": {
                    "version": "1.0.0",
                    "dependencies": {"beta": "*", "@scope/tool": "*"},
                },
                "node_modules/beta": {"version": "2.0.0"},
                "node_modules/@scope/tool": {"version": "3.0.0"},
                "node_modules/Zulu": {"version": "4.0.0"},
            },
        }
        path = write_lockfile(self.tmp, data)
        root_deps, packages_map = load_lockfile(path)
        expected = [
            {"name": "@scope/tool", "version": "3.0.0", "direct": False},
            {"name": "Zulu", "version": "4.0.0", "direct": True},
            {"name": "alpha", "version": "1.0.0", "direct": True},
            {"name": "beta", "version": "2.0.0", "direct": False},
        ]
        self.assertEqual(list_items(root_deps, packages_map), expected)
        self.assertEqual(reachable_items(root_deps, packages_map), expected)
        self.assertEqual(
            direct_items(root_deps, packages_map),
            [
                {"name": "Zulu", "version": "4.0.0", "direct": True},
                {"name": "alpha", "version": "1.0.0", "direct": True},
            ],
        )
        self.assertEqual(unreachable_items(root_deps, packages_map), [])
        self.assertEqual(
            assert_success_doc(self, run_cli(["list", path])), expected
        )


class InputsNotMutated(unittest.TestCase):
    """四种清单函数不修改 root_deps、packages_map 及其内的 deps 列表。"""

    def test_inputs_unchanged_after_all_four_calls(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        snapshot_deps = copy.deepcopy(root_deps)
        snapshot_map = copy.deepcopy(packages_map)

        for func in (list_items, direct_items, reachable_items, unreachable_items):
            func(root_deps, packages_map)

        self.assertEqual(root_deps, snapshot_deps)
        self.assertEqual(packages_map, snapshot_map)


class FailureBoundary(unittest.TestCase):
    """读取、解析、结构校验与输出编码失败统一为退出码 2 的 INPUT_ERROR。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_unreadable_file(self):
        missing = os.path.join(self.tmp, "missing.json")
        assert_input_error(self, run_cli(["list", missing]))

    def test_invalid_json(self):
        path = os.path.join(self.tmp, "broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        assert_input_error(self, run_cli(["list", path]))

    def test_invalid_entry_on_unreachable_package_not_masked(self):
        # 不可达 gamma 的非法 dependencies 不能被 --reachable/--direct 掩盖。
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "*"}},
                "node_modules/alpha": {"version": "1.0.0"},
                "node_modules/gamma": {"version": "3.0.0", "dependencies": None},
            },
        }
        path = write_lockfile(self.tmp, data)
        with self.assertRaises(InputError):
            load_lockfile(path)
        for extra in ([], ["--reachable"], ["--direct"], ["--unreachable"]):
            with self.subTest(extra=extra):
                assert_input_error(self, run_cli(["list", path, *extra]))


class SurrogateOutputBoundary(unittest.TestCase):
    """孤立代理码点：进入实际输出才失败，被筛选排除的版本不影响成功。"""

    def test_output_with_lone_surrogate_is_input_error(self):
        # surrogate-lock.json 中 beta 可达且版本含孤立高代理：
        # 完整与可达清单都输出版本，均失败。
        assert_input_error(self, run_cli(["list", SURROGATE_LOCK]))
        assert_input_error(self, run_cli(["list", SURROGATE_LOCK, "--reachable"]))

    def test_excluded_surrogate_version_does_not_break_result(self):
        # --direct 只保留 alpha，beta 的问题版本被排除：成功。
        result = run_cli(["list", SURROGATE_LOCK, "--direct"])
        self.assertEqual(assert_success_doc(self, result), DEMO_DIRECT)
        # --unreachable 结果为空，同样不接触问题版本：成功。
        result = run_cli(["list", SURROGATE_LOCK, "--unreachable"])
        self.assertEqual(assert_success_doc(self, result), [])

    def test_function_results_pass_surrogate_through(self):
        # 函数层不做输出编码：代理码点原样出现在返回结构中。
        root_deps, packages_map = load_lockfile(SURROGATE_LOCK)
        items = list_items(root_deps, packages_map)
        self.assertEqual(items[1]["version"], "2.0-\ud83f")
        self.assertEqual(direct_items(root_deps, packages_map), DEMO_DIRECT)
        self.assertEqual(unreachable_items(root_deps, packages_map), [])


if __name__ == "__main__":
    unittest.main()
