"""diff 子命令的回归测试：版本差异报告的方向、排序与错误处理。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
全程离线、仅用标准库，不修改 demo-lock.json，不安装或执行锁文件中的依赖。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, diff_items, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def lock_data(root_deps, packages):
    """由根依赖声明与 {名称: 版本或 (版本, 依赖)} 构造平铺 v3 锁文件数据。"""
    entries = {"": {"dependencies": dict(root_deps)}}
    for name, spec in packages.items():
        if isinstance(spec, tuple):
            version, deps = spec
        else:
            version, deps = spec, None
        node = {"version": version}
        if deps is not None:
            node["dependencies"] = dict(deps)
        entries["node_modules/" + name] = node
    return {"lockfileVersion": 3, "packages": entries}


class DiffAcceptance(unittest.TestCase):
    """验收场景：demo-lock.json 为旧清单，new-lock.json 为新清单。"""

    EXPECTED = [
        {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
        {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"},
        {"name": "gamma", "change": "added", "before": None, "after": "3.0.0"},
    ]

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

    def assertDiffArray(self, stdout, expected):
        """成功输出必须是以换行结尾的 JSON 数组，每项仅含四个字段。"""
        self.assertTrue(stdout.endswith("\n"))
        parsed = json.loads(stdout)
        self.assertIsInstance(parsed, list)
        for record in parsed:
            self.assertEqual(set(record), {"name", "change", "before", "after"})
        self.assertEqual(parsed, expected)

    def test_acceptance_scenario_cli(self):
        result = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertDiffArray(result.stdout, self.EXPECTED)

    def test_acceptance_scenario_api(self):
        _, before_map = load_lockfile(DEMO_LOCK)
        _, after_map = load_lockfile(NEW_LOCK)
        self.assertEqual(diff_items(before_map, after_map), self.EXPECTED)

    def test_argument_order_determines_direction(self):
        # 交换参数后 removed/added 互换、changed 的前后版本互换。
        result = self.run_cli(["diff", NEW_LOCK, DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertDiffArray(
            result.stdout,
            [
                {"name": "alpha", "change": "added", "before": None, "after": "1.0.0"},
                {"name": "beta", "change": "changed", "before": "2.1.0", "after": "2.0.0"},
                {"name": "gamma", "change": "removed", "before": "3.0.0", "after": None},
            ],
        )

    def test_self_comparison_is_empty(self):
        result = self.run_cli(["diff", DEMO_LOCK, DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertDiffArray(result.stdout, [])

    def test_both_root_only_is_empty(self):
        path = write_lockfile(self.tmp, lock_data({}, {}))
        result = self.run_cli(["diff", path, path])
        self.assertEqual(result.returncode, 0)
        self.assertDiffArray(result.stdout, [])


class DiffSemantics(unittest.TestCase):
    """排序、大小写、作用域包、不可达包与元数据变化的 diff 语义。"""

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

    def diff_cli(self, before_data, after_data):
        before = write_lockfile(self.tmp, before_data, "before.json")
        after = write_lockfile(self.tmp, after_data, "after.json")
        result = self.run_cli(["diff", before, after])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_unreachable_packages_compared_and_sorted_by_codepoint(self):
        # gamma 不被根节点引用，仍参与比较；排序按 Unicode 码点
        # （大写字母排在小写之前，作用域包 @ 排在字母之前）。
        before = lock_data(
            {"alpha": "1.0.0"},
            {
                "alpha": "1.0.0",
                "Beta": "1.0.0",
                "@scope/pkg": "1.0.0",
                "gamma": "1.0.0",
            },
        )
        after = lock_data(
            {"alpha": "1.0.0"},
            {
                "alpha": "1.0.0",
                "Beta": "2.0.0",
                "@scope/pkg": "2.0.0",
                "gamma": "2.0.0",
            },
        )
        records = self.diff_cli(before, after)
        self.assertEqual(
            [r["name"] for r in records],
            ["@scope/pkg", "Beta", "gamma"],
        )
        self.assertTrue(all(r["change"] == "changed" for r in records))

    def test_name_matching_is_case_sensitive(self):
        before = lock_data({}, {"alpha": "1.0.0"})
        after = lock_data({}, {"Alpha": "1.0.0"})
        self.assertEqual(
            self.diff_cli(before, after),
            [
                {"name": "Alpha", "change": "added", "before": None, "after": "1.0.0"},
                {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
            ],
        )

    def test_identical_versions_produce_no_records(self):
        before = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "b": ("2.0.0", {"a": "1.0.0"})})
        after = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "b": ("2.0.0", {"a": "1.0.0"})})
        self.assertEqual(self.diff_cli(before, after), [])

    def test_metadata_changes_produce_no_records(self):
        # 依赖声明、直接/传递身份与根项目版本变化都不产生记录。
        before = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "b": "2.0.0"})
        after = lock_data({"b": "2.0.0"}, {"a": "1.0.0", "b": ("2.0.0", {"a": "1.0.0"})})
        before["packages"][""]["version"] = "1.0.0"
        after["packages"][""]["version"] = "9.9.9"
        self.assertEqual(self.diff_cli(before, after), [])

    def test_version_strings_kept_verbatim(self):
        # 不解析版本范围，版本字符串原样保留，不判断升级或降级。
        before = lock_data({}, {"a": "2.0.0"})
        after = lock_data({}, {"a": "1.0.0-beta.1"})
        self.assertEqual(
            self.diff_cli(before, after),
            [
                {
                    "name": "a",
                    "change": "changed",
                    "before": "2.0.0",
                    "after": "1.0.0-beta.1",
                }
            ],
        )


class DiffInputErrors(unittest.TestCase):
    """任一输入违反校验规则时：退出码 2、stderr 仅 INPUT_ERROR、stdout 为空。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.good = write_lockfile(
            self.tmp, lock_data({"a": "1.0.0"}, {"a": "1.0.0"}), "good.json"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assertInputError(self, result):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")

    def test_missing_file_on_either_side(self):
        missing = os.path.join(self.tmp, "missing.json")
        self.assertInputError(self.run_cli(["diff", missing, self.good]))
        self.assertInputError(self.run_cli(["diff", self.good, missing]))

    def test_invalid_utf8_on_either_side(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "wb") as handle:
            handle.write(b'{"lockfileVersion": 3, "packages": {"": \xff}}')
        self.assertInputError(self.run_cli(["diff", bad, self.good]))
        self.assertInputError(self.run_cli(["diff", self.good, bad]))

    def test_broken_json(self):
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertInputError(self.run_cli(["diff", broken, self.good]))

    def test_nested_install_path_rejected(self):
        data = lock_data({}, {"a": "1.0.0"})
        data["packages"]["node_modules/a/node_modules/b"] = {"version": "1.0.0"}
        path = write_lockfile(self.tmp, data, "nested.json")
        self.assertInputError(self.run_cli(["diff", self.good, path]))

    def test_link_true_rejected(self):
        data = lock_data({}, {"a": "1.0.0"})
        data["packages"]["node_modules/a"]["link"] = True
        path = write_lockfile(self.tmp, data, "link.json")
        self.assertInputError(self.run_cli(["diff", self.good, path]))

    def test_dangling_dependency_rejected(self):
        data = lock_data({"ghost": "1.0.0"}, {})
        path = write_lockfile(self.tmp, data, "dangling.json")
        self.assertInputError(self.run_cli(["diff", self.good, path]))

    def test_dependencies_not_object_rejected(self):
        data = lock_data({}, {"a": "1.0.0"})
        data["packages"]["node_modules/a"]["dependencies"] = ["b"]
        path = write_lockfile(self.tmp, data, "deps.json")
        self.assertInputError(self.run_cli(["diff", self.good, path]))

    def test_invalid_version_field_rejected(self):
        data = lock_data({}, {"a": ""})
        path = write_lockfile(self.tmp, data, "version.json")
        self.assertInputError(self.run_cli(["diff", self.good, path]))

    def test_api_raises_input_error(self):
        with self.assertRaises(InputError):
            load_lockfile(os.path.join(self.tmp, "missing.json"))


if __name__ == "__main__":
    unittest.main()
