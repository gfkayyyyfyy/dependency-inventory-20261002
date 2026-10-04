"""descendants 全部下游查询的回归测试：API、CLI 与错误语义。

样例为临时目录内独立创建的合法 package-lock.json v3 平铺结构：
- 根节点声明 zeta、alpha、loopy；
- zeta 经 beta 到达 leaf；alpha 同时声明 delta、charlie、leaf；
  delta、charlie、@scope/pkg 也都直接声明 leaf；charlie 还声明 alpha，
 构成 alpha<->charlie 循环；
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

from depinventory import NotFoundError, find_descendants, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_LOCK = os.path.join(REPO_ROOT, "sample-lock.json")
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
BAD_UTF8 = os.path.join(REPO_ROOT, "bad-utf8.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def graph_lock_data():
    """多下游链 + 循环 + 根不可达自环节点 + 字面 $root 包 + scoped 包。

    依赖声明顺序刻意与排序结果不同，证明 descendants 不取决于声明顺序。
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


class DescendantsAcceptance(unittest.TestCase):
    """任务给定的两条验收命令。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_sample_alpha_descendants(self):
        result = self.run_cli(["descendants", SAMPLE_LOCK, "alpha"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            '{"name": "alpha", "descendants": ["beta", "leaf"]}\n',
        )

    def test_sample_orphan_descendants(self):
        # orphan 从根不可达，仍沿自身 dependencies 展开：orphan -> leaf -> beta。
        result = self.run_cli(["descendants", SAMPLE_LOCK, "orphan"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            '{"name": "orphan", "descendants": ["beta", "leaf"]}\n',
        )


class DescendantsApi(unittest.TestCase):
    """find_descendants 的函数级语义。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, graph_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.lock_path)

    def tearDown(self):
        self._tmp.cleanup()

    def descendants_of(self, target):
        return find_descendants(self.root_deps, self.packages_map, target)

    def test_transitive_descendants_sorted_by_unicode_code_point(self):
        # alpha 直接声明 delta、charlie、leaf，并传递到达 beta 之外的全部下游。
        self.assertEqual(
            self.descendants_of("alpha"),
            ["charlie", "delta", "leaf"],
        )

    def test_cycle_members_are_kept_target_excluded(self):
        # alpha<->charlie 循环：互相进入对方结果，目标自身始终排除。
        self.assertEqual(
            self.descendants_of("charlie"), ["alpha", "delta", "leaf"]
        )
        self.assertNotIn("charlie", self.descendants_of("charlie"))

    def test_self_loop_does_not_add_target_itself(self):
        # loopy 声明自身：目标排除后无其他下游，结果为空的已安装目标仍成功。
        self.assertEqual(self.descendants_of("loopy"), [])

    def test_unreachable_start_still_expands_own_deps(self):
        # orphan 从根不可达，仍沿自身 dependencies 展开；自环不计入自身。
        self.assertEqual(self.descendants_of("orphan"), ["solo"])

    def test_literal_root_package_is_normal_package(self):
        # 字面值 $root 按普通已安装包查找，其下游照常展开。
        self.assertEqual(self.descendants_of("$root"), ["solo"])

    def test_no_dependencies_returns_empty(self):
        self.assertEqual(self.descendants_of("leaf"), [])
        self.assertEqual(self.descendants_of("solo"), [])

    def test_case_sensitive_full_name_only(self):
        with self.assertRaises(NotFoundError):
            self.descendants_of("LEAF")
        with self.assertRaises(NotFoundError):
            self.descendants_of("Alpha")

    def test_missing_target_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.descendants_of("ghost")

    def test_root_deps_absent_or_empty_does_not_change_result(self):
        # 根 dependencies 省略或为空不改变起点按自身依赖展开的规则。
        data = graph_lock_data()
        del data["packages"][""]["dependencies"]
        path = write_lockfile(self.tmp, data, "no-root-deps.json")
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, [])
        self.assertEqual(
            find_descendants(root_deps, packages_map, "alpha"),
            ["charlie", "delta", "leaf"],
        )

    def test_declaration_order_swap_keeps_result_identical(self):
        reordered = with_reversed_key_order(graph_lock_data())
        reordered_path = write_lockfile(self.tmp, reordered, "reordered.json")
        rev_root_deps, rev_map = load_lockfile(reordered_path)
        self.assertEqual(
            find_descendants(rev_root_deps, rev_map, "alpha"),
            ["charlie", "delta", "leaf"],
        )
        self.assertEqual(
            find_descendants(rev_root_deps, rev_map, "orphan"),
            ["solo"],
        )

    def test_does_not_mutate_loaded_data(self):
        snapshot = {
            name: list(info["deps"]) for name, info in self.packages_map.items()
        }
        root_snapshot = list(self.root_deps)
        find_descendants(self.root_deps, self.packages_map, "alpha")
        find_descendants(self.root_deps, self.packages_map, "orphan")
        self.assertEqual(self.root_deps, root_snapshot)
        self.assertEqual(
            {name: list(info["deps"]) for name, info in self.packages_map.items()},
            snapshot,
        )


class DescendantsCli(unittest.TestCase):
    """descendants 命令行输出形状、退出码与流约定。"""

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

    def assertDescendantsObject(self, stdout, name, descendants):
        self.assertTrue(stdout.endswith("\n"))
        self.assertEqual(stdout.count("\n"), 1)
        parsed = json.loads(stdout)
        self.assertEqual(set(parsed), {"name", "descendants"})
        self.assertEqual(parsed, {"name": name, "descendants": descendants})

    def test_multi_descendant_object(self):
        result = self.run_cli(["descendants", self.lock_path, "alpha"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertDescendantsObject(
            result.stdout, "alpha", ["charlie", "delta", "leaf"]
        )

    def test_empty_descendants_object(self):
        result = self.run_cli(["descendants", self.lock_path, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertDescendantsObject(result.stdout, "leaf", [])

    def test_literal_root_target_is_normal_package(self):
        result = self.run_cli(["descendants", self.lock_path, "$root"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertDescendantsObject(result.stdout, "$root", ["solo"])

    def test_name_echoed_verbatim(self):
        for target in ("@scope/pkg", "$root", "leaf"):
            result = self.run_cli(["descendants", self.lock_path, target])
            self.assertEqual(json.loads(result.stdout)["name"], target)

    def test_missing_target_not_found(self):
        result = self.run_cli(["descendants", self.lock_path, "ghost"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_case_mismatch_not_found(self):
        result = self.run_cli(["descendants", self.lock_path, "LEAF"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")


class DescendantsInputErrors(unittest.TestCase):
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
        self.assertInputError(["descendants", path, "ghost"])
        # 查询目标恰好是已安装包同样失败。
        self.assertInputError(["descendants", path, "leaf"])

    def test_structure_error_in_unreachable_entry_rejects_whole_file(self):
        # broken 不被任何节点引用，其结构错误仍令整份输入失败。
        data = graph_lock_data()
        data["packages"]["node_modules/broken"] = {"version": "1.0.0", "dependencies": None}
        path = write_lockfile(self.tmp, data, "broken-unreachable.json")
        self.assertInputError(["descendants", path, "leaf"])

    def test_nested_path_and_link_still_rejected(self):
        nested = graph_lock_data()
        nested["packages"]["node_modules/alpha/node_modules/nested"] = {
            "version": "1.0.0"
        }
        nested_path = write_lockfile(self.tmp, nested, "nested.json")
        self.assertInputError(["descendants", nested_path, "leaf"])

        linked = graph_lock_data()
        linked["packages"]["node_modules/linked"] = {"version": "1.0.0", "link": True}
        linked_path = write_lockfile(self.tmp, linked, "link.json")
        self.assertInputError(["descendants", linked_path, "leaf"])

    def test_corrupt_json_is_input_error(self):
        path = os.path.join(self.tmp, "corrupt.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"lockfileVersion": 3, "packages": {')
        self.assertInputError(["descendants", path, "ghost"])

    def test_unreadable_file_is_input_error(self):
        self.assertInputError(
            ["descendants", os.path.join(self.tmp, "missing.json"), "x"]
        )

    def test_bad_utf8_is_input_error_even_for_missing_target(self):
        result = self.run_cli(["descendants", BAD_UTF8, "ghost"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")


class ExistingCommandsUnchanged(unittest.TestCase):
    """descendants 接入后 list/why/parents/ancestors 结果保持不变（冒烟）。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_demo_why_unchanged(self):
        result = self.run_cli(["why", DEMO_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "beta", "path": ["$root", "alpha", "beta"]},
        )

    def test_sample_ancestors_unchanged(self):
        result = self.run_cli(["ancestors", SAMPLE_LOCK, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "leaf", "ancestors": ["alpha", "beta", "orphan"]},
        )


if __name__ == "__main__":
    unittest.main()
