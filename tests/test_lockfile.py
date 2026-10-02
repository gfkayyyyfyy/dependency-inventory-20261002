"""dependencies 字段校验及既有行为的回归测试（仅标准库）。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import depinventory
from depinventory import InputError, NotFoundError, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def demo_data():
    with open(DEMO_LOCK, encoding="utf-8") as handle:
        return json.load(handle)


def empty_lock(packages=None):
    return {
        "lockfileVersion": 3,
        "packages": packages if packages is not None else {"": {}},
    }


class NullDependenciesRejected(unittest.TestCase):
    """显式 null 的 dependencies 必须在任何命令前被整文件校验拒绝。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def set_beta_null(self):
        data = demo_data()
        data["packages"]["node_modules/beta"]["dependencies"] = None
        return write_lockfile(self.tmp, data, "beta-null.json")

    def test_load_rejects_null_on_reachable_package(self):
        with self.assertRaises(InputError):
            load_lockfile(self.set_beta_null())

    def test_load_rejects_null_on_unreachable_package(self):
        # gamma 已安装但不被任何节点引用；非法条目不可达也不能放过。
        data = demo_data()
        data["packages"]["node_modules/gamma"] = {
            "version": "3.0.0",
            "dependencies": None,
        }
        path = write_lockfile(self.tmp, data, "gamma-null.json")
        with self.assertRaises(InputError):
            load_lockfile(path)

    def test_load_rejects_null_on_root(self):
        data = demo_data()
        data["packages"][""]["dependencies"] = None
        path = write_lockfile(self.tmp, data, "root-null.json")
        with self.assertRaises(InputError):
            load_lockfile(path)

    def test_load_rejects_null_on_root_even_with_only_root_entry(self):
        path = write_lockfile(self.tmp, empty_lock({"": {"dependencies": None}}))
        with self.assertRaises(InputError):
            load_lockfile(path)

    def test_load_rejects_other_non_object_dependencies(self):
        for bad_value in ([], "deps", 1, True):
            data = demo_data()
            data["packages"]["node_modules/beta"]["dependencies"] = bad_value
            path = write_lockfile(self.tmp, data, "bad-type.json")
            with self.subTest(bad_value=bad_value):
                with self.assertRaises(InputError):
                    load_lockfile(path)

    def test_cli_rejects_null_for_list_why_found_and_why_missing(self):
        path = self.set_beta_null()
        cases = [
            ["list", path],
            ["why", path, "alpha"],
            ["why", path, "nonexistent"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                result = self.run_cli(argv)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "INPUT_ERROR\n")
                self.assertEqual(result.stdout, "")

    def test_cli_rejects_null_on_unreachable_package(self):
        data = demo_data()
        data["packages"]["node_modules/gamma"] = {
            "version": "3.0.0",
            "dependencies": None,
        }
        path = write_lockfile(self.tmp, data, "gamma-null.json")
        result = self.run_cli(["why", path, "alpha"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_cli_rejects_null_on_root_only_entry(self):
        path = write_lockfile(self.tmp, empty_lock({"": {"dependencies": None}}))
        result = self.run_cli(["list", path])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    @staticmethod
    def run_cli(argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )


class ValidDependenciesForms(unittest.TestCase):
    """字段省略、空对象、非空合法对象均可读取。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_omitted_dependencies_loads(self):
        # demo-lock.json 中 beta 即省略 dependencies。
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        self.assertEqual(packages_map["beta"]["deps"], [])
        self.assertEqual(root_deps, ["alpha"])

    def test_empty_object_dependencies_loads(self):
        data = demo_data()
        data["packages"]["node_modules/beta"]["dependencies"] = {}
        path = write_lockfile(self.tmp, data)
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(packages_map["beta"]["deps"], [])
        self.assertEqual(packages_map["alpha"]["deps"], ["beta"])
        self.assertEqual(root_deps, ["alpha"])

    def test_nonempty_object_dependencies_loads(self):
        data = demo_data()
        data["packages"]["node_modules/gamma"] = {"version": "3.0.0"}
        data["packages"]["node_modules/beta"]["dependencies"] = {"gamma": "^3.0.0"}
        path = write_lockfile(self.tmp, data)
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(packages_map["beta"]["deps"], ["gamma"])
        self.assertEqual(root_deps, ["alpha"])

    def test_root_dependencies_omitted_loads(self):
        path = write_lockfile(
            self.tmp,
            empty_lock({
                "": {},
                "node_modules/orphan": {"version": "1.0.0"},
            }),
        )
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, [])
        self.assertIn("orphan", packages_map)

    def test_non_string_dependency_value_still_rejected(self):
        data = demo_data()
        data["packages"]["node_modules/alpha"]["dependencies"] = {"beta": 2}
        path = write_lockfile(self.tmp, data)
        with self.assertRaises(InputError):
            load_lockfile(path)

    def test_dangling_dependency_still_rejected(self):
        data = demo_data()
        data["packages"]["node_modules/alpha"]["dependencies"] = {"missing": "1.0.0"}
        path = write_lockfile(self.tmp, data)
        with self.assertRaises(InputError):
            load_lockfile(path)

    def test_null_in_unrelated_field_keeps_existing_handling(self):
        # 其他字段中的 null 不参与校验：可忽略字段保持可读，
        # version 为 null 仍按原规则拒绝。
        data = demo_data()
        data["packages"]["node_modules/beta"]["optional"] = None
        path = write_lockfile(self.tmp, data)
        _, packages_map = load_lockfile(path)
        self.assertEqual(packages_map["beta"]["version"], "2.0.0")

        data["packages"]["node_modules/beta"]["version"] = None
        path_bad = write_lockfile(self.tmp, data, "bad-version.json")
        with self.assertRaises(InputError):
            load_lockfile(path_bad)


class ExistingBehaviorUnchanged(unittest.TestCase):
    """demo-lock.json 的既有结果、空清单、不可达包、排序与最短路径。"""

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

    def test_demo_list_output(self):
        result = self.run_cli(["list", DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )

    def test_demo_why_paths(self):
        result = self.run_cli(["why", DEMO_LOCK, "alpha"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "alpha", "path": ["$root", "alpha"]},
        )
        result = self.run_cli(["why", DEMO_LOCK, "beta"])
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "beta", "path": ["$root", "alpha", "beta"]},
        )

    def test_demo_why_missing_is_not_found(self):
        result = self.run_cli(["why", DEMO_LOCK, "ghost"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_empty_inventory_outputs_empty_array(self):
        path = write_lockfile(self.tmp, empty_lock())
        result = self.run_cli(["list", path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "[]\n")

    def test_installed_but_unreachable_listed_with_empty_why_path(self):
        data = demo_data()
        data["packages"]["node_modules/orphan"] = {"version": "9.9.9"}
        path = write_lockfile(self.tmp, data)
        result = self.run_cli(["list", path])
        items = json.loads(result.stdout)
        self.assertEqual(
            items,
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
                {"name": "orphan", "version": "9.9.9", "direct": False},
            ],
        )
        result = self.run_cli(["why", path, "orphan"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"name": "orphan", "path": []})

    def test_sorting_and_shortest_path_unchanged(self):
        # root -> zeta -> mid -> leaf（3 跳）
        # root -> aaaa -> leaf        （2 跳，字典序上 aaaa < zeta）
        # 另验证 list 按 Unicode 码点排序；leaf 同时被两条路径指向，取最短。
        data = empty_lock({
            "": {"dependencies": {"zeta": "1.0.0", "aaaa": "1.0.0"}},
            "node_modules/zeta": {
                "version": "1.0.0",
                "dependencies": {"mid": "1.0.0"},
            },
            "node_modules/aaaa": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/mid": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/leaf": {"version": "1.0.0"},
        })
        path = write_lockfile(self.tmp, data)
        result = self.run_cli(["list", path])
        self.assertEqual(
            [item["name"] for item in json.loads(result.stdout)],
            ["aaaa", "leaf", "mid", "zeta"],
        )
        result = self.run_cli(["why", path, "leaf"])
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "leaf", "path": ["$root", "aaaa", "leaf"]},
        )

    def test_not_found_via_api(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        with self.assertRaises(NotFoundError):
            depinventory.find_path(root_deps, packages_map, "ghost")


if __name__ == "__main__":
    unittest.main()
