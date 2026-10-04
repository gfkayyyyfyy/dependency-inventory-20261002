"""路径选择规则统一后的回归测试：why 与 sbom --with-paths 共用同一份规则。

重构把单包查询（find_path）与批量导出（sbom_document）各自维护的 BFS
合并为同一份遍历；本文件用固定预期锁定该规则在两个入口的可见行为，
而不是只断言两个入口结果相等：

- 等长分支：beta 有两条同为 2 条边的路径，"@scope/pkg"（U+0040 开头）
  先于 "zeta"；leaf 有四条同为 3 条边的路径，取包名序列 Unicode 码点
  字典序最小的 $root/@scope/pkg/beta/leaf；
- 循环与自环：charlie -> alpha 构成可达循环，leaf 自环，查询正常
  结束且结果唯一确定；
- 不可达包：orphan 已安装但根不可达且自依赖，why 返回 []，sbom 的
  path 为 []；
- 作用域包 @scope/pkg 是单个路径元素；
- 条目与 dependencies 声明顺序不影响任一入口的结果；
- --from 从指定已安装包出发（即使根不可达），起点等于目标时返回
  单元素数组，字面值 $root 按普通包名查找（未安装 -> NOT_FOUND）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
全程离线、仅用标准库，不修改仓库自带样例。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import ROOT, find_path, load_lockfile, sbom_document

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")


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


def shared_rule_lock_data():
    """构造覆盖等长分支、循环、自环与不可达包的平铺 v3 锁文件数据。

    书写顺序刻意与字典序相反（根依赖 zeta 在 alpha 前、alpha 的
    dependencies 中 delta 在 charlie 前），以证明结果不取决于声明顺序。
    """
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {
                "dependencies": {
                    "zeta": "1.0.0",
                    "alpha": "1.0.0",
                    "@scope/pkg": "1.0.0",
                }
            },
            "node_modules/zeta": {
                "version": "1.0.0",
                "dependencies": {"beta": "1.0.0"},
            },
            "node_modules/alpha": {
                "version": "1.0.0",
                "dependencies": {"delta": "1.0.0", "charlie": "1.0.0"},
            },
            "node_modules/@scope/pkg": {
                "version": "1.0.0",
                "dependencies": {"beta": "1.0.0"},
            },
            "node_modules/charlie": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0", "alpha": "1.0.0"},
            },
            "node_modules/delta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/beta": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
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


# 固定预期：默认 why（自 $root）每个包的唯一最短路径。
EXPECTED_ROOT_PATHS = {
    "@scope/pkg": ["$root", "@scope/pkg"],
    "alpha": ["$root", "alpha"],
    # 等长：$root/@scope/pkg/beta 与 $root/zeta/beta 同为 2 条边，
    # "@"（U+0040）< "z"，取前者。
    "beta": ["$root", "@scope/pkg", "beta"],
    "charlie": ["$root", "alpha", "charlie"],
    "delta": ["$root", "alpha", "delta"],
    # 等长：四条同为 3 条边的路径（经 alpha/charlie、alpha/delta、
    # zeta/beta、@scope/pkg/beta）中取包名序列字典序最小的一条。
    "leaf": ["$root", "@scope/pkg", "beta", "leaf"],
    "zeta": ["$root", "zeta"],
    # 已安装但根不可达（自环不构成来自根的路径）。
    "orphan": [],
}

EXPECTED_COMPONENT_ORDER = [
    "@scope/pkg", "alpha", "beta", "charlie", "delta", "leaf", "orphan", "zeta",
]


def reversed_key_order(value):
    """递归交换所有对象的键书写顺序，图关系保持不变。"""
    if isinstance(value, dict):
        return {
            key: reversed_key_order(item)
            for key, item in reversed(list(value.items()))
        }
    return value


class SharedRootPathRule(unittest.TestCase):
    """默认 why 与 sbom --with-paths 对同一图给出同一组固定路径。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, shared_rule_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_why_api_matches_fixed_expectations(self):
        for name, expected in EXPECTED_ROOT_PATHS.items():
            with self.subTest(name=name):
                self.assertEqual(
                    find_path(self.root_deps, self.packages_map, name),
                    expected,
                )

    def test_why_cli_matches_fixed_expectations(self):
        for name, expected in EXPECTED_ROOT_PATHS.items():
            with self.subTest(name=name):
                result = run_cli(["why", self.path, name])
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "")
                self.assertTrue(result.stdout.endswith("\n"))
                self.assertEqual(
                    json.loads(result.stdout), {"name": name, "path": expected}
                )

    def test_sbom_with_paths_matches_fixed_expectations(self):
        doc = sbom_document(self.root_deps, self.packages_map, with_paths=True)
        self.assertEqual(
            [item["name"] for item in doc["components"]],
            EXPECTED_COMPONENT_ORDER,
        )
        for item in doc["components"]:
            with self.subTest(name=item["name"]):
                self.assertEqual(
                    item["path"], EXPECTED_ROOT_PATHS[item["name"]]
                )
        result = run_cli(["sbom", self.path, "--with-paths"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), doc)

    def test_sbom_paths_agree_with_why_per_component(self):
        # 补充性一致性检查（固定预期见上方用例）：两个入口共用同一份规则。
        doc = sbom_document(self.root_deps, self.packages_map, with_paths=True)
        for item in doc["components"]:
            self.assertEqual(
                item["path"],
                find_path(self.root_deps, self.packages_map, item["name"]),
                item["name"],
            )

    def test_sbom_without_with_paths_has_no_path_field(self):
        doc = sbom_document(self.root_deps, self.packages_map)
        self.assertEqual(
            [item["name"] for item in doc["components"]],
            EXPECTED_COMPONENT_ORDER,
        )
        for item in doc["components"]:
            self.assertNotIn("path", item)
        result = run_cli(["sbom", self.path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), doc)

    def test_declaration_order_does_not_change_any_entry_point(self):
        reordered = write_lockfile(
            self.tmp, reversed_key_order(shared_rule_lock_data()), "reversed.json"
        )
        rev_root_deps, rev_map = load_lockfile(reordered)
        # 书写顺序确实被交换，图关系不变。
        self.assertEqual(rev_root_deps, ["@scope/pkg", "alpha", "zeta"])
        self.assertEqual(self.packages_map["alpha"]["deps"], ["delta", "charlie"])
        self.assertEqual(rev_map["alpha"]["deps"], ["charlie", "delta"])

        for name, expected in EXPECTED_ROOT_PATHS.items():
            with self.subTest(name=name):
                self.assertEqual(
                    find_path(rev_root_deps, rev_map, name), expected
                )
        self.assertEqual(
            sbom_document(rev_root_deps, rev_map, with_paths=True),
            sbom_document(self.root_deps, self.packages_map, with_paths=True),
        )


class SharedFromSourceRule(unittest.TestCase):
    """--from 指定起点走同一份规则：根不可达的起点、起点即目标、$root 字面值。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, shared_rule_lock_data())
        self.root_deps, self.packages_map = load_lockfile(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_from_installed_package_uses_same_tie_break(self):
        # 自 zeta 出发到 leaf 只有 $zeta/beta/leaf 一条；自 alpha 出发到
        # leaf 的两条等长路径仍按字典序取 charlie 一条，不带根标记。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "zeta"),
            ["zeta", "beta", "leaf"],
        )
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "alpha"),
            ["alpha", "charlie", "leaf"],
        )
        result = run_cli(["why", self.path, "leaf", "--from", "alpha"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "leaf", "path": ["alpha", "charlie", "leaf"]},
        )

    def test_from_unreachable_start_and_start_equals_target(self):
        # orphan 根不可达仍可作为起点；起点等于目标返回单元素数组。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "orphan", "orphan"),
            ["orphan"],
        )
        result = run_cli(["why", self.path, "orphan", "--from", "orphan"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "orphan", "path": ["orphan"]},
        )

    def test_from_unreachable_start_to_unreachable_target(self):
        # 根不可达的起点沿自身 dependencies 查询：orphan 只依赖自身，
        # 到 leaf 不可达，返回空数组而非报错。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "orphan"),
            [],
        )

    def test_from_literal_root_constant_looked_up_as_package(self):
        # 字面值 $root 没有特殊含义：未安装 -> NOT_FOUND，退出 1。
        result = run_cli(["why", self.path, "leaf", "--from", ROOT])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")


class DemoLockAcceptance(unittest.TestCase):
    """基本验收：demo-lock.json 上两个入口的固定输出。"""

    def test_why_beta_path(self):
        result = run_cli(["why", DEMO_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "beta", "path": ["$root", "alpha", "beta"]},
        )

    def test_sbom_with_paths_components(self):
        result = run_cli(["sbom", DEMO_LOCK, "--with-paths"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        paths = {
            item["name"]: item["path"]
            for item in json.loads(result.stdout)["components"]
        }
        self.assertEqual(
            paths,
            {"alpha": ["$root", "alpha"], "beta": ["$root", "alpha", "beta"]},
        )


if __name__ == "__main__":
    unittest.main()
