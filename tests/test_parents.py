"""parents 直接上游查询的回归测试：parents 集合、direct 标记与错误语义。

样例为临时目录内独立创建的合法 package-lock.json v3 平铺结构：
- 所有包版本均为 1.0.0，依赖声明均为字符串；
- 根节点声明 zeta、alpha；alpha 声明 delta、charlie；
  delta 与 charlie 都依赖 leaf；zeta 经 beta 也到达 leaf；
- charlie 还依赖 alpha，构成可达循环；orphan 不被根节点引用，
  依赖自身与 solo；另有名为 "$root" 的普通包与 @scope/pkg；
- leaf 还被根不可达的 orphan 之外的多个包声明，用于验证排序与去重。

全程离线、仅用标准库，不修改 demo-lock.json，不安装或执行锁文件中的依赖。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import NotFoundError, load_lockfile, parents_of

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def graph_lock_data():
    """多上游 + 循环 + 根不可达父包 + 字面 $root 包 + scoped 包。

    依赖声明顺序刻意与字典序相反，证明 parents 结果不取决于声明顺序。
    """
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"zeta": "1.0.0", "alpha": "1.0.0"}},
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
                "dependencies": {"delta": "1.0.0", "charlie": "1.0.0"},
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


class ParentsQuery(unittest.TestCase):
    """parents 集合的构成、排序、去重与 direct 标记。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, graph_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.lock_path)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assertParentsObject(self, stdout, name, direct, parents):
        self.assertTrue(stdout.endswith("\n"))
        parsed = json.loads(stdout)
        self.assertEqual(set(parsed), {"name", "direct", "parents"})
        self.assertEqual(
            parsed, {"name": name, "direct": direct, "parents": parents}
        )

    def test_demo_acceptance_examples(self):
        # 任务给定的验收样例。
        result = self.run_cli(["parents", DEMO_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(result.stdout, "beta", False, ["alpha"])

        result = self.run_cli(["parents", NEW_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # gamma 未被任何 dependencies 声明，不进入结果。
        self.assertParentsObject(result.stdout, "beta", True, [])

    def test_multiple_parents_sorted_and_deduplicated(self):
        # leaf 被 beta、delta、charlie、@scope/pkg 声明：按 Unicode 码点
        # 升序（@ 排在字母前），每个父包只出现一次。
        self.assertEqual(
            parents_of(self.root_deps, self.packages_map, "leaf"),
            {
                "name": "leaf",
                "direct": False,
                "parents": ["@scope/pkg", "beta", "charlie", "delta"],
            },
        )
        result = self.run_cli(["parents", self.lock_path, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(
            result.stdout, "leaf", False, ["@scope/pkg", "beta", "charlie", "delta"]
        )

    def test_direct_marks_root_declaration_only(self):
        # alpha 被根声明也被 charlie 声明：direct 为 True，charlie 是父包，
        # 根项目本身不进入 parents。
        self.assertEqual(
            parents_of(self.root_deps, self.packages_map, "alpha"),
            {"name": "alpha", "direct": True, "parents": ["charlie"]},
        )
        # zeta 只被根声明：direct 为 True，parents 为空。
        self.assertEqual(
            parents_of(self.root_deps, self.packages_map, "zeta"),
            {"name": "zeta", "direct": True, "parents": []},
        )

    def test_unreachable_parent_and_target_included(self):
        # orphan 根不可达，但它声明了 solo，仍是 solo 的父包；
        # 字面值 $root 是普通已安装包，同样计入。
        self.assertEqual(
            parents_of(self.root_deps, self.packages_map, "solo"),
            {"name": "solo", "direct": False, "parents": ["$root", "orphan"]},
        )
        # 根不可达的目标自身也可查询。
        self.assertEqual(
            parents_of(self.root_deps, self.packages_map, "orphan"),
            {"name": "orphan", "direct": False, "parents": ["orphan"]},
        )

    def test_self_loop_keeps_target_cycle_terminates(self):
        # orphan 自环：自身出现在 parents 中，查询正常结束。
        result = self.run_cli(["parents", self.lock_path, "orphan"])
        self.assertEqual(result.returncode, 0)
        self.assertParentsObject(result.stdout, "orphan", False, ["orphan"])
        # 循环 alpha -> charlie -> alpha：只返回直接声明者，不追溯更远祖先。
        result = self.run_cli(["parents", self.lock_path, "charlie"])
        self.assertEqual(result.returncode, 0)
        self.assertParentsObject(result.stdout, "charlie", False, ["alpha"])

    def test_declaration_order_swap_keeps_result_identical(self):
        reordered = with_reversed_key_order(graph_lock_data())
        reordered_path = write_lockfile(self.tmp, reordered, "reordered.json")
        rev_root_deps, rev_map = load_lockfile(reordered_path)
        self.assertEqual(rev_map["alpha"]["deps"], ["charlie", "delta"])
        self.assertEqual(
            parents_of(rev_root_deps, rev_map, "leaf"),
            {
                "name": "leaf",
                "direct": False,
                "parents": ["@scope/pkg", "beta", "charlie", "delta"],
            },
        )

    def test_scoped_and_literal_root_names(self):
        self.assertEqual(
            parents_of(self.root_deps, self.packages_map, "@scope/pkg"),
            {"name": "@scope/pkg", "direct": False, "parents": []},
        )
        # 查询字面值 $root：普通已安装包，无上游。
        self.assertEqual(
            parents_of(self.root_deps, self.packages_map, "$root"),
            {"name": "$root", "direct": False, "parents": []},
        )
        result = self.run_cli(["parents", self.lock_path, "@scope/pkg"])
        self.assertEqual(result.returncode, 0)
        self.assertParentsObject(result.stdout, "@scope/pkg", False, [])

    def test_case_sensitive_full_name_only(self):
        with self.assertRaises(NotFoundError):
            parents_of(self.root_deps, self.packages_map, "LEAF")
        result = self.run_cli(["parents", self.lock_path, "LEAF"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_missing_target_not_found(self):
        with self.assertRaises(NotFoundError):
            parents_of(self.root_deps, self.packages_map, "ghost")
        result = self.run_cli(["parents", self.lock_path, "ghost"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_parents_does_not_mutate_loaded_data(self):
        deps_before = list(self.packages_map["alpha"]["deps"])
        parents_of(self.root_deps, self.packages_map, "leaf")
        parents_of(self.root_deps, self.packages_map, "orphan")
        self.assertEqual(self.packages_map["alpha"]["deps"], deps_before)


class ParentsInputErrors(unittest.TestCase):
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

    def test_invalid_input_beats_missing_target(self):
        # 悬空依赖使整份文件无效；即使目标未安装，也以 INPUT_ERROR 为准。
        data = graph_lock_data()
        data["packages"]["node_modules/solo"]["dependencies"] = {"missing": "1.0.0"}
        path = write_lockfile(self.tmp, data, "dangling.json")
        result = self.run_cli(["parents", path, "ghost"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_unreachable_entry_structure_error_fails(self):
        # 结构错误位于根不可达的包条目中，同样整份失败。
        data = graph_lock_data()
        data["packages"]["node_modules/orphan"]["version"] = ""
        path = write_lockfile(self.tmp, data, "badversion.json")
        result = self.run_cli(["parents", path, "leaf"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_nested_path_and_link_still_rejected(self):
        nested = graph_lock_data()
        nested["packages"]["node_modules/alpha/node_modules/nested"] = {
            "version": "1.0.0"
        }
        nested_path = write_lockfile(self.tmp, nested, "nested.json")
        result = self.run_cli(["parents", nested_path, "leaf"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

        linked = graph_lock_data()
        linked["packages"]["node_modules/linked"] = {
            "version": "1.0.0",
            "link": True,
        }
        linked_path = write_lockfile(self.tmp, linked, "link.json")
        result = self.run_cli(["parents", linked_path, "leaf"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_unreadable_and_broken_files_are_input_error(self):
        result = self.run_cli(
            ["parents", os.path.join(self.tmp, "missing.json"), "x"]
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        result = self.run_cli(["parents", broken, "x"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
