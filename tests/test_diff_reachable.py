"""diff --reachable 根可达筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖。

固定的既有行为：
- 验收场景：diff demo-lock.json new-lock.json --reachable 只输出
  alpha 的 removed 与 beta 的 changed（2.0.0 → 2.1.0），与根断开的
  gamma 不参与；省略 --reachable 时仍输出含 gamma added 的三条记录；
- 两侧各自从根节点 dependencies 出发沿包条目 dependencies 确定可达
  集合，连边只看区分大小写的完整包名（含 @scope/name），不解析版本
  范围；多条路径、自环与循环正常结束且不重复，与根断开的包和循环
  整体排除，根项目不参与比较；
- 仅新侧可达输出 added（before 为 null），仅旧侧可达输出 removed
  （after 为 null），即使包两侧都安装且版本相同也如此；两侧都可达
  时仅版本字符串不同输出 changed，身份变化不产生记录；
- 省略 --reachable 时 diff_items 两参数调用语义不变；
- 筛选不放宽整份校验：不可达包的校验错误同样以 INPUT_ERROR 失败。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import diff_items, load_lockfile, reachable_names

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")

EXPECTED_REACHABLE = [
    {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
    {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"},
]

EXPECTED_FULL = EXPECTED_REACHABLE + [
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


class DiffReachableAcceptance(unittest.TestCase):
    """验收场景：demo-lock.json 为旧清单，new-lock.json 为新清单。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_acceptance_scenario_cli(self):
        result = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout), EXPECTED_REACHABLE)

    def test_omitting_flag_keeps_full_comparison(self):
        result = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), EXPECTED_FULL)

    def test_argument_order_determines_direction(self):
        # 交换参数后旧侧可达的 beta@2.1.0 与新侧可达的 alpha、beta@2.0.0 比较。
        result = self.run_cli(["diff", NEW_LOCK, DEMO_LOCK, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"name": "alpha", "change": "added", "before": None, "after": "1.0.0"},
                {"name": "beta", "change": "changed", "before": "2.1.0", "after": "2.0.0"},
            ],
        )

    def test_self_comparison_is_empty(self):
        result = self.run_cli(["diff", DEMO_LOCK, DEMO_LOCK, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])


class DiffReachableSemantics(unittest.TestCase):
    """可达集合的确定规则与 diff 记录语义。"""

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

    def test_installed_both_sides_but_reachable_on_one_side(self):
        # shared 两侧都安装且版本相同：仅旧侧可达输出 removed，
        # 仅新侧可达输出 added。
        before = lock_data(
            {"old-only": "1.0.0"},
            {
                "old-only": "1.0.0",
                "shared": ("1.0.0", {"old-only": "*"}),
            },
        )
        after = lock_data(
            {"new-only": "1.0.0"},
            {
                "new-only": "1.0.0",
                "shared": ("1.0.0", {"new-only": "*"}),
            },
        )
        # shared 两侧都不可达，不出现；old-only 仅旧侧可达，new-only 仅新侧可达。
        self.assertEqual(
            self.diff_cli(before, after, "--reachable"),
            [
                {"name": "new-only", "change": "added", "before": None, "after": "1.0.0"},
                {"name": "old-only", "change": "removed", "before": "1.0.0", "after": None},
            ],
        )
        # 省略选项时 shared 两侧版本相同仍不输出，其余同上。
        self.assertEqual(
            self.diff_cli(before, after),
            [
                {"name": "new-only", "change": "added", "before": None, "after": "1.0.0"},
                {"name": "old-only", "change": "removed", "before": "1.0.0", "after": None},
            ],
        )

    def test_same_version_one_side_reachable_reports_add_or_remove(self):
        # gamma 两侧都安装且版本相同，但仅新侧可达：输出 added。
        before = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "gamma": "3.0.0"})
        after = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {"gamma": "*"}), "gamma": "3.0.0"},
        )
        self.assertEqual(
            self.diff_cli(before, after, "--reachable"),
            [{"name": "gamma", "change": "added", "before": None, "after": "3.0.0"}],
        )
        # 方向反转后同一情形输出 removed。
        self.assertEqual(
            self.diff_cli(after, before, "--reachable"),
            [{"name": "gamma", "change": "removed", "before": "3.0.0", "after": None}],
        )

    def test_reachable_identity_changes_produce_no_records(self):
        # 可达性与版本都不变时，直接/传递身份与依赖声明变化不产生记录。
        before = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {"b": "*"}), "b": "2.0.0"},
        )
        after = lock_data(
            {"b": "2.0.0"},
            {"a": "1.0.0", "b": ("2.0.0", {"a": "*"})},
        )
        self.assertEqual(self.diff_cli(before, after, "--reachable"), [])

    def test_disconnected_cycle_excluded_reachable_cycle_kept(self):
        # 与根断开的循环（x↔y）整体排除；可达的自环（loop）正常结束并参与。
        before = lock_data(
            {"loop": "1.0.0"},
            {
                "loop": ("1.0.0", {"loop": "*"}),
                "x": ("1.0.0", {"y": "*"}),
                "y": ("1.0.0", {"x": "*"}),
            },
        )
        after = lock_data(
            {"loop": "1.0.0"},
            {
                "loop": ("2.0.0", {"loop": "*"}),
                "x": ("9.0.0", {"y": "*"}),
                "y": ("9.0.0", {"x": "*"}),
            },
        )
        self.assertEqual(
            self.diff_cli(before, after, "--reachable"),
            [
                {"name": "loop", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
            ],
        )

    def test_scoped_and_case_sensitive_names(self):
        # 连边只看区分大小写的完整包名；作用域包同样处理。
        before = lock_data(
            {"@scope/tool": "1.0.0"},
            {
                "@scope/tool": ("1.0.0", {"Beta": "*"}),
                "Beta": "1.0.0",
                "beta": "1.0.0",
            },
        )
        after = lock_data(
            {"@scope/tool": "1.0.0"},
            {
                "@scope/tool": ("1.0.0", {"Beta": "*"}),
                "Beta": "2.0.0",
                "beta": "2.0.0",
            },
        )
        # Beta 经 @scope/tool 可达，beta 与根断开不参与。
        self.assertEqual(
            self.diff_cli(before, after, "--reachable"),
            [
                {"name": "Beta", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
            ],
        )

    def test_both_reachable_sets_empty(self):
        # 根 dependencies 为空时两侧可达集合均为空，即使安装了包也输出 []。
        before = lock_data({}, {"a": "1.0.0"})
        after = lock_data({}, {"b": "2.0.0"})
        self.assertEqual(self.diff_cli(before, after, "--reachable"), [])

    def test_diff_items_two_argument_call_unchanged(self):
        _, before_map = load_lockfile(DEMO_LOCK)
        _, after_map = load_lockfile(NEW_LOCK)
        self.assertEqual(diff_items(before_map, after_map), EXPECTED_FULL)

    def test_reachable_names_api(self):
        root_deps, packages_map = load_lockfile(NEW_LOCK)
        self.assertEqual(reachable_names(root_deps, packages_map), {"beta"})


class DiffReachableInputErrors(unittest.TestCase):
    """带 --reachable 时任一输入违反校验规则仍整体失败。"""

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

    def test_unreachable_package_with_dangling_dependency_rejected(self):
        # 不可达 orphan 的悬空依赖同样在筛选前拒绝整份输入。
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": ("1.0.0", {"ghost": "*"})})
        path = write_lockfile(self.tmp, data, "dangling.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--reachable"]))
        self.assertInputError(self.run_cli(["diff", path, self.good, "--reachable"]))

    def test_unreachable_package_with_invalid_version_rejected(self):
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": ""})
        path = write_lockfile(self.tmp, data, "version.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--reachable"]))

    def test_missing_file_and_broken_json(self):
        missing = os.path.join(self.tmp, "missing.json")
        self.assertInputError(self.run_cli(["diff", missing, self.good, "--reachable"]))
        self.assertInputError(self.run_cli(["diff", self.good, missing, "--reachable"]))
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertInputError(self.run_cli(["diff", self.good, broken, "--reachable"]))


if __name__ == "__main__":
    unittest.main()
