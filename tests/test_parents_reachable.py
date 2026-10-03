"""parents --reachable 的回归测试：API、CLI 与错误语义。

可达性语义与 list --reachable 一致：自根节点 dependencies 出发逐层沿
dependencies 判断，不解析版本范围，不从其他字段补边。--reachable 只限制
parents 成员，direct 仍表示根 dependencies 是否声明目标；省略选项时
find_parents 与 CLI 结果与既有完整查询完全一致（由 test_parents.py 覆盖）。

样例图（与 test_parents.py 同构，另含验收文件 parents-lock.json）：
- 根节点声明 zeta、alpha、loopy；
- alpha 经 delta 到达 leaf；charlie 声明 leaf 与 alpha（循环）；
- loopy 声明自身（可达自环）；orphan 不被根节点引用，声明 solo 与自身
  （不可达自环）；名为 "$root" 的普通包也声明 solo。

全程离线、仅用标准库，不修改仓库自带锁文件，不安装或执行依赖。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import NotFoundError, find_parents, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENTS_LOCK = os.path.join(REPO_ROOT, "parents-lock.json")
BAD_UTF8 = os.path.join(REPO_ROOT, "bad-utf8.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def graph_lock_data():
    """多直接上游 + 循环 + 可达/不可达自环 + 字面 $root 包。"""
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
            "node_modules/zeta": {"version": "1.0.0"},
            "node_modules/alpha": {
                "version": "1.0.0",
                "dependencies": {"delta": "1.0.0", "charlie": "1.0.0"},
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
            "node_modules/$root": {
                "version": "1.0.0",
                "dependencies": {"solo": "1.0.0"},
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

    def test_full_query_lists_alpha_and_orphan(self):
        result = self.run_cli(["parents", PARENTS_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            '{"name": "beta", "direct": false, "parents": ["alpha", "orphan"]}\n',
        )

    def test_reachable_query_lists_only_alpha(self):
        result = self.run_cli(["parents", PARENTS_LOCK, "beta", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            '{"name": "beta", "direct": false, "parents": ["alpha"]}\n',
        )


class ParentsReachableApi(unittest.TestCase):
    """find_parents(..., reachable=True) 的函数级语义。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.lock_path = write_lockfile(self.tmp, graph_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.lock_path)

    def tearDown(self):
        self._tmp.cleanup()

    def parents_of(self, target):
        return find_parents(
            self.root_deps, self.packages_map, target, reachable=True
        )

    def test_unreachable_parents_excluded(self):
        # solo 被 orphan 与 $root 声明，两者均不可达：parents 为空。
        self.assertEqual(
            self.parents_of("solo"),
            {"direct": False, "parents": []},
        )

    def test_reachable_parents_kept_sorted(self):
        # leaf 的直接上游 delta、charlie 均可达，结果按码点升序。
        self.assertEqual(
            self.parents_of("leaf"),
            {"direct": False, "parents": ["charlie", "delta"]},
        )

    def test_reachable_self_loop_keeps_target_itself(self):
        # loopy 可达且声明自身：目标自身进入 parents。
        self.assertEqual(
            self.parents_of("loopy"),
            {"direct": True, "parents": ["loopy"]},
        )

    def test_unreachable_self_loop_excludes_target_itself(self):
        # orphan 声明自身但不可达：目标自身不进入 parents。
        self.assertEqual(
            self.parents_of("orphan"),
            {"direct": False, "parents": []},
        )

    def test_unreachable_target_succeeds_with_empty_parents(self):
        # 已安装但不可达的目标仍成功：direct 为 False、parents 为 []。
        self.assertEqual(
            self.parents_of("$root"),
            {"direct": False, "parents": []},
        )

    def test_direct_unaffected_by_reachable(self):
        # 根直接声明 alpha，reachable 只限制 parents 成员。
        self.assertEqual(
            self.parents_of("alpha"),
            {"direct": True, "parents": ["charlie"]},
        )
        self.assertEqual(
            self.parents_of("zeta"),
            {"direct": True, "parents": []},
        )

    def test_default_call_unchanged(self):
        # 省略 reachable 的既有调用结果不变：不可达声明者照常列出。
        self.assertEqual(
            find_parents(self.root_deps, self.packages_map, "solo"),
            {"direct": False, "parents": ["$root", "orphan"]},
        )
        self.assertEqual(
            find_parents(self.root_deps, self.packages_map, "orphan"),
            {"direct": False, "parents": ["orphan"]},
        )

    def test_missing_target_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.parents_of("ghost")

    def test_does_not_mutate_loaded_data(self):
        snapshot = {
            name: list(info["deps"]) for name, info in self.packages_map.items()
        }
        root_snapshot = list(self.root_deps)
        self.parents_of("leaf")
        self.parents_of("orphan")
        self.assertEqual(self.root_deps, root_snapshot)
        self.assertEqual(
            {name: list(info["deps"]) for name, info in self.packages_map.items()},
            snapshot,
        )


class ParentsReachableCli(unittest.TestCase):
    """parents --reachable 的输出形状、退出码与流约定。"""

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

    def test_reachable_object_shape(self):
        result = self.run_cli(["parents", self.lock_path, "leaf", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        parsed = json.loads(result.stdout)
        self.assertEqual(set(parsed), {"name", "direct", "parents"})
        self.assertEqual(
            parsed,
            {"name": "leaf", "direct": False, "parents": ["charlie", "delta"]},
        )

    def test_unreachable_target_still_succeeds(self):
        result = self.run_cli(["parents", self.lock_path, "orphan", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "orphan", "direct": False, "parents": []},
        )

    def test_missing_target_not_found(self):
        result = self.run_cli(["parents", self.lock_path, "ghost", "--reachable"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_input_error_beats_missing_target(self):
        # 不可达条目的悬空依赖仍使整份输入失败，优先于 NOT_FOUND。
        data = graph_lock_data()
        data["packages"]["node_modules/solo"]["dependencies"] = {"missing": "1.0.0"}
        path = write_lockfile(self.tmp, data, "dangling.json")
        for target in ("ghost", "leaf"):
            result = self.run_cli(["parents", path, target, "--reachable"])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "INPUT_ERROR\n")
            self.assertEqual(result.stdout, "")

    def test_bad_utf8_is_input_error(self):
        result = self.run_cli(["parents", BAD_UTF8, "ghost", "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
