"""why --from 起点扩展的回归测试：指定起点到目标的最短路径与错误语义。

样例为临时目录内独立创建的合法 package-lock.json v3 平铺结构：
- 所有包版本均为 1.0.0，依赖声明均为字符串；
- 根节点声明 zeta、alpha；alpha 声明 delta、charlie；
  delta 与 charlie 都依赖 leaf；zeta 经 beta 也到达 leaf；
- charlie 还依赖 alpha，构成可达循环；orphan 不被根节点引用，
  依赖自身与 solo；另有名为 "$root" 的普通包与 @scope/pkg。

全程离线、仅用标准库，不修改 demo-lock.json，不安装或执行锁文件中的依赖。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import NotFoundError, find_path, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def graph_lock_data():
    """等长路径 + 循环 + 根不可达自环节点 + 字面 $root 包 + scoped 包。

    依赖声明顺序刻意与字典序相反，证明 --from 结果也不取决于声明顺序。
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


class WhyFromPaths(unittest.TestCase):
    """--from 指定起点时的最短路径、字典序与循环行为。"""

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

    def assertWhyObject(self, stdout, name, path):
        self.assertTrue(stdout.endswith("\n"))
        parsed = json.loads(stdout)
        self.assertEqual(set(parsed), {"name", "path"})
        self.assertEqual(parsed, {"name": name, "path": path})

    def test_demo_acceptance_examples(self):
        # 任务给定的验收样例。
        result = self.run_cli(["why", DEMO_LOCK, "beta", "--from", "alpha"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertWhyObject(result.stdout, "beta", ["alpha", "beta"])

        result = self.run_cli(["why", DEMO_LOCK, "alpha", "--from", "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertWhyObject(result.stdout, "alpha", [])

    def test_shortest_path_with_lexicographic_tie_break_api(self):
        # alpha 到 leaf 同为 2 跳：charlie 与 delta 两条，取字典序小者。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "alpha"),
            ["alpha", "charlie", "leaf"],
        )
        # 位置参数与 source= 关键字等价。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", source="alpha"),
            ["alpha", "charlie", "leaf"],
        )
        # 直接边短于经中间节点的路径。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "charlie"),
            ["charlie", "leaf"],
        )
        # charlie -> alpha 在循环边上仍正常结束。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "alpha", "charlie"),
            ["charlie", "alpha"],
        )

    def test_shortest_path_with_lexicographic_tie_break_cli(self):
        result = self.run_cli(["why", self.lock_path, "leaf", "--from", "alpha"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertWhyObject(result.stdout, "leaf", ["alpha", "charlie", "leaf"])

    def test_declaration_order_swap_keeps_result_identical(self):
        reordered = with_reversed_key_order(graph_lock_data())
        reordered_path = write_lockfile(self.tmp, reordered, "reordered.json")
        rev_root_deps, rev_map = load_lockfile(reordered_path)
        self.assertEqual(rev_map["alpha"]["deps"], ["charlie", "delta"])
        self.assertEqual(
            find_path(rev_root_deps, rev_map, "leaf", "alpha"),
            ["alpha", "charlie", "leaf"],
        )
        result = self.run_cli(["why", reordered_path, "leaf", "--from", "alpha"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertWhyObject(result.stdout, "leaf", ["alpha", "charlie", "leaf"])

    def test_source_equals_target_needs_no_self_loop(self):
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "leaf"),
            ["leaf"],
        )
        # 即使起点有自环，同包查询仍是只含该包名的数组。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "orphan", "orphan"),
            ["orphan"],
        )
        result = self.run_cli(["why", self.lock_path, "leaf", "--from", "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertWhyObject(result.stdout, "leaf", ["leaf"])

    def test_orphan_source_walks_its_own_dependencies(self):
        # orphan 根不可达且自依赖：仍可作为起点沿自身依赖查询，自环不致死循环。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "solo", "orphan"),
            ["orphan", "solo"],
        )
        # 两包均已安装但起点不可达目标：空数组、退出码 0。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "beta", "orphan"),
            [],
        )
        result = self.run_cli(["why", self.lock_path, "beta", "--from", "orphan"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertWhyObject(result.stdout, "beta", [])

    def test_scoped_name_matched_as_full_name(self):
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "@scope/pkg"),
            ["@scope/pkg", "leaf"],
        )
        result = self.run_cli(
            ["why", self.lock_path, "leaf", "--from", "@scope/pkg"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertWhyObject(result.stdout, "leaf", ["@scope/pkg", "leaf"])

    def test_literal_root_name_is_a_normal_package(self):
        # --from "$root" 不是虚拟根别名：以名为 $root 的已安装包查询。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "solo", "$root"),
            ["$root", "solo"],
        )
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "beta", "$root"),
            [],
        )
        result = self.run_cli(["why", self.lock_path, "solo", "--from", "$root"])
        self.assertEqual(result.returncode, 0)
        self.assertWhyObject(result.stdout, "solo", ["$root", "solo"])

    def test_case_sensitive_full_name_only(self):
        with self.assertRaises(NotFoundError):
            find_path(self.root_deps, self.packages_map, "LEAF", "alpha")
        with self.assertRaises(NotFoundError):
            find_path(self.root_deps, self.packages_map, "leaf", "ALPHA")
        for args in (
            ["why", self.lock_path, "LEAF", "--from", "alpha"],
            ["why", self.lock_path, "leaf", "--from", "ALPHA"],
        ):
            result = self.run_cli(args)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, "NOT_FOUND\n")
            self.assertEqual(result.stdout, "")

    def test_missing_target_or_source_not_found(self):
        for target, source in (("ghost", "alpha"), ("beta", "ghost"), ("g1", "g2")):
            with self.subTest(target=target, source=source):
                with self.assertRaises(NotFoundError):
                    find_path(self.root_deps, self.packages_map, target, source)
        for args in (
            ["why", self.lock_path, "ghost", "--from", "alpha"],
            ["why", self.lock_path, "beta", "--from", "ghost"],
            ["why", self.lock_path, "g1", "--from", "g2"],
        ):
            with self.subTest(args=args):
                result = self.run_cli(args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "NOT_FOUND\n")
                self.assertEqual(result.stdout, "")

    def test_default_query_without_from_is_unchanged(self):
        # 省略 --from：路径仍以 $root 开始，结果与旧行为一致。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf"),
            ["$root", "alpha", "charlie", "leaf"],
        )
        result = self.run_cli(["why", DEMO_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertWhyObject(result.stdout, "beta", ["$root", "alpha", "beta"])

    def test_from_does_not_mutate_loaded_data(self):
        deps_before = list(self.packages_map["alpha"]["deps"])
        find_path(self.root_deps, self.packages_map, "leaf", "alpha")
        find_path(self.root_deps, self.packages_map, "beta", "orphan")
        self.assertEqual(self.packages_map["alpha"]["deps"], deps_before)


class WhyFromInputErrors(unittest.TestCase):
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

    def test_invalid_input_beats_missing_query_packages(self):
        # 悬空依赖使整份文件无效；即使起点与目标都未安装，也以 INPUT_ERROR 为准。
        data = graph_lock_data()
        data["packages"]["node_modules/solo"]["dependencies"] = {"missing": "1.0.0"}
        path = write_lockfile(self.tmp, data, "dangling.json")
        result = self.run_cli(["why", path, "ghost", "--from", "phantom"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_nested_path_and_link_still_rejected_with_from(self):
        nested = graph_lock_data()
        nested["packages"]["node_modules/alpha/node_modules/nested"] = {
            "version": "1.0.0"
        }
        nested_path = write_lockfile(self.tmp, nested, "nested.json")
        result = self.run_cli(["why", nested_path, "leaf", "--from", "alpha"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

        linked = graph_lock_data()
        linked["packages"]["node_modules/linked"] = {
            "version": "1.0.0",
            "link": True,
        }
        linked_path = write_lockfile(self.tmp, linked, "link.json")
        result = self.run_cli(["why", linked_path, "leaf", "--from", "alpha"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_unreadable_file_is_input_error(self):
        result = self.run_cli(
            ["why", os.path.join(self.tmp, "missing.json"), "x", "--from", "y"]
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
