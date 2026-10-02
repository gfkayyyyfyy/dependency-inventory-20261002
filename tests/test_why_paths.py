"""why 查询的回归测试：等长路径的字典序规则、循环依赖下的确定性结果。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构：
- 所有包版本均为 1.0.0，依赖声明均为字符串；
- 根节点声明 zeta、alpha；alpha 声明 delta、charlie；
  delta 与 charlie 都依赖 leaf；zeta 经 beta 也到达 leaf；
- charlie 还依赖 alpha，构成可达循环；orphan 不被根节点引用且依赖自身。

全程离线、仅用标准库，不修改 demo-lock.json，不安装或执行锁文件中的依赖。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import depinventory
from depinventory import InputError, NotFoundError, find_path, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def cycle_lock_data():
    """构造等长路径 + 可达循环 + 不可达自环的平铺 v3 锁文件数据。

    书写顺序刻意与字典序相反（根依赖 zeta 在 alpha 前、alpha 的
    dependencies 中 delta 在 charlie 前），以证明结果不取决于声明顺序。
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
                "dependencies": {"orphan": "1.0.0"},
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


def with_direct_root_leaf(data):
    """在根节点增加对 leaf 的直接声明，其余关系不变。"""
    enriched = json.loads(json.dumps(data))
    enriched["packages"][""]["dependencies"]["leaf"] = "1.0.0"
    return enriched


class WhyPathRegression(unittest.TestCase):
    """等长路径字典序、循环确定性、不可达/未安装包的 why 行为。"""

    EXPECTED_LEAF_PATH = ["$root", "alpha", "charlie", "leaf"]

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

    def assertWhyObject(self, stdout, name, path):
        """成功输出必须是以换行结尾的 JSON 对象，且仅含 name 与 path。"""
        self.assertTrue(stdout.endswith("\n"))
        parsed = json.loads(stdout)
        self.assertEqual(set(parsed), {"name", "path"})
        self.assertEqual(parsed, {"name": name, "path": path})

    def test_equal_length_paths_use_lexicographic_order_api(self):
        # 三条路径同为 3 跳：
        #   $root/alpha/charlie/leaf、$root/alpha/delta/leaf、$root/zeta/beta/leaf
        # 取包名序列 Unicode 码点字典序最小的一条。
        root_deps, packages_map = load_lockfile(
            write_lockfile(self.tmp, cycle_lock_data())
        )
        self.assertEqual(
            find_path(root_deps, packages_map, "leaf"),
            self.EXPECTED_LEAF_PATH,
        )

    def test_equal_length_paths_use_lexicographic_order_cli(self):
        path = write_lockfile(self.tmp, cycle_lock_data())
        result = self.run_cli(["why", path, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertWhyObject(result.stdout, "leaf", self.EXPECTED_LEAF_PATH)

    def test_declaration_order_swap_keeps_result_identical(self):
        canonical = cycle_lock_data()
        reordered = with_reversed_key_order(canonical)

        canonical_path = write_lockfile(self.tmp, canonical, "canonical.json")
        reordered_path = write_lockfile(self.tmp, reordered, "reordered.json")

        canonical_root_deps, canonical_map = load_lockfile(canonical_path)
        reordered_root_deps, reordered_map = load_lockfile(reordered_path)

        # 先证明书写顺序确实被交换：根依赖与 alpha 依赖的声明次序相反。
        self.assertEqual(canonical_root_deps, ["zeta", "alpha"])
        self.assertEqual(reordered_root_deps, ["alpha", "zeta"])
        self.assertEqual(canonical_map["alpha"]["deps"], ["delta", "charlie"])
        self.assertEqual(reordered_map["alpha"]["deps"], ["charlie", "delta"])

        # 图关系不变：接口与命令的 leaf 结果都保持一致。
        self.assertEqual(
            find_path(canonical_root_deps, canonical_map, "leaf"),
            find_path(reordered_root_deps, reordered_map, "leaf"),
        )
        self.assertEqual(
            find_path(reordered_root_deps, reordered_map, "leaf"),
            self.EXPECTED_LEAF_PATH,
        )
        result = self.run_cli(["why", reordered_path, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertWhyObject(result.stdout, "leaf", self.EXPECTED_LEAF_PATH)

    def test_direct_root_declaration_beats_lexicographic_order(self):
        # 根节点直接声明 leaf 后，2 节点路径短于任一 4 节点路径，
        # 即便 leaf 在根依赖中排在最后也必须优先。
        data = with_direct_root_leaf(cycle_lock_data())
        path = write_lockfile(self.tmp, data, "direct-leaf.json")
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, ["zeta", "alpha", "leaf"])
        self.assertEqual(
            find_path(root_deps, packages_map, "leaf"),
            ["$root", "leaf"],
        )

        result = self.run_cli(["why", path, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertWhyObject(result.stdout, "leaf", ["$root", "leaf"])

        # 交换书写顺序后结论相同：长度优先于字典序，也与声明顺序无关。
        reordered = with_reversed_key_order(data)
        reordered_path = write_lockfile(self.tmp, reordered, "direct-leaf-rev.json")
        rev_root_deps, rev_map = load_lockfile(reordered_path)
        self.assertEqual(
            find_path(rev_root_deps, rev_map, "leaf"), ["$root", "leaf"]
        )

    def test_reachable_cycle_terminates_with_determined_paths(self):
        # charlie -> alpha 构成循环；对循环上各节点的查询都必须正常结束，
        # 且结果由最短路径与字典序唯一确定。
        path = write_lockfile(self.tmp, cycle_lock_data())
        root_deps, packages_map = load_lockfile(path)

        expected = {
            "alpha": ["$root", "alpha"],
            "charlie": ["$root", "alpha", "charlie"],
            "delta": ["$root", "alpha", "delta"],
            "beta": ["$root", "zeta", "beta"],
            "leaf": self.EXPECTED_LEAF_PATH,
        }
        for name, expected_path in expected.items():
            with self.subTest(name=name):
                self.assertEqual(
                    find_path(root_deps, packages_map, name),
                    expected_path,
                )
                result = self.run_cli(["why", path, name])
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "")
                self.assertWhyObject(result.stdout, name, expected_path)

    def test_orphan_unreachable_with_self_dependency_api(self):
        # orphan 已安装但根节点不可达，自身依赖自身不应造成死循环；接口返回 []。
        root_deps, packages_map = load_lockfile(
            write_lockfile(self.tmp, cycle_lock_data())
        )
        self.assertEqual(packages_map["orphan"]["deps"], ["orphan"])
        self.assertEqual(find_path(root_deps, packages_map, "orphan"), [])

    def test_orphan_unreachable_with_self_dependency_cli(self):
        path = write_lockfile(self.tmp, cycle_lock_data())
        result = self.run_cli(["why", path, "orphan"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertWhyObject(result.stdout, "orphan", [])

    def test_uninstalled_ghost_not_found_api(self):
        root_deps, packages_map = load_lockfile(
            write_lockfile(self.tmp, cycle_lock_data())
        )
        with self.assertRaises(NotFoundError):
            find_path(root_deps, packages_map, "ghost")

    def test_uninstalled_ghost_not_found_cli(self):
        path = write_lockfile(self.tmp, cycle_lock_data())
        result = self.run_cli(["why", path, "ghost"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_root_constant_matches_contract(self):
        # 路径中的根标记固定为公开常量 ROOT 的值 "$root"。
        self.assertEqual(depinventory.ROOT, "$root")


class UnsupportedStructuresStillRejected(unittest.TestCase):
    """在循环样例上加入不支持结构时，整文件校验与 why 仍须拒绝。"""

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

    def test_nested_install_path_rejected_for_why(self):
        data = cycle_lock_data()
        data["packages"]["node_modules/alpha/node_modules/nested"] = {
            "version": "1.0.0"
        }
        path = write_lockfile(self.tmp, data, "nested.json")
        with self.assertRaises(InputError):
            load_lockfile(path)
        result = self.run_cli(["why", path, "leaf"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_linked_package_rejected_for_why(self):
        data = cycle_lock_data()
        data["packages"]["node_modules/linked"] = {
            "version": "1.0.0",
            "link": True,
        }
        path = write_lockfile(self.tmp, data, "link.json")
        with self.assertRaises(InputError):
            load_lockfile(path)
        result = self.run_cli(["why", path, "leaf"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
