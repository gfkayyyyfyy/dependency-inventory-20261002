"""why 查询（最短来源路径）的回归测试（仅标准库）。

覆盖：等长路径的字典序规则、循环依赖下的确定结果、书写顺序无关性、
路径长度优先于字典序、孤儿包与未安装包的接口及命令行行为。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, NotFoundError, find_path, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LEAF_PATH = ["$root", "alpha", "charlie", "leaf"]


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def why_packages(root_deps=None):
    """题目约定图：根声明 zeta/alpha，含 alpha<->charlie 循环与自依赖孤儿包。"""
    if root_deps is None:
        root_deps = {"zeta": "1.0.0", "alpha": "1.0.0"}
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": root_deps},
            "node_modules/zeta": {
                "version": "1.0.0",
                "dependencies": {"beta": "1.0.0"},
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
            "node_modules/beta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/leaf": {"version": "1.0.0"},
            "node_modules/orphan": {
                "version": "1.0.0",
                "dependencies": {"orphan": "1.0.0"},
            },
        },
    }


def permuted_why_packages():
    """与 why_packages 图相同，仅交换根依赖、包条目及各 dependencies 书写顺序。"""
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"alpha": "1.0.0", "zeta": "1.0.0"}},
            "node_modules/orphan": {
                "version": "1.0.0",
                "dependencies": {"orphan": "1.0.0"},
            },
            "node_modules/leaf": {"version": "1.0.0"},
            "node_modules/beta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/charlie": {
                "version": "1.0.0",
                "dependencies": {"alpha": "1.0.0", "leaf": "1.0.0"},
            },
            "node_modules/delta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/alpha": {
                "version": "1.0.0",
                "dependencies": {"charlie": "1.0.0", "delta": "1.0.0"},
            },
            "node_modules/zeta": {
                "version": "1.0.0",
                "dependencies": {"beta": "1.0.0"},
            },
        },
    }


class WhyQuery(unittest.TestCase):
    """find_path 公开接口与 why 命令在约定图上的回归行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, why_packages())

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, path=None):
        return load_lockfile(path or self.lock_path)

    @staticmethod
    def run_cli(argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def run_why(self, name, path=None):
        return self.run_cli(["why", path or self.lock_path, name])

    # --- 等长路径取字典序最小 ---

    def test_find_path_picks_lexicographic_among_equal_length(self):
        # leaf 有两条等长路径：$root->alpha->charlie->leaf 与
        # $root->zeta->beta->leaf，字典序最小者胜出。
        root_deps, packages_map = self.load()
        self.assertEqual(find_path(root_deps, packages_map, "leaf"), LEAF_PATH)

    def test_find_path_deterministic_for_all_reachable_nodes(self):
        root_deps, packages_map = self.load()
        expected = {
            "alpha": ["$root", "alpha"],
            "zeta": ["$root", "zeta"],
            "charlie": ["$root", "alpha", "charlie"],
            "delta": ["$root", "alpha", "delta"],
            "beta": ["$root", "zeta", "beta"],
            "leaf": LEAF_PATH,
        }
        for name, path in expected.items():
            with self.subTest(name=name):
                self.assertEqual(find_path(root_deps, packages_map, name), path)

    def test_why_cli_outputs_leaf_path(self):
        result = self.run_why("leaf")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), {"name": "leaf", "path": LEAF_PATH}
        )

    # --- 循环依赖 ---

    def test_cycle_terminates_with_agreed_result(self):
        # charlie -> alpha 构成可达循环；查询须结束且结果确定。
        root_deps, packages_map = self.load()
        for name in ("alpha", "charlie", "leaf"):
            with self.subTest(name=name):
                first = find_path(root_deps, packages_map, name)
                second = find_path(root_deps, packages_map, name)
                self.assertEqual(first, second)
        self.assertEqual(find_path(root_deps, packages_map, "leaf"), LEAF_PATH)

    def test_why_cli_on_cycle_member(self):
        result = self.run_why("charlie")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "charlie", "path": ["$root", "alpha", "charlie"]},
        )

    # --- 书写顺序无关 ---

    def test_declaration_order_does_not_change_result(self):
        permuted_path = write_lockfile(self.tmp, permuted_why_packages(), "permuted.json")
        root_deps, packages_map = self.load(permuted_path)
        self.assertEqual(find_path(root_deps, packages_map, "leaf"), LEAF_PATH)
        result = self.run_why("leaf", permuted_path)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), {"name": "leaf", "path": LEAF_PATH}
        )

    # --- 路径长度优先于字典序 ---

    def test_shorter_path_beats_lexicographic(self):
        # 根节点直接声明 leaf 后，["$root", "leaf"] 虽字典序更大但更短，应胜出。
        data = why_packages(root_deps={"zeta": "1.0.0", "alpha": "1.0.0", "leaf": "1.0.0"})
        direct_path = write_lockfile(self.tmp, data, "direct-leaf.json")
        root_deps, packages_map = self.load(direct_path)
        self.assertEqual(find_path(root_deps, packages_map, "leaf"), ["$root", "leaf"])
        result = self.run_why("leaf", direct_path)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), {"name": "leaf", "path": ["$root", "leaf"]}
        )

    # --- 孤儿包与未安装包 ---

    def test_orphan_returns_empty_path_via_api(self):
        root_deps, packages_map = self.load()
        self.assertEqual(find_path(root_deps, packages_map, "orphan"), [])

    def test_orphan_cli_outputs_empty_path(self):
        result = self.run_why("orphan")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), {"name": "orphan", "path": []}
        )

    def test_ghost_raises_not_found_via_api(self):
        root_deps, packages_map = self.load()
        with self.assertRaises(NotFoundError):
            find_path(root_deps, packages_map, "ghost")

    def test_ghost_cli_reports_not_found(self):
        result = self.run_why("ghost")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    # --- 成功查询的输出形态 ---

    def test_successful_queries_have_clean_output_shape(self):
        for name in ("alpha", "zeta", "charlie", "delta", "beta", "leaf", "orphan"):
            with self.subTest(name=name):
                result = self.run_why(name)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "")
                payload = json.loads(result.stdout)
                self.assertEqual(set(payload), {"name", "path"})
                self.assertEqual(payload["name"], name)
                self.assertIsInstance(payload["path"], list)


class ExistingBehaviorPreserved(unittest.TestCase):
    """新图上的 list 结果、整文件校验及嵌套路径与链接包的拒绝行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, why_packages())

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def run_cli(argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_list_on_why_fixture(self):
        result = self.run_cli(["list", self.lock_path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "1.0.0", "direct": False},
                {"name": "charlie", "version": "1.0.0", "direct": False},
                {"name": "delta", "version": "1.0.0", "direct": False},
                {"name": "leaf", "version": "1.0.0", "direct": False},
                {"name": "orphan", "version": "1.0.0", "direct": False},
                {"name": "zeta", "version": "1.0.0", "direct": True},
            ],
        )

    def test_nested_install_path_still_rejected(self):
        data = why_packages()
        data["packages"]["node_modules/leaf/node_modules/extra"] = {"version": "1.0.0"}
        path = write_lockfile(self.tmp, data, "nested.json")
        with self.assertRaises(InputError):
            load_lockfile(path)
        result = self.run_cli(["why", path, "leaf"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_linked_package_still_rejected(self):
        data = why_packages()
        data["packages"]["node_modules/linked"] = {"version": "1.0.0", "link": True}
        path = write_lockfile(self.tmp, data, "linked.json")
        with self.assertRaises(InputError):
            load_lockfile(path)
        result = self.run_cli(["list", path])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_whole_file_validation_rejects_dangling_dependency(self):
        # 整文件校验：不可达条目的非法依赖同样拒绝。
        data = why_packages()
        data["packages"]["node_modules/orphan"]["dependencies"] = {"missing": "1.0.0"}
        path = write_lockfile(self.tmp, data, "dangling.json")
        with self.assertRaises(InputError):
            load_lockfile(path)
        result = self.run_cli(["why", path, "leaf"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
