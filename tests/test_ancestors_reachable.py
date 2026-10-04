"""ancestors --reachable 根可达筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖。

固定的既有行为：
- 验收场景：sample-lock.json 中根仅声明 alpha，alpha 经 beta 到达 leaf，
  leaf 与 beta 互相依赖，不可达的 orphan 也声明 leaf；不加选项时
  ancestors 为 ["alpha", "beta", "orphan"]，加 --reachable 后为
  ["alpha", "beta"]，两次 name 均为 leaf；
- --reachable 只筛选 ancestors 成员：每个保留成员既沿自身 dependencies
  经过至少一条边到达目标，又自根节点沿 dependencies 可达；根项目与
  目标自身始终排除，自环或循环不会让目标重新进入结果，循环中其他合格
  上游仍保留；
- 可达性与 list --reachable 一致：从根 dependencies 出发逐层沿
  dependencies 判断，不解析版本范围，不从其他字段补边；
- 已安装但根不可达的目标（根 dependencies 省略或为空时同样）仍成功
  返回 ancestors 为 []；
- 省略 --reachable 时 find_ancestors 与 ancestors 命令行为不变；
- 筛选不放宽整份校验：不可达条目的缺失版本、非法 dependencies、悬空
  依赖同样以 INPUT_ERROR 失败。
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
    """可达/不可达混合：多祖先链、循环、自环、字面 $root 包与 scoped 包。

    根声明 zeta、alpha、loopy；zeta 经 beta 到达 leaf；alpha 同时声明
    delta、charlie、leaf，charlie 又声明 alpha，构成 alpha<->charlie
    可达循环；loopy 声明自身（可达自环）。orphan 不被根节点引用，声明
    solo 与自身；字面 "$root" 包也声明 solo；@scope/pkg 不被根引用但
    声明 leaf。依赖声明顺序刻意与排序结果不同。
    """
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {
                "dependencies": {
                    "zeta": "1.0.0",
                    "alpha": "1.0.0",
                    "loopy": "1.0.0",
                }
            },
            "node_modules/zeta": {
                "version": "1.0.0",
                "dependencies": {"beta": "1.0.0"},
            },
            "node_modules/beta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/alpha": {
                "version": "1.0.0",
                "dependencies": {
                    "delta": "1.0.0",
                    "charlie": "1.0.0",
                    "leaf": "1.0.0",
                },
            },
            "node_modules/delta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/charlie": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0", "alpha": "1.0.0"},
            },
            "node_modules/leaf": {"version": "1.0.0"},
            "node_modules/loopy": {
                "version": "1.0.0",
                "dependencies": {"loopy": "1.0.0"},
            },
            "node_modules/orphan": {
                "version": "1.0.0",
                "dependencies": {"solo": "1.0.0", "orphan": "1.0.0"},
            },
            "node_modules/solo": {"version": "1.0.0"},
            "node_modules/@scope/pkg": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/$root": {
                "version": "1.0.0",
                "dependencies": {"solo": "1.0.0"},
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
        # @scope/pkg 不被根引用但声明 leaf，被筛掉；orphan 不到达 leaf。
        # 可达链上的 alpha/beta/charlie/delta/zeta 全部保留并按码点排序。
        result = self.run_cli(["ancestors", self.lock_path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(
            result.stdout,
            "leaf",
            ["alpha", "beta", "charlie", "delta", "zeta"],
        )

    def test_reachable_cycle_members_kept(self):
        # alpha<->charlie 为可达循环：charlie 仍是 alpha 的上游，
        # 目标自身不会因循环重新进入结果。
        result = self.run_cli(["ancestors", self.lock_path, "alpha", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "alpha", ["charlie"])

        result = self.run_cli(
            ["ancestors", self.lock_path, "charlie", "--reachable"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "charlie", ["alpha"])

    def test_reachable_self_loop_excludes_target(self):
        # loopy 根可达且声明自身：自环不把目标列入 ancestors。
        result = self.run_cli(["ancestors", self.lock_path, "loopy", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "loopy", [])

    def test_unreachable_target_returns_empty(self):
        # solo 已安装但根不可达：其上游 orphan、$root 包也都不可达，
        # 结果为空但命令成功。
        result = self.run_cli(["ancestors", self.lock_path, "solo", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "solo", [])

    def test_unreachable_self_loop_target_returns_empty(self):
        # orphan 不可达且只被自身声明：目标排除后无合格上游。
        result = self.run_cli(["ancestors", self.lock_path, "orphan", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "orphan", [])

    def test_literal_root_package_unreachable_filtered(self):
        result = self.run_cli(["ancestors", self.lock_path, "$root", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "$root", [])

    def test_empty_root_dependencies_yields_empty(self):
        data = graph_lock_data()
        data["packages"][""] = {"dependencies": {}}
        path = write_lockfile(self.tmp, data, "empty-root.json")
        result = self.run_cli(["ancestors", path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "leaf", [])

    def test_omitted_root_dependencies_yields_empty(self):
        data = graph_lock_data()
        data["packages"][""] = {}
        path = write_lockfile(self.tmp, data, "no-root-deps.json")
        result = self.run_cli(["ancestors", path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertAncestorsObject(result.stdout, "leaf", [])

    def test_missing_target_still_not_found(self):
        result = self.run_cli(["ancestors", self.lock_path, "ghost", "--reachable"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_case_mismatch_still_not_found(self):
        result = self.run_cli(["ancestors", self.lock_path, "LEAF", "--reachable"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_unreachable_missing_version_rejects_whole_file(self):
        # 不可达包 broken 缺少 version：整份输入失败。
        data = graph_lock_data()
        data["packages"]["node_modules/broken"] = {}
        path = write_lockfile(self.tmp, data, "no-version.json")
        result = self.run_cli(["ancestors", path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
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
        # 不可达包 orphan 声明不存在的包：整份输入失败。
        data = graph_lock_data()
        data["packages"]["node_modules/orphan"]["dependencies"] = {
            "missing": "1.0.0"
        }
        path = write_lockfile(self.tmp, data, "dangling.json")
        result = self.run_cli(["ancestors", path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_invalid_input_beats_missing_target(self):
        data = graph_lock_data()
        data["packages"]["node_modules/solo"]["dependencies"] = {"missing": "1.0.0"}
        path = write_lockfile(self.tmp, data, "dangling.json")
        result = self.run_cli(["ancestors", path, "ghost", "--reachable"])
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
            ["@scope/pkg", "alpha", "beta", "charlie", "delta", "zeta"],
        )

    def test_find_ancestors_unreachable_still_listed(self):
        # solo 与 orphan 均从根不可达，省略选项时照常进入结果。
        self.assertEqual(
            find_ancestors(self.root_deps, self.packages_map, "solo"),
            ["$root", "orphan"],
        )


if __name__ == "__main__":
    unittest.main()
