"""parents 直接上游查询的回归测试：API、CLI 与错误语义。

样例为临时目录内独立创建的合法 package-lock.json v3 平铺结构：
- 根节点声明 zeta、alpha、loopy；
- zeta 经 beta 到达 leaf；alpha 同时声明 delta、charlie、leaf；
  delta、charlie、@scope/pkg 也都直接声明 leaf（多个直接上游）；
  charlie 还声明 alpha，构成 alpha<->charlie 循环；
- loopy 声明自身（可达自环）；orphan 不被根节点引用，声明 solo 与
  自身（不可达自环）；名为 "$root" 的普通包也声明 solo。

全程离线、仅用标准库，不修改 demo-lock.json，不安装或执行锁文件中的依赖。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import NotFoundError, find_parents, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")
BAD_UTF8 = os.path.join(REPO_ROOT, "bad-utf8.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def graph_lock_data():
    """多直接上游 + 循环 + 根不可达自环节点 + 字面 $root 包 + scoped 包。

    依赖声明顺序刻意与排序结果不同，证明 parents 不取决于声明顺序。
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


class ParentsAcceptance(unittest.TestCase):
    """任务给定的两条验收命令。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_demo_beta_has_alpha_parent(self):
        result = self.run_cli(["parents", DEMO_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, '{"name": "beta", "direct": false, "parents": ["alpha"]}\n')

    def test_new_beta_is_direct_without_parents(self):
        result = self.run_cli(["parents", NEW_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # gamma 已安装但不声明 beta，不进入 parents。
        self.assertEqual(
            result.stdout,
            '{"name": "beta", "direct": true, "parents": []}\n',
        )

    def test_new_gamma_installed_but_has_no_upstream(self):
        result = self.run_cli(["parents", NEW_LOCK, "gamma"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "gamma", "direct": False, "parents": []},
        )


class ParentsApi(unittest.TestCase):
    """find_parents 的函数级语义。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, graph_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.lock_path)

    def tearDown(self):
        self._tmp.cleanup()

    def parents_of(self, target):
        return find_parents(self.root_deps, self.packages_map, target)

    def test_all_direct_upstreams_sorted_by_unicode_code_point(self):
        # @(0x40) < a < b < c < d：scoped 名排最前，结果与声明顺序无关。
        self.assertEqual(
            self.parents_of("leaf"),
            {
                "direct": False,
                "parents": ["@scope/pkg", "alpha", "beta", "charlie", "delta"],
            },
        )

    def test_direct_true_and_parents_coexist(self):
        # 根直接声明 alpha，charlie 又在循环边上声明 alpha：两者同时体现。
        self.assertEqual(
            self.parents_of("alpha"),
            {"direct": True, "parents": ["charlie"]},
        )

    def test_root_only_declaration_does_not_add_root_member(self):
        # 仅根节点声明 zeta：direct 为真，根项目不进入 parents。
        self.assertEqual(
            self.parents_of("zeta"),
            {"direct": True, "parents": []},
        )
        self.assertEqual(
            self.parents_of("loopy"),
            {"direct": True, "parents": ["loopy"]},
        )

    def test_only_direct_edges_no_transitive_ancestors(self):
        # beta 的唯一直接上游是 zeta；不把 zeta 的上游（根）或更远祖先列出。
        self.assertEqual(
            self.parents_of("beta"),
            {"direct": False, "parents": ["zeta"]},
        )

    def test_unreachable_packages_are_still_parents(self):
        # orphan、$root 均从根不可达，仍直接声明 solo，照常进入结果。
        # $(0x24) < o(0x6f)：字面 $root 包排在 orphan 前。
        self.assertEqual(
            self.parents_of("solo"),
            {"direct": False, "parents": ["$root", "orphan"]},
        )

    def test_self_loop_on_unreachable_package_keeps_itself(self):
        # orphan 声明自身：保留目标自身，即使它从根不可达。
        self.assertEqual(
            self.parents_of("orphan"),
            {"direct": False, "parents": ["orphan"]},
        )

    def test_scoped_target_matched_as_full_name(self):
        self.assertEqual(
            self.parents_of("@scope/pkg"),
            {"direct": False, "parents": []},
        )

    def test_case_sensitive_full_name_only(self):
        with self.assertRaises(NotFoundError):
            self.parents_of("LEAF")
        with self.assertRaises(NotFoundError):
            self.parents_of("Alpha")

    def test_missing_target_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.parents_of("ghost")

    def test_declaration_order_swap_keeps_result_identical(self):
        reordered = with_reversed_key_order(graph_lock_data())
        reordered_path = write_lockfile(self.tmp, reordered, "reordered.json")
        rev_root_deps, rev_map = load_lockfile(reordered_path)
        self.assertEqual(
            find_parents(rev_root_deps, rev_map, "leaf"),
            {
                "direct": False,
                "parents": ["@scope/pkg", "alpha", "beta", "charlie", "delta"],
            },
        )
        self.assertEqual(
            find_parents(rev_root_deps, rev_map, "solo"),
            {"direct": False, "parents": ["$root", "orphan"]},
        )

    def test_does_not_mutate_loaded_data(self):
        snapshot = {
            name: list(info["deps"]) for name, info in self.packages_map.items()
        }
        root_snapshot = list(self.root_deps)
        find_parents(self.root_deps, self.packages_map, "leaf")
        find_parents(self.root_deps, self.packages_map, "orphan")
        self.assertEqual(self.root_deps, root_snapshot)
        self.assertEqual(
            {name: list(info["deps"]) for name, info in self.packages_map.items()},
            snapshot,
        )


class ParentsCli(unittest.TestCase):
    """parents 命令行输出形状、退出码与流约定。"""

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

    def assertParentsObject(self, stdout, name, direct, parents):
        self.assertTrue(stdout.endswith("\n"))
        self.assertEqual(stdout.count("\n"), 1)
        parsed = json.loads(stdout)
        self.assertEqual(set(parsed), {"name", "direct", "parents"})
        self.assertEqual(
            parsed, {"name": name, "direct": direct, "parents": parents}
        )

    def test_multi_parent_object(self):
        result = self.run_cli(["parents", self.lock_path, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(
            result.stdout,
            "leaf",
            False,
            ["@scope/pkg", "alpha", "beta", "charlie", "delta"],
        )

    def test_direct_with_parents(self):
        result = self.run_cli(["parents", self.lock_path, "alpha"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(result.stdout, "alpha", True, ["charlie"])

    def test_unreachable_self_loop_via_cli(self):
        result = self.run_cli(["parents", self.lock_path, "orphan"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(result.stdout, "orphan", False, ["orphan"])

    def test_literal_root_target_is_normal_package(self):
        # 查询名为 $root 的已安装包：没有包声明它，direct 看根 dependencies。
        result = self.run_cli(["parents", self.lock_path, "$root"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(result.stdout, "$root", False, [])

    def test_name_echoed_verbatim(self):
        for target in ("@scope/pkg", "$root", "leaf"):
            result = self.run_cli(["parents", self.lock_path, target])
            self.assertEqual(json.loads(result.stdout)["name"], target)

    def test_missing_target_not_found(self):
        result = self.run_cli(["parents", self.lock_path, "ghost"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_case_mismatch_not_found(self):
        result = self.run_cli(["parents", self.lock_path, "LEAF"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")


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
        self.assertInputError(["parents", path, "ghost"])
        # 查询目标恰好是已安装包同样失败。
        self.assertInputError(["parents", path, "leaf"])

    def test_structure_error_in_unreachable_entry_rejects_whole_file(self):
        # broken 不被任何节点引用，其结构错误仍令整份输入失败。
        data = graph_lock_data()
        data["packages"]["node_modules/broken"] = {"version": "1.0.0", "dependencies": None}
        path = write_lockfile(self.tmp, data, "broken-unreachable.json")
        self.assertInputError(["parents", path, "leaf"])

    def test_nested_path_and_link_still_rejected(self):
        nested = graph_lock_data()
        nested["packages"]["node_modules/alpha/node_modules/nested"] = {
            "version": "1.0.0"
        }
        nested_path = write_lockfile(self.tmp, nested, "nested.json")
        self.assertInputError(["parents", nested_path, "leaf"])

        linked = graph_lock_data()
        linked["packages"]["node_modules/linked"] = {"version": "1.0.0", "link": True}
        linked_path = write_lockfile(self.tmp, linked, "link.json")
        self.assertInputError(["parents", linked_path, "leaf"])

    def test_corrupt_json_is_input_error(self):
        path = os.path.join(self.tmp, "corrupt.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"lockfileVersion": 3, "packages": {')
        self.assertInputError(["parents", path, "ghost"])

    def test_unreadable_file_is_input_error(self):
        self.assertInputError(
            ["parents", os.path.join(self.tmp, "missing.json"), "x"]
        )

    def test_bad_utf8_is_input_error_even_for_missing_target(self):
        result = self.run_cli(["parents", BAD_UTF8, "ghost"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")


class ExistingCommandsUnchanged(unittest.TestCase):
    """parents 接入后 list/why/diff/sbom 结果保持不变（冒烟）。"""

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

    def test_demo_list_unchanged(self):
        result = self.run_cli(["list", DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )


if __name__ == "__main__":
    unittest.main()
