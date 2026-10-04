"""路径计算流程统一后的回归测试：单包查询与批量导出共用同一份规则。

重构把 find_path（why）与 sbom_document --with-paths 各自维护的 BFS
合并为 lockfile._bfs_parents / _reconstruct_path 一份实现。本文件用
固定预期锁定两个入口的路径结果——等长分支的字典序裁决、循环与自环、
根不可达包——而不是只断言两个入口彼此相等（相等的错误实现也能通过
那种断言）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
全程离线、仅用标准库，不修改仓库自带样例。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import (
    ROOT,
    InputError,
    NotFoundError,
    find_path,
    load_lockfile,
    sbom_document,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_FIELDS = {"name", "version", "ecosystem", "direct", "license", "securityStatus"}


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def shared_lock_data():
    """等长分支 + 可达循环 + 自环 + 根不可达包的平铺 v3 锁文件数据。

    图关系（书写顺序刻意与字典序相反，证明结果不取决于声明顺序）：
    - 根声明 zeta、alpha；alpha 声明 delta、charlie；
    - charlie 依赖 leaf 与 alpha（回到 alpha 的可达循环），delta 依赖 leaf；
    - zeta 经 beta 也到达 leaf，leaf 自环；
    - orphan 不被根引用且自依赖（根不可达）。
    """
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"zeta": "1.0.0", "alpha": "1.0.0"}},
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
            "node_modules/leaf": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/orphan": {
                "version": "1.0.0",
                "dependencies": {"orphan": "1.0.0"},
            },
        },
    }


# 全部包的固定预期路径（默认 why / sbom --with-paths 共用）。
# leaf 的三条等长路径 $root/alpha/charlie/leaf、$root/alpha/delta/leaf、
# $root/zeta/beta/leaf 中，包名序列字典序最小的是经 charlie 的一条。
EXPECTED_PATHS = {
    "alpha": ["$root", "alpha"],
    "beta": ["$root", "zeta", "beta"],
    "charlie": ["$root", "alpha", "charlie"],
    "delta": ["$root", "alpha", "delta"],
    "leaf": ["$root", "alpha", "charlie", "leaf"],
    "zeta": ["$root", "zeta"],
    "orphan": [],
}


class SharedPathRule(unittest.TestCase):
    """两个入口对同一图的每条路径都等于固定预期。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, shared_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_find_path_matches_fixed_expectations(self):
        for name, expected in EXPECTED_PATHS.items():
            with self.subTest(name=name):
                self.assertEqual(
                    find_path(self.root_deps, self.packages_map, name),
                    expected,
                )

    def test_sbom_with_paths_matches_fixed_expectations(self):
        doc = sbom_document(self.root_deps, self.packages_map, with_paths=True)
        self.assertEqual(
            [item["name"] for item in doc["components"]],
            ["alpha", "beta", "charlie", "delta", "leaf", "orphan", "zeta"],
        )
        for item in doc["components"]:
            with self.subTest(name=item["name"]):
                self.assertEqual(item["path"], EXPECTED_PATHS[item["name"]])
                self.assertEqual(set(item), BASE_FIELDS | {"path"})

    def test_sbom_without_paths_has_no_path_field(self):
        doc = sbom_document(self.root_deps, self.packages_map)
        for item in doc["components"]:
            self.assertEqual(set(item), BASE_FIELDS)

    def test_sbom_reachable_keeps_fixed_paths_and_drops_orphan(self):
        doc = sbom_document(
            self.root_deps, self.packages_map, reachable=True, with_paths=True
        )
        self.assertEqual(
            {item["name"]: item["path"] for item in doc["components"]},
            {name: path for name, path in EXPECTED_PATHS.items() if path},
        )

    def test_why_cli_matches_fixed_expectations(self):
        for name, expected in EXPECTED_PATHS.items():
            with self.subTest(name=name):
                result = run_cli(["why", self.path, name])
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "")
                self.assertEqual(
                    json.loads(result.stdout), {"name": name, "path": expected}
                )
                self.assertTrue(result.stdout.endswith("\n"))

    def test_sbom_cli_with_paths_matches_fixed_expectations(self):
        result = run_cli(["sbom", self.path, "--with-paths"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        doc = json.loads(result.stdout)
        self.assertEqual(
            {item["name"]: item["path"] for item in doc["components"]},
            {name: EXPECTED_PATHS[name] for name in
             ["alpha", "beta", "charlie", "delta", "leaf", "orphan", "zeta"]},
        )

    def test_declaration_order_does_not_change_paths(self):
        def reverse_keys(value):
            if isinstance(value, dict):
                return {
                    key: reverse_keys(item)
                    for key, item in reversed(list(value.items()))
                }
            return value

        reordered = write_lockfile(
            self.tmp, reverse_keys(shared_lock_data()), "reordered.json"
        )
        rev_root_deps, rev_map = load_lockfile(reordered)
        # 书写顺序确实被交换，但每个包的路径仍等于固定预期。
        self.assertEqual(rev_root_deps, ["alpha", "zeta"])
        for name, expected in EXPECTED_PATHS.items():
            with self.subTest(name=name):
                self.assertEqual(find_path(rev_root_deps, rev_map, name), expected)
        doc = sbom_document(rev_root_deps, rev_map, with_paths=True)
        for item in doc["components"]:
            self.assertEqual(item["path"], EXPECTED_PATHS[item["name"]])

    def test_inputs_not_mutated_by_either_entry(self):
        root_before = copy.deepcopy(self.root_deps)
        map_before = copy.deepcopy(self.packages_map)
        for name in EXPECTED_PATHS:
            find_path(self.root_deps, self.packages_map, name)
        sbom_document(self.root_deps, self.packages_map, with_paths=True)
        sbom_document(self.root_deps, self.packages_map, True, True)
        self.assertEqual(self.root_deps, root_before)
        self.assertEqual(self.packages_map, map_before)


class FromSourceQueries(unittest.TestCase):
    """--from 起点规则在统一实现下保持不变。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, shared_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_from_source_uses_source_as_single_start(self):
        # 自 zeta 出发到 leaf 不经根标记，也不经过 alpha 一侧的更短字样。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "zeta"),
            ["zeta", "beta", "leaf"],
        )

    def test_from_unreachable_source_still_queries(self):
        # orphan 根不可达，但仍可作为 --from 起点沿自身 dependencies 查询。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "orphan", "orphan"),
            ["orphan"],
        )
        # orphan 只依赖自身，到 alpha 不可达。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "alpha", "orphan"),
            [],
        )

    def test_source_equals_target_returns_single_element(self):
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "alpha", "alpha"),
            ["alpha"],
        )

    def test_root_literal_is_plain_package_name(self):
        # 字面值 "$root" 没有特殊含义：未安装该名字的包即 NotFoundError。
        self.assertNotIn(ROOT, self.packages_map)
        with self.assertRaises(NotFoundError):
            find_path(self.root_deps, self.packages_map, "alpha", ROOT)
        result = run_cli(["why", self.path, "alpha", "--from", ROOT])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_from_source_cli(self):
        result = run_cli(["why", self.path, "leaf", "--from", "zeta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "leaf", "path": ["zeta", "beta", "leaf"]},
        )


class MissingAndInvalidInputs(unittest.TestCase):
    """合法输入中名字缺失为 NOT_FOUND；整份文件问题为 INPUT_ERROR。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, shared_lock_data())

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_target_and_case_sensitivity(self):
        root_deps, packages_map = load_lockfile(self.path)
        # 包名区分大小写：Alpha 与已安装的 alpha 不是同一个包。
        for name in ("ghost", "Alpha"):
            with self.subTest(name=name):
                with self.assertRaises(NotFoundError):
                    find_path(root_deps, packages_map, name)
                result = run_cli(["why", self.path, name])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, "NOT_FOUND\n")
                self.assertEqual(result.stdout, "")

    def test_missing_from_source_not_found(self):
        root_deps, packages_map = load_lockfile(self.path)
        with self.assertRaises(NotFoundError):
            find_path(root_deps, packages_map, "alpha", "ghost")
        result = run_cli(["why", self.path, "alpha", "--from", "ghost"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")

    def test_dangling_dependency_rejected_before_query(self):
        data = shared_lock_data()
        data["packages"]["node_modules/alpha"]["dependencies"]["ghost"] = "1.0.0"
        dangling = write_lockfile(self.tmp, data, "dangling.json")
        with self.assertRaises(InputError):
            load_lockfile(dangling)
        for argv in (["why", dangling, "alpha"], ["sbom", dangling, "--with-paths"]):
            with self.subTest(argv=argv):
                result = run_cli(argv)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "INPUT_ERROR\n")
                self.assertEqual(result.stdout, "")

    def test_broken_json_and_unreadable_file(self):
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        missing = os.path.join(self.tmp, "missing.json")
        for path in (broken, missing):
            with self.subTest(path=path):
                with self.assertRaises(InputError):
                    load_lockfile(path)
                result = run_cli(["why", path, "alpha"])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "INPUT_ERROR\n")
                self.assertEqual(result.stdout, "")


class DemoLockAcceptance(unittest.TestCase):
    """仓库自带 demo-lock.json 的固定输出（用户核对命令）。"""

    DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")

    def test_why_beta(self):
        result = run_cli(["why", self.DEMO_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "beta", "path": ["$root", "alpha", "beta"]},
        )

    def test_sbom_with_paths(self):
        result = run_cli(["sbom", self.DEMO_LOCK, "--with-paths"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        doc = json.loads(result.stdout)
        self.assertEqual(
            {item["name"]: item["path"] for item in doc["components"]},
            {"alpha": ["$root", "alpha"], "beta": ["$root", "alpha", "beta"]},
        )


if __name__ == "__main__":
    unittest.main()
