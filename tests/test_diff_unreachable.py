"""diff --unreachable 根不可达筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖。

固定的既有行为：
- 验收场景：diff demo-lock.json new-lock.json --unreachable 只输出
  gamma 的 added（before 为 null，after 为 "3.0.0"）；同一文件比较
  返回 []；
- 两侧各自排除从自身根节点沿 dependencies 可达的包后再比较，根项目
  不参与；连边只看区分大小写的完整包名（含 @scope/name），不解析版本
  范围；与根断开的自环和循环保留并正常结束；根 dependencies 省略或为
  空时该侧全部安装包进入比较；
- 仅新侧不可达集合有的包记 added（before 为 null），仅旧侧有的记
  removed（after 为 null），即使包两侧都安装且版本相同也如此；两侧
  均不可达时仅版本字符串不同才记 changed；两侧均不可达且版本相同不
  输出；
- --unreachable 与 --reachable 互斥，同时出现（即使还带 --direct）
  在读取文件前以 INPUT_ERROR 拒绝；仅与 --direct 组合时取交集，合法
  输入返回 []，两份文件仍完整校验；
- 筛选不放宽整份校验：可达包与不可达包的校验错误同样以 INPUT_ERROR
  失败；实际结果字符串无法编码为 UTF-8 时同样以 INPUT_ERROR 失败。
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

EXPECTED_UNREACHABLE = [
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


class DiffUnreachableAcceptance(unittest.TestCase):
    """验收场景：demo-lock.json 为旧清单，new-lock.json 为新清单。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_acceptance_scenario_cli(self):
        result = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK, "--unreachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout), EXPECTED_UNREACHABLE)

    def test_argument_order_determines_direction(self):
        # 交换参数后 gamma 仅旧侧（原新侧）不可达：added 变为 removed。
        result = self.run_cli(["diff", NEW_LOCK, DEMO_LOCK, "--unreachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [{"name": "gamma", "change": "removed", "before": "3.0.0", "after": None}],
        )

    def test_self_comparison_is_empty(self):
        result = self.run_cli(["diff", DEMO_LOCK, DEMO_LOCK, "--unreachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])


class DiffUnreachableSemantics(unittest.TestCase):
    """不可达集合的确定规则与 diff 记录语义。"""

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

    def test_same_version_reachability_change_reports_add_or_remove(self):
        # gamma 两侧都安装且版本相同：旧侧可达、新侧不可达 → added；反向 → removed。
        before = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {"gamma": "*"}), "gamma": "3.0.0"},
        )
        after = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "gamma": "3.0.0"})
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [{"name": "gamma", "change": "added", "before": None, "after": "3.0.0"}],
        )
        self.assertEqual(
            self.diff_cli(after, before, "--unreachable"),
            [{"name": "gamma", "change": "removed", "before": "3.0.0", "after": None}],
        )

    def test_both_unreachable_same_version_produces_no_record(self):
        # shared 两侧均不可达且版本相同：不输出；仅一侧不可达的包各自记录。
        before = lock_data(
            {"old-only": "1.0.0"},
            {
                "old-only": "1.0.0",
                "shared": ("1.0.0", {"old-only": "*"}),
                "orphan": "1.0.0",
            },
        )
        after = lock_data(
            {"new-only": "1.0.0"},
            {
                "new-only": "1.0.0",
                "shared": ("1.0.0", {"new-only": "*"}),
                "orphan": "1.0.0",
            },
        )
        # shared 两侧都可达，orphan 两侧都不可达且版本相同，均不出现。
        self.assertEqual(self.diff_cli(before, after, "--unreachable"), [])

    def test_both_unreachable_different_version_reports_changed(self):
        before = lock_data({}, {"orphan": "1.0.0"})
        after = lock_data({}, {"orphan": "2.0.0"})
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [
                {
                    "name": "orphan",
                    "change": "changed",
                    "before": "1.0.0",
                    "after": "2.0.0",
                }
            ],
        )

    def test_empty_root_deps_includes_all_installed_packages(self):
        # 根 dependencies 省略或为空时，该侧全部安装包进入比较。
        before = lock_data({}, {"a": "1.0.0", "b": "1.0.0"})
        after = lock_data({}, {"b": "2.0.0", "c": "1.0.0"})
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [
                {"name": "a", "change": "removed", "before": "1.0.0", "after": None},
                {"name": "b", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
                {"name": "c", "change": "added", "before": None, "after": "1.0.0"},
            ],
        )

    def test_disconnected_self_loop_and_cycle_kept(self):
        # 与根断开的自环（loop）与循环（x↔y）保留并正常结束；可达包不参与。
        before = lock_data(
            {"rooted": "1.0.0"},
            {
                "rooted": "1.0.0",
                "loop": ("1.0.0", {"loop": "*"}),
                "x": ("1.0.0", {"y": "*"}),
                "y": ("1.0.0", {"x": "*"}),
            },
        )
        after = lock_data(
            {"rooted": "1.0.0"},
            {
                "rooted": "1.0.0",
                "loop": ("2.0.0", {"loop": "*"}),
                "x": ("1.0.0", {"y": "*"}),
                "y": ("3.0.0", {"x": "*"}),
            },
        )
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [
                {"name": "loop", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
                {"name": "y", "change": "changed", "before": "1.0.0", "after": "3.0.0"},
            ],
        )

    def test_unreachable_package_declaring_reachable_one_stays_unreachable(self):
        # 不可达包声明了可达包，不会因此被视为可达；关系方向只从声明者到依赖包。
        before = lock_data(
            {"a": "1.0.0"},
            {"a": "1.0.0", "orphan": ("1.0.0", {"a": "*"})},
        )
        after = lock_data(
            {"a": "1.0.0"},
            {"a": "1.0.0", "orphan": ("2.0.0", {"a": "*"})},
        )
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [
                {
                    "name": "orphan",
                    "change": "changed",
                    "before": "1.0.0",
                    "after": "2.0.0",
                }
            ],
        )

    def test_scoped_and_case_sensitive_names(self):
        # 连边只看区分大小写的完整包名；作用域包作为整体参与排序与比较。
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
                "Beta": "1.0.0",
                "beta": "2.0.0",
            },
        )
        # Beta 经 @scope/tool 可达不参与；beta 与根断开，版本不同记 changed。
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [
                {"name": "beta", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
            ],
        )

    def test_diff_items_two_argument_call_unchanged(self):
        # 公开接口保持原行为：筛选由调用方完成，diff_items 语义不变。
        _, before_map = load_lockfile(DEMO_LOCK)
        _, after_map = load_lockfile(NEW_LOCK)
        self.assertEqual(
            diff_items(before_map, after_map),
            [
                {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
                {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"},
                {"name": "gamma", "change": "added", "before": None, "after": "3.0.0"},
            ],
        )


class DiffUnreachableOptionCombinations(unittest.TestCase):
    """--unreachable 与 --reachable/--direct 的组合规则。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.good = write_lockfile(
            self.tmp,
            lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": "1.0.0"}),
            "good.json",
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

    def test_direct_and_unreachable_intersection_is_empty(self):
        # 直接声明的包必然可达，交集为空；合法输入返回 []。
        result = self.run_cli(["diff", self.good, self.good, "--direct", "--unreachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_direct_and_unreachable_still_validates_both_files(self):
        # 交集虽恒为空，两份文件仍在加载时完整校验。
        dangling = write_lockfile(
            self.tmp,
            lock_data({"a": "1.0.0"}, {"a": ("1.0.0", {"ghost": "*"})}),
            "dangling.json",
        )
        self.assertInputError(
            self.run_cli(["diff", self.good, dangling, "--direct", "--unreachable"])
        )
        self.assertInputError(
            self.run_cli(["diff", dangling, self.good, "--direct", "--unreachable"])
        )

    def test_reachable_unreachable_mutex_rejected_before_reading_files(self):
        missing = os.path.join(self.tmp, "missing.json")
        # 文件不存在也先因互斥拒绝：结果与校验失败完全相同。
        self.assertInputError(
            self.run_cli(["diff", missing, missing, "--reachable", "--unreachable"])
        )
        self.assertInputError(
            self.run_cli(["diff", self.good, self.good, "--reachable", "--unreachable"])
        )

    def test_mutex_holds_even_with_direct(self):
        self.assertInputError(
            self.run_cli(
                ["diff", self.good, self.good, "--reachable", "--unreachable", "--direct"]
            )
        )


class DiffUnreachableInputErrors(unittest.TestCase):
    """带 --unreachable 时任一输入违反校验规则仍整体失败。"""

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

    def test_reachable_package_with_dangling_dependency_rejected(self):
        # 筛选不放宽校验：可达包的悬空依赖同样使整份输入失败。
        data = lock_data({"a": "1.0.0"}, {"a": ("1.0.0", {"ghost": "*"})})
        path = write_lockfile(self.tmp, data, "dangling.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--unreachable"]))
        self.assertInputError(self.run_cli(["diff", path, self.good, "--unreachable"]))

    def test_unreachable_package_with_invalid_version_rejected(self):
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": ""})
        path = write_lockfile(self.tmp, data, "version.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--unreachable"]))

    def test_missing_file_and_broken_json(self):
        missing = os.path.join(self.tmp, "missing.json")
        self.assertInputError(self.run_cli(["diff", missing, self.good, "--unreachable"]))
        self.assertInputError(self.run_cli(["diff", self.good, missing, "--unreachable"]))
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertInputError(self.run_cli(["diff", self.good, broken, "--unreachable"]))

    def test_unencodable_result_version_rejected(self):
        # 不可达包的版本含孤立代理码点：实际结果字符串无法编码为 UTF-8，
        # 以 INPUT_ERROR 失败且标准输出为空。
        path = os.path.join(self.tmp, "surrogate.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                '{"lockfileVersion": 3, "packages": {'
                '"": {},'
                '"node_modules/orphan": {"version": "2.0-\\ud83f"}'
                "}}"
            )
        self.assertInputError(self.run_cli(["diff", self.good, path, "--unreachable"]))


if __name__ == "__main__":
    unittest.main()
