"""find_descendants 全部下游查询的组合回归测试（独立核对）。

在 alpha、beta、leaf 三个已安装包之间枚举包含自环在内的全部 9 条可能
有向依赖边（3 个源 × 3 个目标，含 a->a），共 2^9 = 512 张图，并对每张
图逐一查询三个包：

- 期望结果由本模块独立计算：先按边集合构造邻接矩阵，再用 Floyd-Warshall
  求传递闭包，下游即查询包经至少一条边可达、且不等于查询包自身的全部
  包名，按完整包名 Unicode 码点升序排列（Python 字符串比较即按码点）。
  不调用 find_descendants 或其内部辅助函数生成答案，也不复用 BFS 实现；
- 同一图分别采用空根依赖与根只声明 alpha 两种根依赖，查询结果应相同
  （find_descendants 只沿查询包自身的 dependencies 展开）；
- 再颠倒包条目与每个包的依赖列表顺序，结果仍相同：自环不产生查询包
  自身、无边或仅自环返回空列表、循环中的其他下游保留、断开关系不影响
  从查询包出发的展开；
- 每轮查询前后，根依赖、包版本及各依赖列表的内容与顺序均保持不变。

另有少量固定用例把自环、断开与三类循环的意图写明，以及一组用例确认
内存构造的数据与 load_lockfile 的返回约定一致。

全程离线、仅使用标准库、不逐图启动命令行进程。数据遵循 load_lockfile
的返回数据约定：(root_deps, {name: {"version", "deps"}})，三个包版本
统一为 1.0.0，依赖只指向这三个包。
"""

import copy
import json
import os
import tempfile
import unittest

from depinventory import find_descendants, load_lockfile

NAMES = ("alpha", "beta", "leaf")
# 包含自环的全部候选有向边：3 个源 × 3 个目标 = 9 条。
CANDIDATE_EDGES = [(source, target) for source in NAMES for target in NAMES]


def build_packages_map(edges, swapped=False):
    """按 load_lockfile 返回约定构造 packages_map，版本统一 1.0.0。

    swapped 为 True 时颠倒包条目顺序与每个包的依赖列表顺序；图本身
    （边集合）保持不变。
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


def reference_descendants_table(edges):
    """独立期望：Floyd-Warshall 传递闭包给出每个查询包的全部下游。

    reach[a][b] 为真表示沿 dependencies 经过至少一条边可由 a 到达 b
    （对角线仅在自环或回到自身的循环存在时为真）。结果排除查询包自身，
    故自环与回到自身的循环都不会让查询包出现在自己的下游中；循环中的
    其他包、以及与根节点断开但自查询包可达的包照常保留。
    """
    index = {name: i for i, name in enumerate(NAMES)}
    reach = [[False] * len(NAMES) for _ in NAMES]
    for source, target in edges:
        reach[index[source]][index[target]] = True
    for mid in range(len(NAMES)):
        for source in range(len(NAMES)):
            if not reach[source][mid]:
                continue
            row = reach[source]
            mid_row = reach[mid]
            for target in range(len(NAMES)):
                if mid_row[target]:
                    row[target] = True
    table = {}
    for name in NAMES:
        source = index[name]
        table[name] = sorted(
            other for other in NAMES if other != name and reach[source][index[other]]
        )
    return table


def graph_label(edges):
    return ",".join("%s->%s" % edge for edge in edges) or "(no edges)"


class DescendantsCombinatorial(unittest.TestCase):
    """512 种边集合 × 三种根依赖/排列情况 × 三个查询包的全量核对。"""

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
            # 期望表只依赖边集合本身，与根依赖、条目及依赖书写顺序无关。
            expected_table = reference_descendants_table(edges)
            for root_deps in ([], ["alpha"]):
                for order_tag, packages_map in (
                    ("declared", base_map),
                    ("swapped", swapped_map),
                ):
                    map_snapshot = copy.deepcopy(packages_map)
                    root_snapshot = list(root_deps)
                    for query in NAMES:
                        with self.subTest(
                            graph=label, query=query, root=root_deps, order=order_tag
                        ):
                            actual = find_descendants(
                                root_deps, packages_map, query
                            )
                            expected = expected_table[query]
                            self.assertEqual(
                                actual,
                                expected,
                                "graph=%s query=%s root=%r order=%s"
                                % (label, query, root_deps, order_tag),
                            )
                            # 结果自身无重复，且始终不含查询包。
                            self.assertEqual(
                                len(actual),
                                len(set(actual)),
                                "graph=%s query=%s root=%r order=%s: "
                                "duplicate names in result"
                                % (label, query, root_deps, order_tag),
                            )
                            self.assertNotIn(
                                query,
                                actual,
                                "graph=%s query=%s root=%r order=%s: "
                                "query name must never be its own descendant"
                                % (label, query, root_deps, order_tag),
                            )
                    # 一轮查询后：包版本、根依赖及各依赖列表的内容与
                    # 顺序均保持不变（dict 相等同时约束键的插入顺序）。
                    self.assertEqual(
                        packages_map,
                        map_snapshot,
                        "graph=%s root=%r order=%s: packages_map mutated"
                        % (label, root_deps, order_tag),
                    )
                    self.assertEqual(
                        root_deps,
                        root_snapshot,
                        "graph=%s root=%r order=%s: root_deps mutated"
                        % (label, root_deps, order_tag),
                    )
                    for name in NAMES:
                        self.assertEqual(
                            packages_map[name]["version"],
                            "1.0.0",
                            "graph=%s root=%r order=%s: version of %s changed"
                            % (label, root_deps, order_tag, name),
                        )
                        self.assertEqual(
                            packages_map[name]["deps"],
                            map_snapshot[name]["deps"],
                            "graph=%s root=%r order=%s: deps of %s mutated"
                            % (label, root_deps, order_tag, name),
                        )


class DescendantsFixedCases(unittest.TestCase):
    """把无边、仅自环、断开与循环场景的预期写明（均被 512 枚举覆盖）。"""

    def test_no_edges_or_self_loop_only_returns_empty(self):
        # 无边：三个包互无依赖。
        packages_map = {name: {"version": "1.0.0", "deps": []} for name in NAMES}
        for query in NAMES:
            with self.subTest(graph="no edges", query=query):
                self.assertEqual(find_descendants([], packages_map, query), [])
                self.assertEqual(
                    find_descendants(["alpha"], packages_map, query), []
                )
        # 仅自环：每个包只声明自身，查询包自身排除后仍为空。
        loop_map = {
            name: {"version": "1.0.0", "deps": [name]} for name in NAMES
        }
        for query in NAMES:
            with self.subTest(graph="self loops only", query=query):
                self.assertEqual(find_descendants([], loop_map, query), [])

    def test_three_node_cycle_keeps_other_members(self):
        # alpha -> beta -> leaf -> alpha：每个包的下游都是另外两个，
        # 循环回到查询包自身时不把它计入，结果按码点升序。
        edges = [("alpha", "beta"), ("beta", "leaf"), ("leaf", "alpha")]
        packages_map = build_packages_map(edges)
        self.assertEqual(
            find_descendants([], packages_map, "alpha"), ["beta", "leaf"]
        )
        self.assertEqual(
            find_descendants([], packages_map, "beta"), ["alpha", "leaf"]
        )
        self.assertEqual(
            find_descendants([], packages_map, "leaf"), ["alpha", "beta"]
        )

    def test_disconnected_components_expand_from_query_alone(self):
        # alpha<->beta 构成一个与 leaf 断开的循环；根依赖为空或只声明
        # alpha 都不影响查询包沿自身依赖展开。
        edges = [("alpha", "beta"), ("beta", "alpha")]
        packages_map = build_packages_map(edges)
        for root_deps in ([], ["alpha"]):
            self.assertEqual(
                find_descendants(root_deps, packages_map, "alpha"), ["beta"]
            )
            self.assertEqual(
                find_descendants(root_deps, packages_map, "beta"), ["alpha"]
            )
            # leaf 与该循环断开：无下游；反过来 alpha/beta 也到不了 leaf。
            self.assertEqual(find_descendants(root_deps, packages_map, "leaf"), [])


class DescendantsLockfileConvention(unittest.TestCase):
    """组合测试的内存构造与 load_lockfile 返回约定一致。"""

    def test_in_memory_map_matches_loader(self):
        edges = [
            ("alpha", "alpha"),
            ("alpha", "beta"),
            ("beta", "leaf"),
            ("leaf", "beta"),
        ]
        data = {
            "lockfileVersion": 3,
            "packages": {"": {"dependencies": {"alpha": "1.0.0"}}},
        }
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
        # 同一数据下两种查询路径给出一致结果。
        for name in NAMES:
            self.assertEqual(
                find_descendants(root_deps, packages_map, name),
                find_descendants([], build_packages_map(edges), name),
            )


if __name__ == "__main__":
    unittest.main()
