"""diff --unreachable 根不可达筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖。

固定行为：
- 验收场景：diff demo-lock.json new-lock.json --unreachable 只输出
  gamma 的 added（before 为 null、after 为 "3.0.0"）；旧侧不可达集合
  为空，新侧不可达集合为 {gamma}；alpha 的 removed 与 beta 的
  changed 都属于可达侧变化，不出现；同一文件比较返回 []；
- 两侧各自取自身全部已安装包中不属于根可达集合的条目后再比较，口径
  与 list --unreachable 完全一致：关系只取 dependencies，不解析版本
  范围，不从其他字段补边；根项目不参与；与根断开的自环和循环保留并
  正常结束；根 dependencies 省略或为空时该侧全部安装包进入比较；
- 仅新侧筛选集合有该包记为 added（before 为 null），仅旧侧有记为
  removed（after 为 null），即使两侧都安装且版本相同也如此；两侧
  筛选集合都有且版本字符串不同才记为 changed，版本原样保留；两侧都
  不可达且版本相同不输出；
- --unreachable 与 --reachable 互斥，同时出现（即使还带 --direct）
  在读取任何文件前按输入错误拒绝：退出码 2、stdout 为空、stderr 仅
  "INPUT_ERROR\\n"；仅与 --direct 组合时取交集，合法输入返回 []，
  但仍完整校验两份文件；
- 筛选不放宽整份校验：不可达包（进入比较范围）与可达包（被排除）的
  校验错误同样以 INPUT_ERROR 失败；实际结果字符串无法编码为 UTF-8
  时（孤立代理码点）同样失败，而被筛选排除的问题版本不影响结果。
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

EXPECTED_UNREACHABLE = [
    {"name": "gamma", "change": "added", "before": None, "after": "3.0.0"},
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


def unreachable_diff_via_api(before_path, after_path):
    """用公开接口复刻 diff --unreachable 的主体：各自取可达集合的补集后比较。"""
    before_root, before_map = load_lockfile(before_path)
    after_root, after_map = load_lockfile(after_path)
    before_reachable = reachable_names(before_root, before_map)
    after_reachable = reachable_names(after_root, after_map)
    before_filtered = {
        name: info for name, info in before_map.items() if name not in before_reachable
    }
    after_filtered = {
        name: info for name, info in after_map.items() if name not in after_reachable
    }
    return diff_items(before_filtered, after_filtered)


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
        self.assertEqual(
            result.stdout,
            json.dumps(EXPECTED_UNREACHABLE, ensure_ascii=False) + "\n",
        )
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))
        self.assertEqual(json.loads(result.stdout), EXPECTED_UNREACHABLE)

    def test_flag_between_positionals_also_accepted(self):
        # 选项与位置参数交错放置不影响解析结果。
        result = self.run_cli(["diff", DEMO_LOCK, "--unreachable", NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), EXPECTED_UNREACHABLE)

    def test_omitting_flag_keeps_full_comparison(self):
        result = self.run_cli(["diff", DEMO_LOCK, NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), EXPECTED_FULL)

    def test_argument_order_determines_direction(self):
        # 交换参数后 gamma 仅旧侧不可达，记为 removed。
        result = self.run_cli(["diff", NEW_LOCK, DEMO_LOCK, "--unreachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"name": "gamma", "change": "removed", "before": "3.0.0", "after": None},
            ],
        )

    def test_self_comparison_is_empty(self):
        for path in (DEMO_LOCK, NEW_LOCK):
            result = self.run_cli(["diff", path, path, "--unreachable"])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.stdout, "[]\n")
            self.assertEqual(json.loads(result.stdout), [])

    def test_acceptance_matches_public_api_composition(self):
        # CLI 结果与 reachable_names 取补集后调用 diff_items 的组合一致。
        self.assertEqual(
            unreachable_diff_via_api(DEMO_LOCK, NEW_LOCK), EXPECTED_UNREACHABLE
        )


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
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_same_version_unreachable_on_one_side_reports_add_or_remove(self):
        # gamma 两侧都安装且版本相同：旧侧不可达、新侧经 a 变为可达，
        # 仅旧侧筛选集合有 gamma，输出 removed；反向输出 added。
        before = lock_data(
            {"a": "1.0.0"},
            {"a": "1.0.0", "gamma": "3.0.0"},
        )
        after = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {"gamma": "*"}), "gamma": "3.0.0"},
        )
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [{"name": "gamma", "change": "removed", "before": "3.0.0", "after": None}],
        )
        self.assertEqual(
            self.diff_cli(after, before, "--unreachable"),
            [{"name": "gamma", "change": "added", "before": None, "after": "3.0.0"}],
        )

    def test_both_sides_unreachable_same_version_produces_no_record(self):
        # shared 两侧都不可达且版本相同：新侧给 shared 加了自环声明，
        # 成员与版本都不变，依赖声明变化不产生记录。
        before = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "shared": "1.0.0"})
        after = lock_data(
            {"a": "1.0.0"},
            {"a": "1.0.0", "shared": ("1.0.0", {"shared": "*"})},
        )
        self.assertEqual(self.diff_cli(before, after, "--unreachable"), [])

    def test_identity_flip_from_reachable_to_unreachable_reports_added(self):
        # 同一 shared@1.0.0：旧侧经 a 可达、新侧不可达，输出 added；
        # 反向输出 removed。
        before = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {"shared": "*"}), "shared": "1.0.0"},
        )
        after = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "shared": "1.0.0"})
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [{"name": "shared", "change": "added", "before": None, "after": "1.0.0"}],
        )
        self.assertEqual(
            self.diff_cli(after, before, "--unreachable"),
            [{"name": "shared", "change": "removed", "before": "1.0.0", "after": None}],
        )

    def test_both_sides_unreachable_version_difference_reports_changed(self):
        before = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": "1.0.0"})
        after = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": "2.0.0"})
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [
                {"name": "orphan", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
            ],
        )

    def test_reachable_only_changes_are_excluded(self):
        # 可达包的改版与不可达集合的成员不变时，可达侧变化全部排除：
        # 两侧可达集合都是 {a, b}（根声明 a、a 声明 b），b 改版属于
        # 可达侧；orphan 两侧都不可达且版本相同。
        before = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {"b": "*"}), "b": "1.0.0", "orphan": "1.0.0"},
        )
        after = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {"b": "*"}), "b": "2.0.0", "orphan": "1.0.0"},
        )
        self.assertEqual(self.diff_cli(before, after, "--unreachable"), [])

    def test_reachable_set_member_added_removed_is_excluded(self):
        # 可达集合自身的增删同样不进入不可达差异：新侧可达集合多出 c，
        # 少了谁都不影响；不可达成员两侧相同且版本相同。
        before = lock_data(
            {"a": "1.0.0"},
            {
                "a": ("1.0.0", {"b": "*"}),
                "b": "1.0.0",
                "orphan": "1.0.0",
            },
        )
        after = lock_data(
            {"a": "1.0.0", "c": "3.0.0"},
            {
                "a": ("1.0.0", {"b": "*"}),
                "b": "1.0.0",
                "c": "3.0.0",
                "orphan": "1.0.0",
            },
        )
        self.assertEqual(self.diff_cli(before, after, "--unreachable"), [])

    def test_disconnected_self_loop_and_cycle_are_kept(self):
        # 可达的 loop（自环）改版被排除；与根断开的 x↔y 循环保留并正常
        # 结束，两侧都不可达且版本变化，输出两条 changed。
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
            self.diff_cli(before, after, "--unreachable"),
            [
                {"name": "x", "change": "changed", "before": "1.0.0", "after": "9.0.0"},
                {"name": "y", "change": "changed", "before": "1.0.0", "after": "9.0.0"},
            ],
        )

    def test_disconnected_cycle_present_on_one_side_reports_add_remove(self):
        # 整个断开循环只在一侧安装：成员各自记一条 added/removed。
        before = lock_data(
            {"a": "1.0.0"},
            {"a": "1.0.0"},
        )
        after = lock_data(
            {"a": "1.0.0"},
            {
                "a": "1.0.0",
                "x": ("1.0.0", {"y": "*"}),
                "y": ("1.0.0", {"x": "*"}),
            },
        )
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [
                {"name": "x", "change": "added", "before": None, "after": "1.0.0"},
                {"name": "y", "change": "added", "before": None, "after": "1.0.0"},
            ],
        )
        self.assertEqual(
            self.diff_cli(after, before, "--unreachable"),
            [
                {"name": "x", "change": "removed", "before": "1.0.0", "after": None},
                {"name": "y", "change": "removed", "before": "1.0.0", "after": None},
            ],
        )

    def test_empty_or_omitted_root_dependencies_keeps_everything(self):
        # 两侧根都没有 dependencies：两侧筛选集合都是全部安装包，
        # 与不带筛选的普通 diff 等价。
        before = lock_data({}, {"a": "1.0.0", "b": "1.0.0"})
        after = lock_data({}, {"a": "2.0.0", "c": "3.0.0"})
        expected = [
            {"name": "a", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
            {"name": "b", "change": "removed", "before": "1.0.0", "after": None},
            {"name": "c", "change": "added", "before": None, "after": "3.0.0"},
        ]
        self.assertEqual(self.diff_cli(before, after, "--unreachable"), expected)

        # 根条目省略 dependencies 字段与显式空对象同口径。
        before_omitted = {
            "lockfileVersion": 3,
            "packages": {
                "": {},
                "node_modules/a": {"version": "1.0.0"},
                "node_modules/b": {"version": "1.0.0"},
            },
        }
        after_omitted = {
            "lockfileVersion": 3,
            "packages": {
                "": {},
                "node_modules/a": {"version": "2.0.0"},
                "node_modules/c": {"version": "3.0.0"},
            },
        }
        self.assertEqual(
            self.diff_cli(before_omitted, after_omitted, "--unreachable"), expected
        )

    def test_one_side_empty_root_compares_against_other_side_unreachable(self):
        # 仅旧侧根为空：旧侧全部不可达；新侧 orphan 不可达、a 可达。
        empty = lock_data({}, {"a": "1.0.0", "orphan": "1.0.0"})
        rooted = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {}), "orphan": "1.0.0"},
        )
        self.assertEqual(
            self.diff_cli(rooted, empty, "--unreachable"),
            [{"name": "a", "change": "added", "before": None, "after": "1.0.0"}],
        )
        self.assertEqual(
            self.diff_cli(empty, rooted, "--unreachable"),
            [{"name": "a", "change": "removed", "before": "1.0.0", "after": None}],
        )

    def test_scoped_and_case_sensitive_names_sorted(self):
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
                "Beta": "1.0.0",
                "beta": "2.0.0",
            },
        )
        # Beta 经 @scope/tool 可达被排除；beta 与根断开，输出其 changed。
        self.assertEqual(
            self.diff_cli(before, after, "--unreachable"),
            [
                {"name": "beta", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
            ],
        )

    def test_root_entry_only_sides_output_empty(self):
        root_only = {"lockfileVersion": 3, "packages": {"": {}}}
        populated = lock_data({}, {"a": "1.0.0"})
        self.assertEqual(self.diff_cli(root_only, root_only, "--unreachable"), [])
        self.assertEqual(
            self.diff_cli(root_only, populated, "--unreachable"),
            [{"name": "a", "change": "added", "before": None, "after": "1.0.0"}],
        )

    def test_diff_items_two_argument_call_unchanged(self):
        _, before_map = load_lockfile(DEMO_LOCK)
        _, after_map = load_lockfile(NEW_LOCK)
        self.assertEqual(diff_items(before_map, after_map), EXPECTED_FULL)


class DiffUnreachableDirectCombination(unittest.TestCase):
    """--unreachable 仅与 --direct 组合：取交集，恒为空但仍完整校验。"""

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

    def test_valid_inputs_return_empty(self):
        # 验收夹具与合成夹具都返回 []；stderr 为空、退出码 0。
        result = self.run_cli(
            ["diff", DEMO_LOCK, NEW_LOCK, "--unreachable", "--direct"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

        before = write_lockfile(
            self.tmp, lock_data({}, {"a": "1.0.0", "b": "2.0.0"}), "before.json"
        )
        after = write_lockfile(
            self.tmp,
            lock_data({"a": "*"}, {"a": "1.0.0", "b": "9.0.0"}),
            "after.json",
        )
        result = self.run_cli(["diff", before, after, "--direct", "--unreachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])

    def test_validation_still_runs_on_both_files(self):
        # 即使比较集合必为空，不可达 orphan 的悬空依赖与空版本仍使
        # 整份输入失败（两份文件、两个位置都核对）。
        good = write_lockfile(
            self.tmp, lock_data({"a": "1.0.0"}, {"a": "1.0.0"}), "good.json"
        )
        dangling = lock_data(
            {"a": "1.0.0"}, {"a": "1.0.0", "orphan": ("1.0.0", {"ghost": "*"})}
        )
        dangling_path = write_lockfile(self.tmp, dangling, "dangling.json")
        bad_version = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": ""})
        bad_version_path = write_lockfile(self.tmp, bad_version, "version.json")

        for first, second in (
            (good, dangling_path),
            (dangling_path, good),
            (good, bad_version_path),
            (bad_version_path, good),
        ):
            result = self.run_cli(
                ["diff", first, second, "--unreachable", "--direct"]
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "INPUT_ERROR\n")


class DiffUnreachableMutex(unittest.TestCase):
    """--unreachable 与 --reachable 互斥：读取文件前拒绝。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.good = write_lockfile(
            self.tmp, lock_data({"a": "1.0.0"}, {"a": "1.0.0"}), "good.json"
        )
        self.missing = os.path.join(self.tmp, "missing.json")

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

    def test_both_filters_together_rejected_for_existing_files(self):
        for flags in (
            ["--reachable", "--unreachable"],
            ["--unreachable", "--reachable"],
        ):
            self.assertInputError(
                self.run_cli(["diff", self.good, self.good, *flags])
            )

    def test_rejected_even_with_direct(self):
        self.assertInputError(
            self.run_cli(
                ["diff", self.good, self.good, "--reachable", "--unreachable", "--direct"]
            )
        )

    def test_rejected_before_reading_files(self):
        # 路径不存在也先报互斥冲突：证明拒绝发生在读取任何文件之前。
        for first, second in (
            (self.missing, self.missing),
            (self.good, self.missing),
            (self.missing, self.good),
        ):
            self.assertInputError(
                self.run_cli(
                    ["diff", first, second, "--reachable", "--unreachable"]
                )
            )
            self.assertInputError(
                self.run_cli(
                    [
                        "diff", first, second,
                        "--reachable", "--unreachable", "--direct",
                    ]
                )
            )


class DiffUnreachableInputErrors(unittest.TestCase):
    """带 --unreachable 时任一输入违反校验规则仍整体失败。"""

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

    def test_unreachable_package_with_dangling_dependency_rejected(self):
        # 进入比较范围的不可达 orphan 悬空依赖：整份输入失败。
        data = lock_data(
            {"a": "1.0.0"}, {"a": "1.0.0", "orphan": ("1.0.0", {"ghost": "*"})}
        )
        path = write_lockfile(self.tmp, data, "dangling.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--unreachable"]))
        self.assertInputError(self.run_cli(["diff", path, self.good, "--unreachable"]))

    def test_reachable_excluded_package_error_still_rejected(self):
        # 被 --unreachable 排除的可达包 a 版本为空：筛选不放宽整份校验。
        data = lock_data({"a": "1.0.0"}, {"a": "", "orphan": "1.0.0"})
        path = write_lockfile(self.tmp, data, "reachable-bad.json")
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

    def write_surrogate_lock(self, data, name):
        # 孤立代理只能以 JSON 转义落盘：ensure_ascii 默认开启时 json.dump
        # 把孤立代理码点写成 ASCII 转义序列，文件字节仍是合法 UTF-8。
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def test_surrogate_in_compared_unreachable_version_rejected(self):
        # 根为空时全部安装包不可达：含孤立代理的版本进入实际输出，按
        # 输入错误处理，标准输出为空。
        data = lock_data({}, {"a": "2.0-\ud83f"})
        path = self.write_surrogate_lock(data, "surrogate-in.json")
        self.assertInputError(self.run_cli(["diff", self.good, path, "--unreachable"]))

    def test_surrogate_in_excluded_reachable_version_ignored(self):
        # 可达包版本含孤立代理，被筛选排除；干净的不可达 orphan 正常输出。
        good_root = {"a": "1.0.0"}
        data = lock_data(
            good_root,
            {"a": ("2.0-\ud83f", {}), "orphan": "1.0.0"},
        )
        path = self.write_surrogate_lock(data, "surrogate-excluded.json")
        good_path = write_lockfile(
            self.tmp,
            lock_data(good_root, {"a": "1.0.0", "orphan": "1.0.0"}),
            "good2.json",
        )
        result = self.run_cli(["diff", good_path, path, "--unreachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), [])


if __name__ == "__main__":
    unittest.main()
