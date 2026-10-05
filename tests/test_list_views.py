"""list 四种清单（完整/直接/可达/不可达）共享条目结构的回归测试（仅标准库、离线）。

四个清单生成函数 list_items、direct_items、reachable_items、
unreachable_items 共同维护同一份条目输出规则（每项仅含 name、version、
direct，按完整包名 Unicode 码点升序，direct 只表示根 dependencies 是否
直接声明）。本文件在重构后钉住这些既有行为：

- demo-lock.json 验收：完整与可达清单依次为 alpha@1.0.0（direct true）、
  beta@2.0.0（direct false）；直接清单只有 alpha；不可达清单为 []；
- --direct 与 --reachable 合用不改变直接清单，与 --unreachable 合用
  返回 []；--reachable 与 --unreachable 同时出现在读取文件前失败，
  即使附加 --direct 也一样；
- 根 dependencies 省略或为空时：直接与可达清单为空，不可达清单等于
  完整清单；共享依赖只出现一次，自环与循环正常结束，与根断开的循环
  仍不可达；
- 互斥冲突、文件不可读、JSON 或结构校验失败均退出码 2、stdout 为空、
  stderr 仅 "INPUT_ERROR\\n"；成功时退出码 0、stderr 为空、stdout 为
  单个 JSON 文档加末尾一个换行；
- 实际输出含孤立代理码点时同样按输入错误处理，被筛选排除的版本字符串
  不影响成功结果；
- 四种函数结果与对应命令行输出逐项一致；调用不修改 root_deps、
  packages_map 及其内的 deps 列表。
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

DEMO_FULL = [
    {"name": "alpha", "version": "1.0.0", "direct": True},
    {"name": "beta", "version": "2.0.0", "direct": False},
]
DEMO_DIRECT = [
    {"name": "alpha", "version": "1.0.0", "direct": True},
]


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def write_text(directory, text, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    return path


def assert_input_error(test, result):
    test.assertEqual(result.returncode, 2)
    test.assertEqual(result.stdout, "")
    test.assertEqual(result.stderr, "INPUT_ERROR\n")


def assert_success_doc(test, result):
    test.assertEqual(result.returncode, 0, result.stderr)
    test.assertEqual(result.stderr, "")
    # 单个 JSON 文档加末尾一个换行。
    test.assertTrue(result.stdout.endswith("\n"))
    test.assertEqual(result.stdout.count("\n"), 1)
    return json.loads(result.stdout)


class DemoLockAcceptance(unittest.TestCase):
    """demo-lock.json：四种函数结果与四种命令行输出逐项一致。"""

    def test_function_results(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        self.assertEqual(list_items(root_deps, packages_map), DEMO_FULL)
        self.assertEqual(direct_items(root_deps, packages_map), DEMO_DIRECT)
        self.assertEqual(reachable_items(root_deps, packages_map), DEMO_FULL)
        self.assertEqual(unreachable_items(root_deps, packages_map), [])

    def test_cli_outputs_match_function_results(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        cases = [
            (["list", DEMO_LOCK], list_items(root_deps, packages_map)),
            (["list", DEMO_LOCK, "--direct"], direct_items(root_deps, packages_map)),
            (
                ["list", DEMO_LOCK, "--reachable"],
                reachable_items(root_deps, packages_map),
            ),
            (
                ["list", DEMO_LOCK, "--unreachable"],
                unreachable_items(root_deps, packages_map),
            ),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                result = run_cli(argv)
                doc = assert_success_doc(self, result)
                self.assertEqual(doc, expected)
                # 输出为单个 JSON 文档加末尾换行的精确文本。
                self.assertEqual(
                    result.stdout,
                    json.dumps(expected, ensure_ascii=False) + "\n",
                )

    def test_items_have_exact_keys_and_versions_preserved(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        for items in (
            list_items(root_deps, packages_map),
            direct_items(root_deps, packages_map),
            reachable_items(root_deps, packages_map),
            unreachable_items(root_deps, packages_map),
        ):
            for item in items:
                self.assertEqual(set(item.keys()), {"name", "version", "direct"})
                self.assertIsInstance(item["direct"], bool)
                # 版本字符串原样保留，与安装条目一致。
                self.assertEqual(
                    item["version"], packages_map[item["name"]]["version"]
                )

    def test_calls_do_not_mutate_inputs(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        snapshot_deps = copy.deepcopy(root_deps)
        snapshot_map = copy.deepcopy(packages_map)
        list_items(root_deps, packages_map)
        direct_items(root_deps, packages_map)
        reachable_items(root_deps, packages_map)
        unreachable_items(root_deps, packages_map)
        self.assertEqual(root_deps, snapshot_deps)
        self.assertEqual(packages_map, snapshot_map)


class DirectFilterCombinations(unittest.TestCase):
    """--direct 与可达性筛选的组合及互斥冲突。"""

    def test_direct_with_reachable_keeps_direct_result(self):
        for argv in (
            ["list", DEMO_LOCK, "--direct"],
            ["list", DEMO_LOCK, "--direct", "--reachable"],
            ["list", DEMO_LOCK, "--reachable", "--direct"],
        ):
            with self.subTest(argv=argv):
                doc = assert_success_doc(self, run_cli(argv))
                self.assertEqual(doc, DEMO_DIRECT)

    def test_direct_with_unreachable_is_empty(self):
        result = run_cli(["list", DEMO_LOCK, "--direct", "--unreachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    def test_reachable_unreachable_conflict_fails_before_reading(self):
        # 路径不存在也按输入错误拒绝：冲突判定发生在读取文件之前，
        # 附加 --direct 不改变该行为。
        missing = os.path.join(REPO_ROOT, "no-such-lockfile.json")
        for extra in ([], ["--direct"]):
            argv = ["list", missing, "--reachable", "--unreachable", *extra]
            with self.subTest(argv=argv):
                assert_input_error(self, run_cli(argv))


class EmptyRootDependencies(unittest.TestCase):
    """根 dependencies 省略或为空：直接/可达为空，不可达等于完整清单。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def cycle_data(self, root_node):
        # orphan↔zeta 是与根断开的合法循环；根无边时全部不可达。
        return {
            "lockfileVersion": 3,
            "packages": {
                "": root_node,
                "node_modules/orphan": {
                    "version": "1.0.0",
                    "dependencies": {"zeta": "*"},
                },
                "node_modules/zeta": {
                    "version": "1.0.0",
                    "dependencies": {"orphan": "*"},
                },
            },
        }

    def check_empty_root(self, path):
        root_deps, packages_map = load_lockfile(path)
        full = list_items(root_deps, packages_map)
        self.assertEqual(
            full,
            [
                {"name": "orphan", "version": "1.0.0", "direct": False},
                {"name": "zeta", "version": "1.0.0", "direct": False},
            ],
        )
        self.assertEqual(direct_items(root_deps, packages_map), [])
        self.assertEqual(reachable_items(root_deps, packages_map), [])
        self.assertEqual(unreachable_items(root_deps, packages_map), full)

        self.assertEqual(assert_success_doc(self, run_cli(["list", path])), full)
        self.assertEqual(
            assert_success_doc(self, run_cli(["list", path, "--direct"])), []
        )
        self.assertEqual(
            assert_success_doc(self, run_cli(["list", path, "--reachable"])), []
        )
        self.assertEqual(
            assert_success_doc(self, run_cli(["list", path, "--unreachable"])),
            full,
        )

    def test_root_dependencies_omitted(self):
        self.check_empty_root(write_lockfile(self.tmp, self.cycle_data({})))

    def test_root_dependencies_empty_object(self):
        self.check_empty_root(
            write_lockfile(
                self.tmp, self.cycle_data({"dependencies": {}}), "empty.json"
            )
        )


class SharedAndCyclicGraphs(unittest.TestCase):
    """共享依赖只出现一次，自环/循环正常结束，断开的循环仍不可达。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_shared_dependency_and_cycles(self):
        # 根声明 alpha 与 @scope/tool，两者都依赖 beta（共享）；
        # beta 自环且与 alpha 成环；orphan↔zeta 与根断开。
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "*", "@scope/tool": "*"}},
                "node_modules/alpha": {
                    "version": "1.0.0",
                    "dependencies": {"beta": "*"},
                },
                "node_modules/@scope/tool": {
                    "version": "1.0.0",
                    "dependencies": {"beta": "*"},
                },
                "node_modules/beta": {
                    "version": "2.0.0",
                    "dependencies": {"alpha": "*", "beta": "*"},
                },
                "node_modules/orphan": {
                    "version": "1.0.0",
                    "dependencies": {"zeta": "*"},
                },
                "node_modules/zeta": {
                    "version": "1.0.0",
                    "dependencies": {"orphan": "*"},
                },
            },
        }
        path = write_lockfile(self.tmp, data)
        root_deps, packages_map = load_lockfile(path)

        expected_reachable = [
            {"name": "@scope/tool", "version": "1.0.0", "direct": True},
            {"name": "alpha", "version": "1.0.0", "direct": True},
            {"name": "beta", "version": "2.0.0", "direct": False},
        ]
        expected_unreachable = [
            {"name": "orphan", "version": "1.0.0", "direct": False},
            {"name": "zeta", "version": "1.0.0", "direct": False},
        ]
        # 共享的 beta 在完整与可达清单中各只出现一次。
        self.assertEqual(
            list_items(root_deps, packages_map),
            expected_reachable + expected_unreachable,
        )
        self.assertEqual(
            reachable_items(root_deps, packages_map), expected_reachable
        )
        self.assertEqual(
            unreachable_items(root_deps, packages_map), expected_unreachable
        )
        self.assertEqual(
            direct_items(root_deps, packages_map),
            [
                {"name": "@scope/tool", "version": "1.0.0", "direct": True},
                {"name": "alpha", "version": "1.0.0", "direct": True},
            ],
        )
        # 与根断开的循环仍不可达，命令行结果与函数一致。
        self.assertEqual(
            assert_success_doc(self, run_cli(["list", path, "--unreachable"])),
            expected_unreachable,
        )


class FailureAndSurrogateBoundaries(unittest.TestCase):
    """错误出口与孤立代理码点的输出边界。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_unreadable_file_is_input_error(self):
        missing = os.path.join(self.tmp, "missing.json")
        for extra in ([], ["--direct"], ["--reachable"], ["--unreachable"]):
            with self.subTest(extra=extra):
                assert_input_error(self, run_cli(["list", missing, *extra]))

    def test_broken_json_is_input_error(self):
        path = write_text(self.tmp, "{not json", "broken.json")
        assert_input_error(self, run_cli(["list", path]))

    def test_structure_validation_failure_is_input_error(self):
        # 不可达条目的非法 dependencies 不被筛选掩盖：整份输入失败。
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "*"}},
                "node_modules/alpha": {"version": "1.0.0"},
                "node_modules/orphan": {"version": "1.0.0", "dependencies": None},
            },
        }
        path = write_lockfile(self.tmp, data)
        with self.assertRaises(InputError):
            load_lockfile(path)
        for extra in ([], ["--direct"], ["--reachable"], ["--unreachable"]):
            with self.subTest(extra=extra):
                assert_input_error(self, run_cli(["list", path, *extra]))

    def test_surrogate_in_output_is_input_error(self):
        # beta 可达且版本含孤立高代理：完整与可达清单的输出编码失败。
        text = (
            '{"lockfileVersion": 3, "packages": {'
            '"": {"dependencies": {"alpha": "1.0.0"}},'
            '"node_modules/alpha": {"version": "1.0.0",'
            ' "dependencies": {"beta": "2.0.0"}},'
            '"node_modules/beta": {"version": "2.0-\\ud83f"}'
            "}}"
        )
        path = write_text(self.tmp, text, "surrogate.json")
        assert_input_error(self, run_cli(["list", path]))
        assert_input_error(self, run_cli(["list", path, "--reachable"]))
        # 直接清单只含 alpha，被排除的 beta 版本不影响成功结果。
        doc = assert_success_doc(self, run_cli(["list", path, "--direct"]))
        self.assertEqual(doc, DEMO_DIRECT)
        # 不可达清单为空，同样成功。
        result = run_cli(["list", path, "--unreachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "[]\n")

    def test_surrogate_only_on_unreachable_package_filtered_out(self):
        # 孤立代理只出现在根不可达包的版本里：--reachable 与 --direct
        # 筛选后输出干净，成功；完整与不可达清单仍按输入错误处理。
        text = (
            '{"lockfileVersion": 3, "packages": {'
            '"": {"dependencies": {"alpha": "1.0.0"}},'
            '"node_modules/alpha": {"version": "1.0.0"},'
            '"node_modules/orphan": {"version": "9.0-\\ud83f"}'
            "}}"
        )
        path = write_text(self.tmp, text, "orphan-surrogate.json")
        assert_input_error(self, run_cli(["list", path]))
        assert_input_error(self, run_cli(["list", path, "--unreachable"]))
        doc = assert_success_doc(self, run_cli(["list", path, "--reachable"]))
        self.assertEqual(
            doc, [{"name": "alpha", "version": "1.0.0", "direct": True}]
        )
        doc = assert_success_doc(self, run_cli(["list", path, "--direct"]))
        self.assertEqual(doc, DEMO_DIRECT)


if __name__ == "__main__":
    unittest.main()
