"""diff --reachable 根可达筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改 demo-lock.json，不安装或执行锁文件中的依赖。固定的行为：

- 验收场景：demo-lock.json 为旧、new-lock.json 为新，--reachable 只输出
  alpha 的 removed 与 beta 的 changed（2.0.0→2.1.0），与根断开的 gamma
  被排除；省略选项时仍是含 gamma added 的三条记录；
- 两侧各自独立求可达集合：仅一侧可达的包即使另一侧也安装且版本相同，
  仍输出 added/removed（对应 before/after 为 null）；
- 直接/传递身份变化但名称与版本不变不产生记录；与根断开的包及其循环
  整体排除；可达的自环与循环正常参与比较；
- 连边按区分大小写的完整包名匹配，作用域包同样处理，结果按 Unicode
  码点排序，每包最多一条；同文件自比或两侧可达集合均为空时输出 []；
- 筛选保留整份输入校验：不可达包的错误（版本缺失、悬空依赖等）同样
  导致退出码 2、stdout 为空、stderr 仅 "INPUT_ERROR\\n"。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import load_lockfile, reachable_diff_items

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


class DiffReachableAcceptance(unittest.TestCase):
    """验收场景与 API 一致性。"""

    EXPECTED = [
        {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
        {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"},
    ]

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assertDiffArray(self, stdout, expected):
        self.assertTrue(stdout.endswith("\n"))
        parsed = json.loads(stdout)
        self.assertIsInstance(parsed, list)
        for record in parsed:
            self.assertEqual(set(record), {"name", "change", "before", "after"})
        self.assertEqual(parsed, expected)

    def test_acceptance_scenario_cli(self):
        result = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertDiffArray(result.stdout, self.EXPECTED)

    def test_acceptance_scenario_api(self):
        before_root, before_map = load_lockfile(DEMO_LOCK)
        after_root, after_map = load_lockfile(NEW_LOCK)
        self.assertEqual(
            reachable_diff_items(before_root, before_map, after_root, after_map),
            self.EXPECTED,
        )

    def test_flag_before_paths_also_accepted(self):
        result = self.run_cli(["diff", "--reachable", DEMO_LOCK, NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertDiffArray(result.stdout, self.EXPECTED)

    def test_reversed_direction(self):
        # 旧新互换：alpha 在新侧不可达、旧侧可达 → added；beta 前后版本互换；
        # gamma 两侧都不可达，仍不出现。
        result = self.run_cli(["diff", NEW_LOCK, DEMO_LOCK, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertDiffArray(
            result.stdout,
            [
                {"name": "alpha", "change": "added", "before": None, "after": "1.0.0"},
                {"name": "beta", "change": "changed", "before": "2.1.0", "after": "2.0.0"},
            ],
        )

    def test_without_flag_keeps_three_records(self):
        # 省略 --reachable 时 gamma（仅安装、不可达）的 added 仍在。
        result = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertDiffArray(
            result.stdout,
            self.EXPECTED
            + [{"name": "gamma", "change": "added", "before": None, "after": "3.0.0"}],
        )

    def test_self_comparison_is_empty(self):
        result = self.run_cli(["diff", DEMO_LOCK, DEMO_LOCK, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertDiffArray(result.stdout, [])


class DiffReachableSemantics(unittest.TestCase):
    """单侧可达、身份变化、循环、大小写、作用域包与排序语义。"""

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
        result = self.run_cli(["diff", before, after, "--reachable", *extra])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        parsed = json.loads(result.stdout)
        self.assertTrue(result.stdout.endswith("\n"))
        for record in parsed:
            self.assertEqual(set(record), {"name", "change", "before", "after"})
        return parsed

    def test_reachable_one_side_with_same_version_still_recorded(self):
        # b 两侧都安装且版本相同，但只在新侧可达 → added（before 为 null）。
        before = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "b": "1.0.0"})
        after = lock_data(
            {"a": "1.0.0", "b": "1.0.0"}, {"a": "1.0.0", "b": "1.0.0"}
        )
        self.assertEqual(
            self.diff_cli(before, after),
            [{"name": "b", "change": "added", "before": None, "after": "1.0.0"}],
        )
        # 反向：只在旧侧可达 → removed（after 为 null）。
        self.assertEqual(
            self.diff_cli(after, before),
            [{"name": "b", "change": "removed", "before": "1.0.0", "after": None}],
        )

    def test_direct_transitive_identity_change_is_silent(self):
        # 两侧 a、b 都可达且版本相同；b 从根直接依赖变为 a 的传递依赖，
        # 直接/传递身份变化不产生记录。
        before = lock_data({"a": "1.0.0", "b": "1.0.0"}, {"a": "1.0.0", "b": "1.0.0"})
        after = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {"b": "1.0.0"}), "b": "1.0.0"},
        )
        self.assertEqual(self.diff_cli(before, after), [])

    def test_disconnected_packages_and_cycles_excluded(self):
        # orphan↔zeta 构成与根断开的循环，两侧版本变化也不产生记录。
        before = lock_data(
            {"a": "1.0.0"},
            {
                "a": "1.0.0",
                "orphan": ("1.0.0", {"zeta": "*"}),
                "zeta": ("1.0.0", {"orphan": "*"}),
            },
        )
        after = lock_data(
            {"a": "1.0.0"},
            {
                "a": "1.0.0",
                "orphan": ("9.9.9", {"zeta": "*"}),
                "zeta": ("9.9.9", {"orphan": "*"}),
            },
        )
        self.assertEqual(self.diff_cli(before, after), [])

    def test_reachable_self_loop_and_cycle_participate(self):
        # a↔b 循环且 b 自环，根→a，循环节点都可达；b 版本变化输出 changed。
        before = lock_data(
            {"a": "1.0.0"},
            {
                "a": ("1.0.0", {"b": "*"}),
                "b": ("2.0.0", {"a": "*", "b": "*"}),
            },
        )
        after = lock_data(
            {"a": "1.0.0"},
            {
                "a": ("1.0.0", {"b": "*"}),
                "b": ("2.1.0", {"a": "*", "b": "*"}),
            },
        )
        self.assertEqual(
            self.diff_cli(before, after),
            [
                {
                    "name": "b",
                    "change": "changed",
                    "before": "2.0.0",
                    "after": "2.1.0",
                }
            ],
        )

    def test_case_sensitive_and_scoped_sorted_by_codepoint(self):
        # 大写 Z 与小写 z 视为不同包；@scope/x 按码点排在最前；
        # 不可达的 gamma 不参与。
        before = lock_data(
            {"@scope/x": "1.0.0", "Z": "1.0.0"},
            {"@scope/x": "1.0.0", "Z": "1.0.0", "gamma": "1.0.0"},
        )
        after = lock_data(
            {"@scope/x": "2.0.0", "z": "1.0.0"},
            {"@scope/x": "2.0.0", "Z": "1.0.0", "z": "1.0.0", "gamma": "9.9.9"},
        )
        self.assertEqual(
            self.diff_cli(before, after),
            [
                {"name": "@scope/x", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
                {"name": "Z", "change": "removed", "before": "1.0.0", "after": None},
                {"name": "z", "change": "added", "before": None, "after": "1.0.0"},
            ],
        )

    def test_both_reachable_sets_empty_outputs_empty_array(self):
        # 根无依赖：即使两侧装有互不相干的包且版本不同，可达集合都为空。
        before = lock_data({}, {"a": "1.0.0"})
        after = lock_data({}, {"a": "2.0.0", "b": "3.0.0"})
        records = self.diff_cli(before, after)
        self.assertEqual(records, [])
        self.assertEqual(json.loads(json.dumps(records)), [])

    def test_version_strings_kept_verbatim(self):
        before = lock_data({"a": "1.0.0"}, {"a": "2.0.0"})
        after = lock_data({"a": "1.0.0"}, {"a": "1.0.0-beta.1"})
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


class DiffReachableInputErrors(unittest.TestCase):
    """--reachable 不放松校验：不可达包的错误同样整体失败。"""

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

    def test_unreachable_package_missing_version_rejected(self):
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "b": ""})
        path = write_lockfile(self.tmp, data, "bad.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--reachable"]))
        self.assertInputError(self.run_cli(["diff", path, self.good, "--reachable"]))

    def test_dangling_dependency_from_unreachable_package_rejected(self):
        # orphan 不可达，但它声明了未安装的 ghost：整份输入仍被拒绝。
        data = lock_data(
            {"a": "1.0.0"},
            {"a": "1.0.0", "orphan": ("1.0.0", {"ghost": "*"})},
        )
        path = write_lockfile(self.tmp, data, "dangling.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--reachable"]))

    def test_broken_json_rejected_with_reachable(self):
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertInputError(self.run_cli(["diff", broken, self.good, "--reachable"]))

    def test_missing_file_rejected_with_reachable(self):
        missing = os.path.join(self.tmp, "missing.json")
        self.assertInputError(self.run_cli(["diff", missing, self.good, "--reachable"]))


if __name__ == "__main__":
    unittest.main()
