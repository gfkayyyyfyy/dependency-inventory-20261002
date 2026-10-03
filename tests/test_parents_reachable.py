"""parents --reachable 根可达筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖。

固定的既有行为：
- 验收场景：parents-lock.json 中根仅声明 alpha，alpha 与不可达的
  orphan 都声明 beta；不加选项时 parents 为 ["alpha", "orphan"]，
  加 --reachable 后为 ["alpha"]，两次 name 均为 beta、direct 均为 false；
- --reachable 只筛选 parents 成员：保留自根节点沿 dependencies 可达的
  直接上游；direct 仍表示根 dependencies 是否声明目标，不受筛选影响；
- 可达性与 list --reachable 一致：从根 dependencies 出发逐层沿
  dependencies 判断，不解析版本范围，不从其他字段补边；可达自环与循环
  正常结束，目标自身仅在可达且声明自身时进入数组；
- 已安装但不可达的目标仍成功返回 direct 为 false、parents 为 []；
- 省略 --reachable 时 find_parents 与 parents 命令行为不变；
- 筛选不放宽整份校验：不可达包的校验错误同样以 INPUT_ERROR 失败。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import find_parents, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENTS_LOCK = os.path.join(REPO_ROOT, "parents-lock.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def graph_lock_data():
    """可达/不可达混合：循环、自环、字面 $root 包与 scoped 包。

    根声明 alpha、loopy；alpha 经 beta 到达 leaf，charlie 与 alpha 互相
    声明构成可达循环；orphan 与 $root 包从根不可达，orphan 自环并声明
    leaf，$root 包声明 leaf；@scope/pkg 可达并声明 leaf。
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


class ParentsReachableAcceptance(unittest.TestCase):
    """任务给定的验收命令：parents-lock.json 查询 beta。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_without_option_lists_all_parents(self):
        result = self.run_cli(["parents", PARENTS_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            '{"name": "beta", "direct": false, "parents": ["alpha", "orphan"]}\n',
        )

    def test_with_reachable_filters_to_root_chain(self):
        result = self.run_cli(["parents", PARENTS_LOCK, "beta", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            '{"name": "beta", "direct": false, "parents": ["alpha"]}\n',
        )


class ParentsReachableCli(unittest.TestCase):
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

    def assertParentsObject(self, stdout, name, direct, parents):
        self.assertTrue(stdout.endswith("\n"))
        self.assertEqual(stdout.count("\n"), 1)
        parsed = json.loads(stdout)
        self.assertEqual(set(parsed), {"name", "direct", "parents"})
        self.assertEqual(
            parsed, {"name": name, "direct": direct, "parents": parents}
        )

    def test_unreachable_parents_filtered_out(self):
        # orphan 与字面 $root 包不可达，被筛掉；可达上游保持码点升序。
        result = self.run_cli(["parents", self.lock_path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(
            result.stdout, "leaf", False, ["@scope/pkg", "beta", "charlie"]
        )

    def test_direct_unaffected_by_filter(self):
        # 根直接声明 alpha；循环边上的 charlie 可达，仍列出。
        result = self.run_cli(["parents", self.lock_path, "alpha", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(result.stdout, "alpha", True, ["charlie"])

    def test_reachable_self_loop_keeps_target_itself(self):
        result = self.run_cli(["parents", self.lock_path, "loopy", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(result.stdout, "loopy", True, ["loopy"])

    def test_unreachable_self_loop_target_returns_empty_parents(self):
        # orphan 已安装但不可达：自身自环也不进入结果，仍成功返回。
        result = self.run_cli(["parents", self.lock_path, "orphan", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(result.stdout, "orphan", False, [])

    def test_literal_root_package_unreachable_filtered(self):
        result = self.run_cli(["parents", self.lock_path, "$root", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertParentsObject(result.stdout, "$root", False, [])

    def test_missing_target_still_not_found(self):
        result = self.run_cli(["parents", self.lock_path, "ghost", "--reachable"])
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
        result = self.run_cli(["parents", path, "leaf", "--reachable"])
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
        result = self.run_cli(["parents", path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")


class FindParentsUnchanged(unittest.TestCase):
    """find_parents 既有调用结果不受 --reachable 影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, graph_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.lock_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_find_parents_still_covers_whole_lockfile(self):
        self.assertEqual(
            find_parents(self.root_deps, self.packages_map, "leaf"),
            {
                "direct": False,
                "parents": ["$root", "@scope/pkg", "beta", "charlie", "orphan"],
            },
        )

    def test_find_parents_unreachable_self_loop_kept(self):
        self.assertEqual(
            find_parents(self.root_deps, self.packages_map, "orphan"),
            {"direct": False, "parents": ["orphan"]},
        )


if __name__ == "__main__":
    unittest.main()
