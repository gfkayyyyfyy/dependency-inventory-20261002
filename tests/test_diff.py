"""diff 子命令与 diff_items 的验收及回归测试（仅标准库）。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import depinventory
from depinventory import InputError, diff_items, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def demo_data():
    with open(DEMO_LOCK, encoding="utf-8") as handle:
        return json.load(handle)


def empty_lock():
    return {"lockfileVersion": 3, "packages": {"": {}}}


class DiffAcceptance(unittest.TestCase):
    """任务验收场景：demo-lock.json → new-lock.json。"""

    EXPECTED = [
        {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
        {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"},
        {"name": "gamma", "change": "added", "before": None, "after": "3.0.0"},
    ]

    def test_cli_diff_outputs_expected_records(self):
        result = run_cli(["diff", DEMO_LOCK, NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), self.EXPECTED)
        self.assertTrue(result.stdout.endswith("\n"))

    def test_cli_diff_reverse_direction_swaps_marks(self):
        result = run_cli(["diff", NEW_LOCK, DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), [
            {"name": "alpha", "change": "added", "before": None, "after": "1.0.0"},
            {"name": "beta", "change": "changed", "before": "2.1.0", "after": "2.0.0"},
            {"name": "gamma", "change": "removed", "before": "3.0.0", "after": None},
        ])

    def test_api_diff_items_matches_cli(self):
        _, before_map = load_lockfile(DEMO_LOCK)
        _, after_map = load_lockfile(NEW_LOCK)
        self.assertEqual(diff_items(before_map, after_map), self.EXPECTED)

    def test_diff_items_exported_from_package(self):
        self.assertIs(depinventory.diff_items, diff_items)


class DiffSemantics(unittest.TestCase):
    """空清单、自比较、元数据变化与排序规则。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_self_comparison_is_empty(self):
        result = run_cli(["diff", DEMO_LOCK, DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "[]\n")
        self.assertEqual(result.stderr, "")

    def test_both_root_only_is_empty(self):
        path = write_lockfile(self.tmp, empty_lock())
        result = run_cli(["diff", path, path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "[]\n")

    def test_root_project_version_change_produces_no_record(self):
        before = empty_lock()
        after = empty_lock()
        after["packages"][""]["version"] = "9.9.9"
        before_path = write_lockfile(self.tmp, before, "before.json")
        after_path = write_lockfile(self.tmp, after, "after.json")
        result = run_cli(["diff", before_path, after_path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "[]\n")

    def test_dependency_declaration_change_produces_no_record(self):
        # 版本相同，仅依赖声明变化（alpha 改依赖 gamma）不产生记录。
        before = demo_data()
        after = demo_data()
        after["packages"][""]["dependencies"] = {"alpha": "1.0.0"}
        after["packages"]["node_modules/alpha"]["dependencies"] = {}
        after["packages"]["node_modules/beta"]["version"] = "2.0.0"
        before_path = write_lockfile(self.tmp, before, "before.json")
        after_path = write_lockfile(self.tmp, after, "after.json")
        result = run_cli(["diff", before_path, after_path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "[]\n")

    def test_unreachable_packages_are_compared(self):
        # gamma 不被任何节点引用，仍参与比较。
        before = demo_data()
        before["packages"]["node_modules/gamma"] = {"version": "3.0.0"}
        after = demo_data()
        after["packages"]["node_modules/gamma"] = {"version": "3.1.0"}
        before_path = write_lockfile(self.tmp, before, "before.json")
        after_path = write_lockfile(self.tmp, after, "after.json")
        result = run_cli(["diff", before_path, after_path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), [
            {"name": "gamma", "change": "changed", "before": "3.0.0", "after": "3.1.0"},
        ])

    def test_scoped_and_case_sensitive_names_sorted_by_codepoint(self):
        packages = {"": {}}
        for key, version in (
            ("node_modules/Zeta", "1.0.0"),
            ("node_modules/@scope/pkg", "1.0.0"),
            ("node_modules/alpha", "1.0.0"),
        ):
            packages[key] = {"version": version}
        before = {"lockfileVersion": 3, "packages": packages}
        after = {"lockfileVersion": 3, "packages": {"": {}}}
        before_path = write_lockfile(self.tmp, before, "before.json")
        after_path = write_lockfile(self.tmp, after, "after.json")
        result = run_cli(["diff", before_path, after_path])
        self.assertEqual(result.returncode, 0)
        # Unicode 码点升序："@" < "Z" < "a"。
        self.assertEqual(
            [record["name"] for record in json.loads(result.stdout)],
            ["@scope/pkg", "Zeta", "alpha"],
        )

    def test_version_string_kept_verbatim(self):
        before = demo_data()
        before["packages"]["node_modules/alpha"]["version"] = "1.0.0-beta.1+build"
        before_path = write_lockfile(self.tmp, before, "before.json")
        result = run_cli(["diff", before_path, DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), [
            {"name": "alpha", "change": "changed",
             "before": "1.0.0-beta.1+build", "after": "1.0.0"},
        ])


class DiffInputErrors(unittest.TestCase):
    """任一输入违反完整校验规则时统一退出码 2，不输出部分报告。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_input_error(self, argv):
        result = run_cli(argv)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_missing_before_file(self):
        self.assert_input_error(
            ["diff", os.path.join(self.tmp, "missing.json"), DEMO_LOCK])

    def test_missing_after_file(self):
        self.assert_input_error(
            ["diff", DEMO_LOCK, os.path.join(self.tmp, "missing.json")])

    def test_invalid_utf8_rejected(self):
        path = os.path.join(self.tmp, "bad.json")
        with open(path, "wb") as handle:
            handle.write(b'{"lockfileVersion": 3, "packages": {"": \xff}}')
        self.assert_input_error(["diff", DEMO_LOCK, path])

    def test_invalid_json_rejected(self):
        path = os.path.join(self.tmp, "broken.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assert_input_error(["diff", path, DEMO_LOCK])

    def test_dangling_dependency_rejected(self):
        data = demo_data()
        data["packages"][""]["dependencies"]["ghost"] = "1.0.0"
        path = write_lockfile(self.tmp, data)
        self.assert_input_error(["diff", DEMO_LOCK, path])

    def test_nested_path_rejected(self):
        data = demo_data()
        data["packages"]["node_modules/alpha/node_modules/deep"] = {"version": "1.0.0"}
        path = write_lockfile(self.tmp, data)
        self.assert_input_error(["diff", path, DEMO_LOCK])

    def test_link_entry_rejected(self):
        data = demo_data()
        data["packages"]["node_modules/linked"] = {"version": "1.0.0", "link": True}
        path = write_lockfile(self.tmp, data)
        self.assert_input_error(["diff", DEMO_LOCK, path])

    def test_empty_version_rejected(self):
        data = demo_data()
        data["packages"]["node_modules/alpha"]["version"] = ""
        path = write_lockfile(self.tmp, data)
        self.assert_input_error(["diff", path, DEMO_LOCK])


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    unittest.main()
