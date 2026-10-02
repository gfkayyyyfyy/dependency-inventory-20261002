"""dependencies 显式为 null 的校验回归测试。

仅使用 Python 标准库；通过临时文件与 `python -m depinventory` 子进程验证，
不联网、不安装或执行任何依赖包，也不改动仓库内的输入文件。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, load_lockfile

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")


def _demo_data():
    with open(DEMO_LOCK, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _root_only_data():
    return {
        "name": "root-only",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "packages": {"": {"name": "root-only", "version": "1.0.0"}},
    }


class LockfileCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def write_lock(self, data):
        path = os.path.join(self._tmp.name, "lock.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assert_input_error_cli(self, proc):
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "INPUT_ERROR\n")
        self.assertEqual(proc.stdout, "")

    # ---- 既有行为基线 ----

    def test_demo_lock_baseline(self):
        proc = self.run_cli("list", DEMO_LOCK)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )
        proc = self.run_cli("why", DEMO_LOCK, "beta")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout),
            {"name": "beta", "path": ["$root", "alpha", "beta"]},
        )

    def test_empty_lock_lists_empty_array(self):
        path = self.write_lock(_root_only_data())
        proc = self.run_cli("list", path)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "[]\n")

    def test_unreachable_installed_package(self):
        data = _demo_data()
        data["packages"]["node_modules/gamma"] = {"version": "3.0.0"}
        path = self.write_lock(data)
        proc = self.run_cli("list", path)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
                {"name": "gamma", "version": "3.0.0", "direct": False},
            ],
        )
        proc = self.run_cli("why", path, "gamma")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {"name": "gamma", "path": []})

    def test_missing_package_not_found(self):
        proc = self.run_cli("why", DEMO_LOCK, "nonexistent")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stderr, "NOT_FOUND\n")
        self.assertEqual(proc.stdout, "")

    def test_null_in_other_fields_untouched(self):
        data = _demo_data()
        data["packages"]["node_modules/beta"]["description"] = None
        data["packages"][""]["license"] = None
        path = self.write_lock(data)
        proc = self.run_cli("list", path)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(json.loads(proc.stdout)), 2)

    # ---- null dependencies 校验 ----

    def test_beta_null_dependencies_rejected(self):
        data = _demo_data()
        data["packages"]["node_modules/beta"]["dependencies"] = None
        path = self.write_lock(data)
        with self.assertRaises(InputError):
            load_lockfile(path)
        self.assert_input_error_cli(self.run_cli("list", path))
        self.assert_input_error_cli(self.run_cli("why", path, "alpha"))

    def test_beta_dependencies_removed_or_empty_recovers(self):
        for variant in ("omitted", "empty"):
            with self.subTest(variant=variant):
                data = _demo_data()
                if variant == "empty":
                    data["packages"]["node_modules/beta"]["dependencies"] = {}
                path = self.write_lock(data)
                proc = self.run_cli("list", path)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(len(json.loads(proc.stdout)), 2)
                proc = self.run_cli("why", path, "alpha")
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(
                    json.loads(proc.stdout),
                    {"name": "alpha", "path": ["$root", "alpha"]},
                )

    def test_root_null_dependencies_rejected(self):
        data = _demo_data()
        data["packages"][""]["dependencies"] = None
        path = self.write_lock(data)
        with self.assertRaises(InputError):
            load_lockfile(path)
        self.assert_input_error_cli(self.run_cli("list", path))
        self.assert_input_error_cli(self.run_cli("why", path, "alpha"))

    def test_root_only_null_dependencies_rejected(self):
        data = _root_only_data()
        data["packages"][""]["dependencies"] = None
        path = self.write_lock(data)
        with self.assertRaises(InputError):
            load_lockfile(path)
        self.assert_input_error_cli(self.run_cli("list", path))

    def test_unreachable_package_null_dependencies_rejected(self):
        data = _demo_data()
        data["packages"]["node_modules/gamma"] = {
            "version": "3.0.0",
            "dependencies": None,
        }
        path = self.write_lock(data)
        with self.assertRaises(InputError):
            load_lockfile(path)
        self.assert_input_error_cli(self.run_cli("list", path))
        self.assert_input_error_cli(self.run_cli("why", path, "alpha"))

    def test_invalid_file_query_missing_package_is_input_error(self):
        data = _demo_data()
        data["packages"]["node_modules/beta"]["dependencies"] = None
        path = self.write_lock(data)
        proc = self.run_cli("why", path, "nonexistent")
        self.assert_input_error_cli(proc)

    def test_non_object_dependencies_rejected(self):
        for bad in (["alpha"], "alpha", 1, 1.5, True, False):
            with self.subTest(bad=bad):
                data = _demo_data()
                data["packages"]["node_modules/beta"]["dependencies"] = bad
                path = self.write_lock(data)
                with self.assertRaises(InputError):
                    load_lockfile(path)
                self.assert_input_error_cli(self.run_cli("list", path))

    def test_valid_dependencies_variants_accepted(self):
        variants = {
            "omitted": None,  # 哨兵：表示删除该字段
            "empty": {},
            "non_empty": {"alpha": "1.0.0"},
        }
        for label, deps in variants.items():
            with self.subTest(variant=label):
                data = _demo_data()
                if deps is None:
                    data["packages"]["node_modules/beta"].pop("dependencies", None)
                else:
                    data["packages"]["node_modules/beta"]["dependencies"] = deps
                path = self.write_lock(data)
                root_deps, packages_map = load_lockfile(path)
                self.assertEqual(root_deps, ["alpha"])
                self.assertEqual(packages_map["beta"]["version"], "2.0.0")


if __name__ == "__main__":
    unittest.main()
