"""find_descendants 全部下游查询的组合回归测试（独立核对）。

在 alpha、beta、leaf 三个已安装包之间枚举包含自环的全部 9 条候选有向边
（3×3）的所有组合，共 2^9 = 512 张图，对每张图逐一查询三个包：

- 期望结果由本模块独立的可达集不动点计算得出（沿 dependencies 至少一条
  边可达的其他包；查询包自身始终排除；按完整包名 Unicode 码点升序排列且
  无重复），不调用 find_descendants 或其内部实现来生成答案；
- 同一张图分别采用空根依赖与根只声明 alpha 两种根依赖，任一查询包的
  结果都应相同（起点即使从根不可达也沿自身 dependencies 展开）；
- 再颠倒包条目顺序与每个包的依赖列表顺序，结果仍一致；
- 每轮三个查询前后，根依赖、包版本及各依赖列表的内容和顺序均保持不变。

自环（无边或仅自环返回空列表）、循环（循环中的其他下游保留）、分支与
断开关系（与根断开的包照常展开自身依赖）都由 512 张图自然覆盖。

全程离线、仅使用标准库，不逐图启动命令行进程。数据遵循 load_lockfile
的返回数据约定：(root_deps, {name: {"version", "deps"}})，所有包版本
统一为 1.0.0，依赖只指向这三个包。
"""

import copy
import json
import os
import tempfile
import unittest

from depinventory import find_descendants, load_lockfile

NAMES = ("alpha", "beta", "leaf")
# 含自环的全部候选有向边：3 个包两两组合（含 source == target）共 9 条。
CANDIDATE_EDGES = [(source, target) for source in NAMES for target in NAMES]


def build_packages_map(edges, swapped=False):
    """按 load_lockfile 返回约定构造 packages_map，版本统一 1.0.0。

    swapped 为 True 时颠倒包条目顺序与每个包的依赖列表顺序；图本身不变，
    用于证明查询结果与声明、遍历书写顺序无关。
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


def reference_descendants(edges, start):
    """独立期望：邻接集上的可达集不动点，与产品的 BFS 实现互不相关。

    下游为自 start 沿有向边经过至少一条边可达的全部包名：前沿直接由
    start 的邻接播种（至少一条边），随后反复并入前沿节点的邻接，直到
    集合不再增长；start 自身在结果中显式剔除，故自环与回到 start 的循环
    都不把 start 计入，循环中的其他包保留。最后按完整包名 Unicode 码点
    升序排列（Python 字符串比较即按码点）；集合天然去重。
    """
    adjacency = {name: set() for name in NAMES}
    for source, target in edges:
        adjacency[source].add(target)

    reachable = set()
    frontier = set(adjacency[start])
    while frontier:
        reachable |= frontier
        frontier = {
            nxt
            for node in frontier
            for nxt in adjacency[node]
            if nxt not in reachable
        }
    reachable.discard(start)
    return sorted(reachable)


def graph_label(edges):
    return ",".join("%s->%s" % edge for edge in edges) or "(no edges)"


class DescendantsCombinatorial(unittest.TestCase):
    """512 种边组合 × 两种根依赖 × 两种排列 × 三个查询包的全量核对。"""

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
            # 期望表只依赖图关系本身，与根依赖、条目及依赖书写顺序无关。
            expected_table = {
                start: reference_descendants(edges, start) for start in NAMES
            }
            for root_deps in ([], ["alpha"]):
                for order_tag, packages_map in (
                    ("declared", base_map),
                    ("swapped", swapped_map),
                ):
                    map_snapshot = copy.deepcopy(packages_map)
                    root_snapshot = list(root_deps)
                    context = "edges={%s} root_deps=%r order=%s" % (
                        label,
                        root_deps,
                        order_tag,
                    )
                    for start in NAMES:
                        actual = find_descendants(root_deps, packages_map, start)
                        self.assertEqual(
                            actual,
                            expected_table[start],
                            "%s query=%s" % (context, start),
                        )
                        self.assertEqual(
                            len(actual),
                            len(set(actual)),
                            "%s query=%s: duplicates in result"
                            % (context, start),
                        )
                        self.assertNotIn(
                            start,
                            actual,
                            "%s query=%s: queried package must be excluded"
                            % (context, start),
                        )
                        # 每轮查询前后，根依赖、版本与依赖列表的内容和
                        # 顺序都保持不变。
                        self.assertEqual(
                            packages_map,
                            map_snapshot,
                            "%s query=%s: packages_map mutated"
                            % (context, start),
                        )
                        self.assertEqual(
                            root_deps,
                            root_snapshot,
                            "%s query=%s: root_deps mutated" % (context, start),
                        )

    def test_expected_shapes_on_key_graphs(self):
        """对组合空间中几张代表性图钉住具体期望，便于失败时人工复现。

        - 无边 / 仅自环：三个包都返回空列表；
        - alpha<->beta 循环且 beta 指向 leaf：循环中的另一成员保留；
        - 与根完全断开的分支仍沿起点自身依赖展开。
        """
        cases = [
            ("empty", [], {"alpha": [], "beta": [], "leaf": []}),
            (
                "self-loops-only",
                [("alpha", "alpha"), ("beta", "beta"), ("leaf", "leaf")],
                {"alpha": [], "beta": [], "leaf": []},
            ),
            (
                "cycle-and-branch",
                [("alpha", "beta"), ("beta", "alpha"), ("beta", "leaf")],
                {"alpha": ["beta", "leaf"], "beta": ["alpha", "leaf"], "leaf": []},
            ),
            (
                "disconnected-diamond",
                [("alpha", "beta"), ("alpha", "leaf"), ("beta", "leaf")],
                {"alpha": ["beta", "leaf"], "beta": ["leaf"], "leaf": []},
            ),
        ]
        for name, edges, expected in cases:
            packages_map = build_packages_map(edges)
            for root_deps in ([], ["alpha"]):
                for start in NAMES:
                    self.assertEqual(
                        find_descendants(root_deps, packages_map, start),
                        expected[start],
                        "case=%s edges={%s} root_deps=%r query=%s"
                        % (name, graph_label(edges), root_deps, start),
                    )


class DescendantsLockfileConvention(unittest.TestCase):
    """组合测试的内存构造与 load_lockfile 返回约定逐字段一致。"""

    def test_in_memory_map_matches_loader(self):
        edges = [
            ("alpha", "alpha"),
            ("alpha", "beta"),
            ("beta", "alpha"),
            ("beta", "leaf"),
            ("leaf", "leaf"),
        ]
        data = {
            "lockfileVersion": 3,
            "packages": {"": {"dependencies": {"alpha": "1.0.0"}}},
        }
        # JSON 对象按 NAMES 顺序写入，依赖声明顺序刻意与边枚举一致，
        # 与 build_packages_map 的列表顺序直接可比。
        for name in NAMES:
            node = {"version": "1.0.0"}
            node_deps = {
                target: "1.0.0" for source, target in edges if source == name
            }
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
            # 依赖只指向这三个包。
            self.assertTrue(set(packages_map[name]["deps"]) <= set(NAMES))

    def test_empty_root_deps_lockfile_matches_in_memory(self):
        # 根不声明任何依赖：root_deps 为 []，三个包互相之间的边仍可查询。
        edges = [("leaf", "alpha"), ("alpha", "beta")]
        data = {"lockfileVersion": 3, "packages": {"": {}}}
        for name in NAMES:
            node = {"version": "1.0.0"}
            node_deps = {
                target: "1.0.0" for source, target in edges if source == name
            }
            if node_deps:
                node["dependencies"] = node_deps
            data["packages"]["node_modules/" + name] = node

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "lock.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            root_deps, packages_map = load_lockfile(path)

        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, build_packages_map(edges))
        for start in NAMES:
            self.assertEqual(
                find_descendants(root_deps, packages_map, start),
                reference_descendants(edges, start),
            )


if __name__ == "__main__":
    unittest.main()
