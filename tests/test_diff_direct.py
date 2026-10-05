"""diff --direct 根直接声明筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖。

固定的既有行为：
- 验收场景：diff demo-lock.json new-lock.json --direct 只输出
  alpha@1.0.0 的 removed 与 beta@2.1.0 的 added，gamma 不出现；
- 每侧成员只取该侧根节点 dependencies 直接声明的已安装包，版本取安装
  条目的原始字符串，不解析根声明的版本范围；传递依赖、未引用包与根
  项目本身不进入该侧范围，其他字段不参与成员筛选；
- 仅新侧直接声明输出 added（before 为 null），仅旧侧直接声明输出
  removed（after 为 null），即使另一侧仍安装着相同版本也如此；两侧
  均直接声明时仅版本字符串不同输出 changed，只改变根声明的范围文本
  不产生记录；
- --direct 与 --reachable 可同时使用，结果与只用 --direct 一致；
- 省略 --direct 时 diff 与 diff_items 两参数调用语义不变；
- 筛选不放宽整份校验：未进入比较范围的条目的校验错误同样以
  INPUT_ERROR 失败。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import diff_items, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")

EXPECTED_DIRECT = [
    {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
    {"name": "beta", "change": "added", "before": None, "after": "2.1.0"},
]

EXPECTED_FULL = [
    {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
    {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"},
    {"name": "gamma", "change": "added", "before": None, "after": "3.0.0"},
]


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


class DiffDirectAcceptance(unittest.TestCase):
    """验收场景：demo-lock.json 为旧清单，new-lock.json 为新清单。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_acceptance_scenario_cli(self):
        result = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout), EXPECTED_DIRECT)

    def test_direct_combined_with_reachable_matches_direct_only(self):
        result = self.run_cli(
            ["diff", DEMO_LOCK, NEW_LOCK, "--direct", "--reachable"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), EXPECTED_DIRECT)

    def test_omitting_flag_keeps_full_comparison(self):
        result = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), EXPECTED_FULL)

    def test_argument_order_determines_direction(self):
        result = self.run_cli(["diff", NEW_LOCK, DEMO_LOCK, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"name": "alpha", "change": "added", "before": None, "after": "1.0.0"},
                {"name": "beta", "change": "removed", "before": "2.1.0", "after": None},
            ],
        )

    def test_self_comparison_is_empty(self):
        result = self.run_cli(["diff", DEMO_LOCK, DEMO_LOCK, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])


class DiffDirectSemantics(unittest.TestCase):
    """直接声明集合的确定规则与 diff 记录语义。"""

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

    def diff_cli(self, before_data, after_data, *extra):
        before = write_lockfile(self.tmp, before_data, "before.json")
        after = write_lockfile(self.tmp, after_data, "after.json")
        result = self.run_cli(["diff", before, after, *extra])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_transitive_and_unreferenced_packages_excluded(self):
        # 旧侧根只声明 a，b 仅经 a 传递引入，c 未被任何声明引用：
        # 两侧的直接声明集合都只有 a，b、c 的版本变化不产生记录。
        before = lock_data(
            {"a": "^1.0.0"},
            {
                "a": ("1.0.0", {"b": "*"}),
                "b": "1.0.0",
                "c": "1.0.0",
            },
        )
        after = lock_data(
            {"a": "~1.0.0"},
            {
                "a": ("1.0.0", {"b": "*"}),
                "b": "2.0.0",
                "c": "2.0.0",
            },
        )
        self.assertEqual(self.diff_cli(before, after, "--direct"), [])

    def test_same_version_installed_other_side_still_add_or_remove(self):
        # shared 两侧都安装且版本相同，但仅旧侧根直接声明：输出 removed。
        before = lock_data(
            {"shared": "1.0.0"},
            {"shared": "1.0.0", "other": ("1.0.0", {"shared": "*"})},
        )
        after = lock_data(
            {"other": "1.0.0"},
            {"shared": "1.0.0", "other": ("1.0.0", {"shared": "*"})},
        )
        self.assertEqual(
            self.diff_cli(before, after, "--direct"),
            [
                {"name": "other", "change": "added", "before": None, "after": "1.0.0"},
                {"name": "shared", "change": "removed", "before": "1.0.0", "after": None},
            ],
        )

    def test_both_direct_changed_only_on_version_difference(self):
        # 两侧都直接声明：版本不同输出 changed，两个版本原样保留；
        # 只改变根声明的范围文本（1.0.0 → ^1.0.0）不产生记录。
        before = lock_data({"a": "1.0.0", "b": "2.0.0"}, {"a": "1.0.0", "b": "2.0.0"})
        after = lock_data({"a": "1.1.0", "b": "^2.0.0"}, {"a": "1.1.0", "b": "2.0.0"})
        self.assertEqual(
            self.diff_cli(before, after, "--direct"),
            [{"name": "a", "change": "changed", "before": "1.0.0", "after": "1.1.0"}],
        )

    def test_scoped_and_case_sensitive_names_sorted(self):
        # 作用域包作为完整名称参与，区分大小写，按 Unicode 码点升序。
        before = lock_data(
            {"@scope/tool": "1.0.0", "Beta": "1.0.0"},
            {"@scope/tool": "1.0.0", "Beta": "1.0.0", "beta": "9.0.0"},
        )
        after = lock_data(
            {"@scope/tool": "2.0.0", "beta": "1.0.0"},
            {"@scope/tool": "2.0.0", "Beta": "1.0.0", "beta": "1.0.0"},
        )
        self.assertEqual(
            self.diff_cli(before, after, "--direct"),
            [
                {
                    "name": "@scope/tool",
                    "change": "changed",
                    "before": "1.0.0",
                    "after": "2.0.0",
                },
                {"name": "Beta", "change": "removed", "before": "1.0.0", "after": None},
                {"name": "beta", "change": "added", "before": None, "after": "1.0.0"},
            ],
        )

    def test_both_direct_sets_empty(self):
        # 根 dependencies 为空或省略时该侧没有成员，两侧都为空输出 []。
        before = lock_data({}, {"a": "1.0.0"})
        after = {"lockfileVersion": 3, "packages": {"": {}, "node_modules/b": {"version": "2.0.0"}}}
        self.assertEqual(self.diff_cli(before, after, "--direct"), [])

    def test_one_side_empty_reports_other_side_members(self):
        # 仅一侧根 dependencies 为空：另一侧的直接声明全部按 added/removed 报告。
        empty = lock_data({}, {"a": "1.0.0"})
        direct = lock_data({"a": "1.0.0"}, {"a": "1.0.0"})
        self.assertEqual(
            self.diff_cli(empty, direct, "--direct"),
            [{"name": "a", "change": "added", "before": None, "after": "1.0.0"}],
        )
        self.assertEqual(
            self.diff_cli(direct, empty, "--direct"),
            [{"name": "a", "change": "removed", "before": "1.0.0", "after": None}],
        )

    def test_diff_items_two_argument_call_unchanged(self):
        _, before_map = load_lockfile(DEMO_LOCK)
        _, after_map = load_lockfile(NEW_LOCK)
        self.assertEqual(diff_items(before_map, after_map), EXPECTED_FULL)


class DiffDirectInputErrors(unittest.TestCase):
    """带 --direct 时任一输入违反校验规则仍整体失败。"""

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

    def test_excluded_package_with_dangling_dependency_rejected(self):
        # 未进入比较范围的 orphan 的悬空依赖同样在筛选前拒绝整份输入。
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": ("1.0.0", {"ghost": "*"})})
        path = write_lockfile(self.tmp, data, "dangling.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--direct"]))
        self.assertInputError(self.run_cli(["diff", path, self.good, "--direct"]))

    def test_excluded_package_with_invalid_version_rejected(self):
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": ""})
        path = write_lockfile(self.tmp, data, "version.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--direct"]))

    def test_missing_file_and_broken_json(self):
        missing = os.path.join(self.tmp, "missing.json")
        self.assertInputError(self.run_cli(["diff", missing, self.good, "--direct"]))
        self.assertInputError(self.run_cli(["diff", self.good, missing, "--direct"]))
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertInputError(self.run_cli(["diff", self.good, broken, "--direct"]))

    def write_surrogate_lock(self, data, name):
        # 孤立代理只能以 JSON 转义落盘：ensure_ascii 默认开启时 json.dump
        # 把孤立代理码点写成 ASCII 转义序列，文件字节仍是合法 UTF-8。
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def test_surrogate_in_compared_version_rejected(self):
        # 实际输出文本含孤立代理码点：按输入错误处理，标准输出为空。
        data = lock_data({"a": "1.0.0"}, {"a": "2.0-\ud83f"})
        path = self.write_surrogate_lock(data, "surrogate.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--direct"]))

    def test_surrogate_in_excluded_version_ignored(self):
        # 未进入比较范围的版本不参与输出检查：结果正常输出。
        data = lock_data(
            {"a": "1.0.0"},
            {"a": "1.0.0", "orphan": "2.0-\ud83f"},
        )
        path = self.write_surrogate_lock(data, "surrogate-excluded.json")
        result = self.run_cli(["diff", self.good, path, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])


if __name__ == "__main__":
    unittest.main()
