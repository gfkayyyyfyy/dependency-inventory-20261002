"""why --from 指定起点最短路径语义的组合回归测试（独立核对）。

在 alpha、beta、gamma、leaf 四个已安装包之间枚举所有不含自环的有向边
组合（12 条候选边，共 2^12 = 4096 张图），对每张图核对全部起点 × 目标
组合（含起点等于目标）：

- 期望路径由本模块独立的简单路径枚举得出（边数最少；同长度按包名序列
  的 Unicode 码点字典序取第一条；不可达为 []），不调用 find_path 生成
  答案，也不复用其内部辅助函数；
- 每张图分别采用空根依赖与根直接声明 alpha 两种根依赖，指定起点的
  答案应相同；
- 交换条目与依赖列表顺序后结果仍相同，且查询前后版本、根依赖及包
  依赖列表内容保持不变。

另有固定用例覆盖自环、区分大小写的完整包名、@scope/pkg、名为 $root 的
普通安装包与 NotFoundError 语义，以及 sample-lock.json 的命令行查询。

全程离线、仅使用标准库。数据遵循 load_lockfile 的返回数据约定：
(root_deps, {name: {"version", "deps"}})，所有包版本统一为 1.0.0。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import NotFoundError, find_path, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_LOCK = os.path.join(REPO_ROOT, "sample-lock.json")

NAMES = ("alpha", "beta", "gamma", "leaf")
# 不含自环的全部候选有向边。
CANDIDATE_EDGES = [(a, b) for a in NAMES for b in NAMES if a != b]


def build_packages_map(edges, swapped=False):
    """按 load_lockfile 返回约定构造 packages_map，版本统一 1.0.0。

    swapped 为 True 时交换条目顺序与每个包的依赖列表顺序；图本身不变。
    """
    deps = {name: [] for name in NAMES}
    for source, target in edges:
        deps[source].append(target)
    names = list(NAMES)
    if swapped:
        names.reverse()
    packages_map = {}
    for name in names:
        dep_list = list(deps[name])
        if swapped:
            dep_list.reverse()
        packages_map[name] = {"version": "1.0.0", "deps": dep_list}
    return packages_map


def reference_shortest_path(packages_map, source, target):
    """独立期望：枚举 source 到 target 的全部简单路径，取最优者。

    最优定义为边数最少；并列时按包名序列的 Unicode 码点字典序取第一条
    （Python 字符串比较即按码点）。起点等于目标时返回只含该名称的数组；
    不可达返回 []。最短路径必为简单路径，故枚举简单路径即可。
    """
    if source == target:
        return [source]
    best = None

    def visit(node, path, seen):
        nonlocal best
        for nxt in packages_map[node]["deps"]:
            if nxt in seen:
                continue
            candidate = path + [nxt]
            if nxt == target:
                if best is None or (len(candidate), candidate) < (len(best), best):
                    best = candidate
            else:
                visit(nxt, candidate, seen | {nxt})

    visit(source, [source], {source})
    return best if best is not None else []


def graph_label(edges):
    return ",".join("%s->%s" % edge for edge in edges) or "(no edges)"


class WhyFromCombinatorial(unittest.TestCase):
    """全部无自环有向图 × 全部起点目标组合的最短路径核对。"""

    def test_all_edge_combinations(self):
        for mask in range(1 << len(CANDIDATE_EDGES)):
            edges = [
                CANDIDATE_EDGES[i]
                for i in range(len(CANDIDATE_EDGES))
                if mask & (1 << i)
            ]
            label = graph_label(edges)
            base_map = build_packages_map(edges)
            swapped_map = build_packages_map(edges, swapped=True)
            # 期望表只依赖图本身，与根依赖、条目及依赖书写顺序无关。
            expected_table = {
                (source, target): reference_shortest_path(base_map, source, target)
                for source in NAMES
                for target in NAMES
            }
            for root_deps in ([], ["alpha"]):
                for order_tag, packages_map in (
                    ("declared", base_map),
                    ("swapped", swapped_map),
                ):
                    map_snapshot = copy.deepcopy(packages_map)
                    root_snapshot = list(root_deps)
                    with self.subTest(graph=label, root=root_deps, order=order_tag):
                        for source in NAMES:
                            for target in NAMES:
                                actual = find_path(
                                    root_deps, packages_map, target, source
                                )
                                expected = expected_table[(source, target)]
                                self.assertEqual(
                                    actual,
                                    expected,
                                    "graph=%s root=%r order=%s source=%s target=%s"
                                    % (label, root_deps, order_tag, source, target),
                                )
                                # 指定起点的查询不额外插入虚拟根标记。
                                self.assertNotIn(
                                    "$root",
                                    actual,
                                    "graph=%s source=%s target=%s: "
                                    "unexpected virtual root marker"
                                    % (label, source, target),
                                )
                        # 查询不修改版本、根依赖及包依赖列表内容。
                        self.assertEqual(packages_map, map_snapshot)
                        self.assertEqual(root_deps, root_snapshot)


class WhyFromFixedCases(unittest.TestCase):
    """自环、大小写、scoped 名、字面 $root 包与错误语义。"""

    def test_self_loop_source_equals_target(self):
        packages_map = {
            "alpha": {"version": "1.0.0", "deps": ["alpha", "beta"]},
            "beta": {"version": "1.0.0", "deps": []},
        }
        # 有自环时同包查询仍只返回含该名称的数组。
        self.assertEqual(find_path([], packages_map, "alpha", "alpha"), ["alpha"])
        self.assertEqual(find_path([], packages_map, "beta", "beta"), ["beta"])
        # 自环不产生重复节点，也不影响其他最短路径。
        self.assertEqual(
            find_path([], packages_map, "beta", "alpha"), ["alpha", "beta"]
        )

    def test_self_loop_on_intermediate_node_terminates(self):
        packages_map = {
            "alpha": {"version": "1.0.0", "deps": ["beta"]},
            "beta": {"version": "1.0.0", "deps": ["beta", "leaf"]},
            "leaf": {"version": "1.0.0", "deps": []},
        }
        self.assertEqual(
            find_path([], packages_map, "leaf", "alpha"),
            ["alpha", "beta", "leaf"],
        )

    def test_cycle_with_unreachable_target(self):
        packages_map = {
            "alpha": {"version": "1.0.0", "deps": ["beta"]},
            "beta": {"version": "1.0.0", "deps": ["alpha"]},
            "leaf": {"version": "1.0.0", "deps": []},
        }
        # 循环正常结束；已安装但不可达返回 []。
        self.assertEqual(find_path([], packages_map, "leaf", "alpha"), [])
        self.assertEqual(find_path([], packages_map, "beta", "alpha"), ["alpha", "beta"])

    def test_case_sensitive_full_name(self):
        packages_map = {
            "Alpha": {"version": "1.0.0", "deps": ["leaf"]},
            "alpha": {"version": "1.0.0", "deps": []},
            "leaf": {"version": "1.0.0", "deps": []},
        }
        # 完整包名精确匹配：Alpha 与 alpha 是两个不同的包。
        self.assertEqual(
            find_path([], packages_map, "leaf", "Alpha"), ["Alpha", "leaf"]
        )
        self.assertEqual(find_path([], packages_map, "leaf", "alpha"), [])
        with self.assertRaises(NotFoundError):
            find_path([], packages_map, "LEAF", "Alpha")
        with self.assertRaises(NotFoundError):
            find_path([], packages_map, "leaf", "ALPHA")

    def test_scoped_name_matched_as_full_name(self):
        packages_map = {
            "@scope/pkg": {"version": "1.0.0", "deps": ["leaf"]},
            "leaf": {"version": "1.0.0", "deps": []},
            "pkg": {"version": "1.0.0", "deps": ["@scope/pkg"]},
        }
        self.assertEqual(
            find_path([], packages_map, "leaf", "@scope/pkg"),
            ["@scope/pkg", "leaf"],
        )
        self.assertEqual(
            find_path([], packages_map, "@scope/pkg", "pkg"),
            ["pkg", "@scope/pkg"],
        )
        # 不带作用域的短名不匹配 scoped 包。
        with self.assertRaises(NotFoundError):
            find_path([], packages_map, "leaf", "scope/pkg")

    def test_literal_root_name_is_a_normal_package(self):
        packages_map = {
            "$root": {"version": "1.0.0", "deps": ["leaf"]},
            "leaf": {"version": "1.0.0", "deps": []},
            "alpha": {"version": "1.0.0", "deps": []},
        }
        # --from "$root" 以同名已安装包为起点；结果只含一个 $root，
        # 不额外插入虚拟根标记（否则会成为 ["$root", "$root", "leaf"]）。
        self.assertEqual(
            find_path([], packages_map, "leaf", "$root"), ["$root", "leaf"]
        )
        self.assertEqual(find_path([], packages_map, "alpha", "$root"), [])
        self.assertEqual(
            find_path([], packages_map, "$root", "$root"), ["$root"]
        )

    def test_missing_source_or_target_not_found(self):
        packages_map = {
            "alpha": {"version": "1.0.0", "deps": ["leaf"]},
            "leaf": {"version": "1.0.0", "deps": []},
        }
        for target, source in (
            ("ghost", "alpha"),
            ("alpha", "ghost"),
            ("ghost", "phantom"),
        ):
            with self.subTest(target=target, source=source):
                with self.assertRaises(NotFoundError):
                    find_path([], packages_map, target, source)


class WhyFromLockfileConvention(unittest.TestCase):
    """组合测试的内存构造与 load_lockfile 返回约定一致。"""

    def test_in_memory_map_matches_loader(self):
        edges = [("alpha", "beta"), ("beta", "leaf"), ("gamma", "alpha")]
        data = {
            "lockfileVersion": 3,
            "packages": {"": {"dependencies": {"alpha": "1.0.0"}}},
        }
        for name in NAMES:
            node = {"version": "1.0.0"}
            node_deps = {dst: "1.0.0" for src, dst in edges if src == name}
            if node_deps:
                node["dependencies"] = node_deps
            data["packages"]["node_modules/" + name] = node
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "lock.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, ["alpha"])
        self.assertEqual(packages_map, build_packages_map(edges))
        for name in NAMES:
            self.assertEqual(packages_map[name]["version"], "1.0.0")


class WhyFromCliSampleLock(unittest.TestCase):
    """sample-lock.json 上 --from 查询的命令行行为。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assertWhyOutput(self, result, name, path):
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        # 标准输出仅为含 name 与 path 的 JSON 对象加末尾换行。
        self.assertEqual(
            result.stdout,
            json.dumps({"name": name, "path": path}, ensure_ascii=False) + "\n",
        )
        parsed = json.loads(result.stdout)
        self.assertEqual(set(parsed), {"name", "path"})
        self.assertEqual(parsed, {"name": name, "path": path})

    def test_orphan_to_leaf(self):
        result = self.run_cli(["why", SAMPLE_LOCK, "leaf", "--from", "orphan"])
        self.assertWhyOutput(result, "leaf", ["orphan", "leaf"])

    def test_leaf_to_orphan_unreachable(self):
        result = self.run_cli(["why", SAMPLE_LOCK, "orphan", "--from", "leaf"])
        self.assertWhyOutput(result, "orphan", [])

    def test_queries_without_from_unchanged(self):
        # 省略 --from：路径仍以虚拟根 $root 开始。
        result = self.run_cli(["why", SAMPLE_LOCK, "leaf"])
        self.assertWhyOutput(result, "leaf", ["$root", "alpha", "beta", "leaf"])
        # 其他公开命令保持现有结果。
        result = self.run_cli(["list", SAMPLE_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        names = [item["name"] for item in json.loads(result.stdout)]
        self.assertEqual(names, ["alpha", "beta", "isolated", "leaf", "orphan"])


if __name__ == "__main__":
    unittest.main()
