"""ancestors --reachable 根可达筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖。

固定的既有行为：
- 验收场景：sample-lock.json 中根仅声明 alpha，alpha 经 beta 到达 leaf，
  不可达的 orphan 也声明 leaf；不加选项时 ancestors 为
  ["alpha", "beta", "orphan"]，加 --reachable 后为 ["alpha", "beta"]，
  两次 name 均为 leaf；
- --reachable 只筛选 ancestors 成员：每个保留的成员既沿自身
  dependencies 经过至少一条边到达目标，又能自根节点沿 dependencies
  到达；name 仍原样保留查询名，不受筛选影响；
- 可达性与 list --reachable 一致：从根 dependencies 出发逐层沿
  dependencies 判断，不解析版本范围，不从其他字段补边；可达自环与循环
  正常结束，根项目与目标自身始终排除；
- 已安装但根不可达的目标，或根 dependencies 省略、为空时，成功返回空
  ancestors；
- 省略 --reachable 时 find_ancestors 与 ancestors 命令行为不变；
- 筛选不放宽整份校验：不可达包的校验错误同样以 INPUT_ERROR 失败。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import find_ancestors, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_LOCK = os.path.join(REPO_ROOT, "sample-lock.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def graph_lock_data():
    """可达/不可达混合：传递链、循环、自环、字面 $root 包与 scoped 包。

    根声明 alpha、loopy；alpha 经 beta 到达 leaf，charlie 与 alpha 互相
    声明构成可达循环；orphan 自环并声明 leaf，outer 声明 orphan（不可达
    传递上游）；$root 包声明 leaf；@scope/pkg 可达并声明 leaf。
    """
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {
                "dependencies": {
                    "alpha": "1.0.0",
                    "loopy": "1.0.0",
                }
            },
            "node_modules/alpha": {
                "version": "1.0.0",
                "dependencies": {"beta": "1.0.0", "charlie": "1.0.0"},
            },
            "node_modules/beta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0", "@scope/pkg": "1.0.0"},
            },
            "node_modules/charlie": {
                "version": "1.0.0",
                "dependencies": {"alpha": "1.0.0", "leaf": "1.0.0"},
            },
            "node_modules/leaf": {"version": "1.0.0"},
            "node_modules/loopy": {
                "version": "1.0.0",
                "dependencies": {"loopy": "1.0.0"},
            },
            "node_modules/orphan": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0", "orphan": "1.0.0"},
            },
            "node_modules/outer": {
                "version": "1.0.0",
                "dependencies": {"orphan": "1.0.0"},
            },
            "node_modules/@scope/pkg": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/$root": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
        },
    }


class AncestorsReachableAcceptance(unittest.TestCase):
    """任务给定的验收命令：sample-lock.json 查询 leaf。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_without_option_lists_all_ancestors(self):
        result = self.run_cli(["ancestors", SAMPLE_LOCK, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            '{"name": "leaf", "ancestors": ["alpha", "beta", "orphan"]}\n',
        )

    def test_with_reachable_filters_to_root_chain(self):
        result = self.run_cli(["ancestors", SAMPLE_LOCK, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            '{"name": "leaf", "ancestors": ["alpha", "beta"]}\n',
        )


class AncestorsReachableCli(unittest.TestCase):
    """--reachable 的筛选语义、退出码与流约定。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, graph_lock_data())

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assertAncestorsObject(self, stdout, name, ancestors):
        self.assertTrue(stdout.endswith("\n"))
        self.assertEqual(stdout.count("\n"), 1)
        parsed = json.loads(stdout)
        self.assertEqual(set(parsed), {"name", "ancestors"})
        self.assertEqual(parsed, {"name": name, "ancestors": ancestors})

    def test_unreachable_ancestors_filtered_out(self):
        # orphan、outer 与字面 $root 包不可达，被筛掉；可达上游（含经循环
        # 到达的 charlie、传递到达的 alpha）保持码点升序。
        result = self.run_cli(["ancestors", self.lock_path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(
            result.stdout, "leaf", ["@scope/pkg", "alpha", "beta", "charlie"]
        )

    def test_without_option_keeps_unreachable_ancestors(self):
        result = self.run_cli(["ancestors", self.lock_path, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(
            result.stdout,
            "leaf",
            ["$root", "@scope/pkg", "alpha", "beta", "charlie", "orphan", "outer"],
        )

    def test_cycle_members_reachable_from_root_are_kept(self):
        # alpha<->charlie 循环均可达：查询 alpha 时 charlie 保留，目标自身排除。
        result = self.run_cli(["ancestors", self.lock_path, "alpha", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "alpha", ["charlie"])

    def test_unreachable_target_returns_empty_ancestors(self):
        # orphan 已安装但不可达：其唯一其他上游 outer 也不可达，仍成功返回。
        result = self.run_cli(["ancestors", self.lock_path, "orphan", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "orphan", [])

    def test_unreachable_self_loop_target_returns_empty(self):
        # loopy 可达且自环：目标自身始终排除，无其他上游。
        result = self.run_cli(["ancestors", self.lock_path, "loopy", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "loopy", [])

    def test_literal_root_package_unreachable_filtered(self):
        result = self.run_cli(["ancestors", self.lock_path, "$root", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "$root", [])

    def test_empty_or_missing_root_dependencies_yield_empty(self):
        for root_node in ({}, {"dependencies": {}}):
            data = {
                "lockfileVersion": 3,
                "packages": {
                    "": root_node,
                    "node_modules/alpha": {
                        "version": "1.0.0",
                        "dependencies": {"leaf": "1.0.0"},
                    },
                    "node_modules/leaf": {"version": "1.0.0"},
                },
            }
            path = write_lockfile(self.tmp, data, "no-root-deps.json")
            result = self.run_cli(["ancestors", path, "leaf", "--reachable"])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertAncestorsObject(result.stdout, "leaf", [])

    def test_name_echoed_verbatim_with_option(self):
        for target in ("@scope/pkg", "$root", "leaf"):
            result = self.run_cli(["ancestors", self.lock_path, target, "--reachable"])
            self.assertEqual(json.loads(result.stdout)["name"], target)

    def test_missing_target_still_not_found(self):
        result = self.run_cli(["ancestors", self.lock_path, "ghost", "--reachable"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_unreachable_entry_error_still_rejects_whole_file(self):
        # 不可达包 broken 的 dependencies 非对象：整份输入失败。
        data = graph_lock_data()
        data["packages"]["node_modules/broken"] = {
            "version": "1.0.0",
            "dependencies": None,
        }
        path = write_lockfile(self.tmp, data, "broken.json")
        result = self.run_cli(["ancestors", path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_unreachable_dangling_dependency_still_rejects_whole_file(self):
        # 不可达包 orphan 声明不存在的包：整份输入失败，优先于目标判断。
        data = graph_lock_data()
        data["packages"]["node_modules/orphan"]["dependencies"] = {
            "missing": "1.0.0"
        }
        path = write_lockfile(self.tmp, data, "dangling.json")
        for target in ("leaf", "ghost"):
            result = self.run_cli(["ancestors", path, target, "--reachable"])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "INPUT_ERROR\n")
            self.assertEqual(result.stdout, "")


class FindAncestorsUnchanged(unittest.TestCase):
    """find_ancestors 既有调用结果不受 --reachable 影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, graph_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.lock_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_find_ancestors_still_covers_whole_lockfile(self):
        self.assertEqual(
            find_ancestors(self.root_deps, self.packages_map, "leaf"),
            ["$root", "@scope/pkg", "alpha", "beta", "charlie", "orphan", "outer"],
        )

    def test_find_ancestors_unreachable_self_loop_target(self):
        self.assertEqual(
            find_ancestors(self.root_deps, self.packages_map, "orphan"),
            ["outer"],
        )


if __name__ == "__main__":
    unittest.main()
