"""diff --direct 根直接声明筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖。

固定的既有行为：
- 验收场景：diff demo-lock.json new-lock.json --direct 只输出
  alpha@1.0.0 的 removed 与 beta@2.1.0 的 added，gamma 不出现；
- 每侧成员取该侧根节点 dependencies 直接声明的已安装包，版本取安装
  条目的原始字符串，不解析声明范围；根项目、仅被其他包引入的传递
  依赖和未引用的包不进入该侧范围，其他字段不参与成员筛选；
- 仅新侧直接声明输出 added（before 为 null），仅旧侧直接声明输出
  removed（after 为 null），即使另一侧仍安装相同版本也如此；两侧
  均直接声明时仅版本字符串不同输出 changed，只改变根声明的范围
  文本不产生记录；
- 根 dependencies 省略或为空时该侧没有成员，两侧都为空或同一文件
  比较时输出 []；--direct 与 --reachable 同用时结果与只用 --direct
  一致；
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

    def test_direct_with_reachable_matches_direct_alone(self):
        both = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK, "--direct", "--reachable"])
        self.assertEqual(both.returncode, 0)
        self.assertEqual(both.stderr, "")
        self.assertEqual(json.loads(both.stdout), EXPECTED_DIRECT)

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
    """根直接声明成员筛选与 diff 记录语义。"""

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
        # beta 仅经 alpha 传递引入，gamma 未被引用：都不进入两侧范围。
        before = lock_data(
            {"alpha": "1.0.0"},
            {
                "alpha": ("1.0.0", {"beta": "*"}),
                "beta": "2.0.0",
                "gamma": "3.0.0",
            },
        )
        after = lock_data(
            {"alpha": "1.0.0"},
            {
                "alpha": ("1.0.0", {"beta": "*"}),
                "beta": "9.9.9",
                "gamma": "8.8.8",
            },
        )
        self.assertEqual(self.diff_cli(before, after, "--direct"), [])

    def test_installed_on_other_side_still_added_or_removed(self):
        # shared 两侧都安装且版本相同，但仅旧侧直接声明：仍输出 removed。
        before = lock_data({"shared": "1.0.0"}, {"shared": "1.0.0"})
        after = lock_data({}, {"shared": "1.0.0"})
        self.assertEqual(
            self.diff_cli(before, after, "--direct"),
            [{"name": "shared", "change": "removed", "before": "1.0.0", "after": None}],
        )
        # 方向反转后同一情形输出 added。
        self.assertEqual(
            self.diff_cli(after, before, "--direct"),
            [{"name": "shared", "change": "added", "before": None, "after": "1.0.0"}],
        )

    def test_changed_only_when_installed_versions_differ(self):
        before = lock_data({"a": "^1.0.0"}, {"a": "1.0.0"})
        after = lock_data({"a": "^2.0.0"}, {"a": "2.0.0"})
        self.assertEqual(
            self.diff_cli(before, after, "--direct"),
            [
                {
                    "name": "a",
                    "change": "changed",
                    "before": "1.0.0",
                    "after": "2.0.0",
                }
            ],
        )

    def test_range_text_change_alone_produces_no_records(self):
        # 只改变根声明的范围文本、安装版本不变时不产生记录。
        before = lock_data({"a": "^1.0.0"}, {"a": "1.0.0"})
        after = lock_data({"a": "~1.0.0"}, {"a": "1.0.0"})
        self.assertEqual(self.diff_cli(before, after, "--direct"), [])

    def test_version_taken_from_installed_entry_not_declared_range(self):
        # 声明范围与安装版本字符串不同：记录取安装条目的原始字符串。
        before = lock_data({"a": "^1.0.0"}, {"a": "1.2.3"})
        after = lock_data({}, {"a": "1.2.3"})
        self.assertEqual(
            self.diff_cli(before, after, "--direct"),
            [{"name": "a", "change": "removed", "before": "1.2.3", "after": None}],
        )

    def test_scoped_and_case_sensitive_names_sorted_by_codepoint(self):
        before = lock_data(
            {"@scope/pkg": "1.0.0", "Beta": "1.0.0", "alpha": "1.0.0"},
            {"@scope/pkg": "1.0.0", "Beta": "1.0.0", "alpha": "1.0.0"},
        )
        after = lock_data(
            {"@scope/pkg": "1.0.0", "Beta": "1.0.0", "alpha": "1.0.0"},
            {"@scope/pkg": "2.0.0", "Beta": "2.0.0", "alpha": "2.0.0"},
        )
        records = self.diff_cli(before, after, "--direct")
        self.assertEqual(
            [r["name"] for r in records],
            ["@scope/pkg", "Beta", "alpha"],
        )
        self.assertTrue(all(r["change"] == "changed" for r in records))

    def test_both_root_dependencies_empty_or_omitted(self):
        # 两侧根 dependencies 为空或省略时输出 []，即使安装了包。
        before = lock_data({}, {"a": "1.0.0"})
        after = lock_data({}, {"b": "2.0.0"})
        del after["packages"][""]["dependencies"]
        self.assertEqual(self.diff_cli(before, after, "--direct"), [])

    def test_one_side_empty_marks_other_side_members(self):
        before = lock_data({}, {"a": "1.0.0"})
        after = lock_data({"a": "1.0.0"}, {"a": "1.0.0"})
        self.assertEqual(
            self.diff_cli(before, after, "--direct"),
            [{"name": "a", "change": "added", "before": None, "after": "1.0.0"}],
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

    def test_non_member_with_invalid_version_rejected(self):
        # 未进入比较范围的 orphan 版本无效同样使整份输入失败。
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": ""})
        path = write_lockfile(self.tmp, data, "version.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--direct"]))
        self.assertInputError(self.run_cli(["diff", path, self.good, "--direct"]))

    def test_non_member_with_dangling_dependency_rejected(self):
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": ("1.0.0", {"ghost": "*"})})
        path = write_lockfile(self.tmp, data, "dangling.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--direct"]))

    def test_missing_file_and_broken_json(self):
        missing = os.path.join(self.tmp, "missing.json")
        self.assertInputError(self.run_cli(["diff", missing, self.good, "--direct"]))
        self.assertInputError(self.run_cli(["diff", self.good, missing, "--direct"]))
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertInputError(self.run_cli(["diff", self.good, broken, "--direct"]))

    def test_surrogate_in_output_version_rejected(self):
        # 实际输出的版本字符串含孤立代理码点：按输入错误处理。
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0"})
        raw = json.dumps(data).replace(
            '"version": "1.0.0"', '"version": "2.0-\\ud83f"'
        )
        path = os.path.join(self.tmp, "surrogate.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw)
        self.assertInputError(self.run_cli(["diff", self.good, path, "--direct"]))

    def test_surrogate_in_non_member_version_ignored(self):
        # 未进入比较范围的条目版本含孤立代理：不参与输出检查，照常成功。
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": "9.9.9"})
        raw = json.dumps(data).replace('"9.9.9"', '"2.0-\\ud83f"')
        path = os.path.join(self.tmp, "surrogate-orphan.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw)
        result = self.run_cli(["diff", self.good, path, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])


if __name__ == "__main__":
    unittest.main()
