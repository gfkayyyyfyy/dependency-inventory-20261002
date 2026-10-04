"""ancestors 全部上游查询的回归测试：API、CLI 与错误语义。

样例为临时目录内独立创建的合法 package-lock.json v3 平铺结构：
- 根节点声明 zeta、alpha、loopy；
- zeta 经 beta 到达 leaf；alpha 同时声明 delta、charlie、leaf；
  delta、charlie、@scope/pkg 也都直接声明 leaf（多个直接上游）；
  charlie 还声明 alpha，构成 alpha<->charlie 循环；
- loopy 声明自身（可达自环）；orphan 不被根节点引用，声明 solo 与
  自身（不可达自环）；名为 "$root" 的普通包也声明 solo。

全程离线、仅用标准库，不修改仓库自带样例，不安装或执行锁文件中的依赖。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import NotFoundError, find_ancestors, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_LOCK = os.path.join(REPO_ROOT, "sample-lock.json")
BAD_UTF8 = os.path.join(REPO_ROOT, "bad-utf8.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def graph_lock_data():
    """多层祖先 + 循环 + 根不可达节点 + 字面 $root 包 + scoped 包。

    依赖声明顺序刻意与排序结果不同，证明 ancestors 不取决于声明顺序。
    """
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {
                "dependencies": {
                    "zeta": "1.0.0",
                    "alpha": "1.0.0",
                    "loopy": "1.0.0",
                }
            },
            "node_modules/zeta": {
                "version": "1.0.0",
                "dependencies": {"beta": "1.0.0"},
            },
            "node_modules/beta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/alpha": {
                "version": "1.0.0",
                "dependencies": {
                    "delta": "1.0.0",
                    "charlie": "1.0.0",
                    "leaf": "1.0.0",
                },
            },
            "node_modules/delta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/charlie": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0", "alpha": "1.0.0"},
            },
            "node_modules/leaf": {"version": "1.0.0"},
            "node_modules/loopy": {
                "version": "1.0.0",
                "dependencies": {"loopy": "1.0.0"},
            },
            "node_modules/orphan": {
                "version": "1.0.0",
                "dependencies": {"solo": "1.0.0", "orphan": "1.0.0"},
            },
            "node_modules/solo": {"version": "1.0.0"},
            "node_modules/@scope/pkg": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/$root": {
                "version": "1.0.0",
                "dependencies": {"solo": "1.0.0"},
            },
        },
    }


def with_reversed_key_order(value):
    """递归交换所有对象的键书写顺序，关系（图本身）保持不变。"""
    if isinstance(value, dict):
        return {
            key: with_reversed_key_order(item)
            for key, item in reversed(list(value.items()))
        }
    return value


class AncestorsAcceptance(unittest.TestCase):
    """任务给定的两条验收命令。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_sample_leaf_ancestors(self):
        result = self.run_cli(["ancestors", SAMPLE_LOCK, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "leaf", "ancestors": ["alpha", "beta", "orphan"]},
        )

    def test_sample_isolated_has_no_ancestors(self):
        result = self.run_cli(["ancestors", SAMPLE_LOCK, "isolated"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "isolated", "ancestors": []},
        )


class AncestorsApi(unittest.TestCase):
    """find_ancestors 的函数级语义。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, graph_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.lock_path)

    def tearDown(self):
        self._tmp.cleanup()

    def ancestors_of(self, target):
        return find_ancestors(self.packages_map, target)

    def test_transitive_ancestors_sorted_by_unicode_code_point(self):
        # @(0x40) < a < b < c < d < z：直接上游与更远的祖先都进入结果，
        # 根项目不进入；目标自身无自环，本就不在图中回到自身。
        self.assertEqual(
            self.ancestors_of("leaf"),
            ["@scope/pkg", "alpha", "beta", "charlie", "delta", "zeta"],
        )

    def test_cycle_members_that_reach_target_are_kept(self):
        # alpha<->charlie 循环：两者都能到达 leaf，互相也都能到达对方。
        self.assertEqual(self.ancestors_of("alpha"), ["charlie"])
        self.assertEqual(
            self.ancestors_of("charlie"),
            ["alpha"],
        )

    def test_target_excluded_even_with_self_loop(self):
        # loopy 声明自身：目标自身始终排除，结果为空。
        self.assertEqual(self.ancestors_of("loopy"), [])
        # orphan 声明自身且不可达：同样排除自身，结果为空。
        self.assertEqual(self.ancestors_of("orphan"), [])

    def test_unreachable_packages_are_still_ancestors(self):
        # orphan、$root 均从根不可达，仍声明 solo，照常进入结果。
        # $(0x24) < o(0x6f)：字面 $root 包排在 orphan 前。
        self.assertEqual(self.ancestors_of("solo"), ["$root", "orphan"])

    def test_no_upstream_returns_empty_list(self):
        self.assertEqual(self.ancestors_of("zeta"), [])
        self.assertEqual(self.ancestors_of("@scope/pkg"), [])

    def test_literal_root_target_is_normal_package(self):
        # 查询名为 $root 的已安装包：没有包声明它。
        self.assertEqual(self.ancestors_of("$root"), [])

    def test_case_sensitive_full_name_only(self):
        with self.assertRaises(NotFoundError):
            self.ancestors_of("LEAF")
        with self.assertRaises(NotFoundError):
            self.ancestors_of("Alpha")

    def test_missing_target_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.ancestors_of("ghost")

    def test_declaration_order_swap_keeps_result_identical(self):
        reordered = with_reversed_key_order(graph_lock_data())
        reordered_path = write_lockfile(self.tmp, reordered, "reordered.json")
        _, rev_map = load_lockfile(reordered_path)
        self.assertEqual(
            find_ancestors(rev_map, "leaf"),
            ["@scope/pkg", "alpha", "beta", "charlie", "delta", "zeta"],
        )
        self.assertEqual(find_ancestors(rev_map, "solo"), ["$root", "orphan"])

    def test_does_not_mutate_loaded_data(self):
        snapshot = {
            name: list(info["deps"]) for name, info in self.packages_map.items()
        }
        find_ancestors(self.packages_map, "leaf")
        find_ancestors(self.packages_map, "orphan")
        self.assertEqual(
            {name: list(info["deps"]) for name, info in self.packages_map.items()},
            snapshot,
        )


class AncestorsCli(unittest.TestCase):
    """ancestors 命令行输出形状、退出码与流约定。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, graph_lock_data())

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assertAncestorsObject(self, stdout, name, ancestors):
        self.assertTrue(stdout.endswith("\n"))
        self.assertEqual(stdout.count("\n"), 1)
        parsed = json.loads(stdout)
        self.assertEqual(set(parsed), {"name", "ancestors"})
        self.assertEqual(parsed, {"name": name, "ancestors": ancestors})

    def test_multi_ancestor_object(self):
        result = self.run_cli(["ancestors", self.lock_path, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(
            result.stdout,
            "leaf",
            ["@scope/pkg", "alpha", "beta", "charlie", "delta", "zeta"],
        )

    def test_empty_ancestors_object(self):
        result = self.run_cli(["ancestors", self.lock_path, "orphan"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "orphan", [])

    def test_name_echoed_verbatim(self):
        for target in ("@scope/pkg", "$root", "leaf"):
            result = self.run_cli(["ancestors", self.lock_path, target])
            self.assertEqual(json.loads(result.stdout)["name"], target)

    def test_missing_target_not_found(self):
        result = self.run_cli(["ancestors", self.lock_path, "ghost"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_case_mismatch_not_found(self):
        result = self.run_cli(["ancestors", self.lock_path, "LEAF"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")


class AncestorsInputErrors(unittest.TestCase):
    """输入错误优先于未安装查询包：整份锁文件先校验。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assertInputError(self, argv):
        result = self.run_cli(argv)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_invalid_input_beats_missing_query_target(self):
        # 悬空依赖使整份文件无效；即使查询目标未安装也以 INPUT_ERROR 为准。
        data = graph_lock_data()
        data["packages"]["node_modules/solo"]["dependencies"] = {"missing": "1.0.0"}
        path = write_lockfile(self.tmp, data, "dangling.json")
        self.assertInputError(["ancestors", path, "ghost"])
        # 查询目标恰好是已安装包同样失败。
        self.assertInputError(["ancestors", path, "leaf"])

    def test_structure_error_in_unreachable_entry_rejects_whole_file(self):
        # broken 不被任何节点引用，其结构错误仍令整份输入失败。
        data = graph_lock_data()
        data["packages"]["node_modules/broken"] = {"version": "1.0.0", "dependencies": None}
        path = write_lockfile(self.tmp, data, "broken-unreachable.json")
        self.assertInputError(["ancestors", path, "leaf"])

    def test_corrupt_json_is_input_error(self):
        path = os.path.join(self.tmp, "corrupt.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"lockfileVersion": 3, "packages": {')
        self.assertInputError(["ancestors", path, "ghost"])

    def test_unreadable_file_is_input_error(self):
        self.assertInputError(
            ["ancestors", os.path.join(self.tmp, "missing.json"), "x"]
        )

    def test_bad_utf8_is_input_error_even_for_missing_target(self):
        result = self.run_cli(["ancestors", BAD_UTF8, "ghost"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")


class ExistingCommandsUnchanged(unittest.TestCase):
    """ancestors 接入后 list/why/parents 结果保持不变（冒烟）。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_sample_why_unchanged(self):
        result = self.run_cli(["why", SAMPLE_LOCK, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "leaf", "path": ["$root", "alpha", "beta", "leaf"]},
        )

    def test_sample_parents_unchanged(self):
        result = self.run_cli(["parents", SAMPLE_LOCK, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "leaf", "direct": False, "parents": ["beta", "orphan"]},
        )


if __name__ == "__main__":
    unittest.main()
