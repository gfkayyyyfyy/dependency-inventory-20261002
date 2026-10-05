"""find_ancestors 全部上游查询的组合回归测试（独立核对，不带 --reachable）。

在 alpha、beta、leaf 三个已安装包之间枚举包含自环的全部 9 条候选有向边
（3×3）的所有组合，共 2^9 = 512 张图，对每张图逐一查询三个目标：

- 期望结果由本模块独立的反向可达集不动点计算得出（沿被查包自身
  dependencies 至少一条边能到达目标的其他包；目标自身始终排除；按完整
  包名 Unicode 码点升序排列且无重复），不调用 find_ancestors 或其内部
  实现，也不借助其他产品查询函数来生成答案；
- 同一张图分别采用空根依赖与根只声明 alpha 两种根依赖，任一目标的结果
  都应相同（与根断开的包只要能到达目标同样进入结果）；
- 再颠倒包条目顺序与每个包的依赖列表顺序，结果仍一致；
- 每次查询后，根依赖、包版本及各依赖列表的内容和顺序均保持不变。

自环（无边或仅自环返回空列表，自环不能让目标进入结果）、循环（循环中的
其他上游保留）、分支与断开关系（根不可达的包照常参与）都由 512 张图
自然覆盖。

另含两条验收：仓库自带 sample-lock.json 上的真实命令行查询 leaf（仅启动
一次进程，组合图不逐图启动 CLI），以及未安装目标 ghost 的函数查询抛
NotFoundError。全程离线、仅使用标准库，不修改任何仓库样例。数据遵循
load_lockfile 的返回数据约定：(root_deps, {name: {"version", "deps"}})，
所有包版本统一为 1.0.0，依赖只指向这三个包。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import NotFoundError, find_ancestors, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_LOCK = os.path.join(REPO_ROOT, "sample-lock.json")

NAMES = ("alpha", "beta", "leaf")
# 含自环的全部候选有向边：3 个包两两组合（含 source == target）共 9 条。
CANDIDATE_EDGES = [(source, target) for source in NAMES for target in NAMES]


def build_packages_map(edges, swapped=False):
    """按 load_lockfile 返回约定构造 packages_map，版本统一 1.0.0。

    swapped 为 True 时颠倒包条目顺序与每个包的依赖列表顺序；图本身不变，
    用于证明查询结果与条目、依赖声明书写顺序无关。
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


def reference_ancestors(edges, target):
    """独立期望：反向邻接集上的可达集不动点，与产品的 BFS 实现互不相关。

    祖先为沿自身 dependencies 经过至少一条边能到达 target 的全部包名：
    先由边 source->target 构造反向邻接（target 的直接声明者），前沿直接
    由 target 的反向邻接播种（至少一条边），随后反复并入前沿节点的反向
    邻接，直到集合不再增长；target 自身在结果中显式剔除，故自环与回到
    target 的循环都不把 target 计入，循环中的其他上游保留。根依赖完全不
    参与计算——根不可达的包只要能到达 target 同样是祖先。最后按完整包名
    Unicode 码点升序排列（Python 字符串比较即按码点）；集合天然去重。
    """
    reverse = {name: set() for name in NAMES}
    for source, dest in edges:
        reverse[dest].add(source)

    reachable = set()
    frontier = set(reverse[target])
    while frontier:
        reachable |= frontier
        frontier = {
            prev
            for node in frontier
            for prev in reverse[node]
            if prev not in reachable
        }
    reachable.discard(target)
    return sorted(reachable)


def graph_label(edges):
    return ",".join("%s->%s" % edge for edge in edges) or "(no edges)"


class AncestorsCombinatorial(unittest.TestCase):
    """512 种边组合 × 两种根依赖 × 两种排列 × 三个查询目标的全量核对。"""

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
            # 期望表只依赖边与传递关系本身，与根依赖、条目及依赖书写顺序无关。
            expected_table = {
                target: reference_ancestors(edges, target) for target in NAMES
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
                    for target in NAMES:
                        actual = find_ancestors(root_deps, packages_map, target)
                        self.assertEqual(
                            actual,
                            expected_table[target],
                            "%s query=%s" % (context, target),
                        )
                        self.assertEqual(
                            len(actual),
                            len(set(actual)),
                            "%s query=%s: duplicates in result"
                            % (context, target),
                        )
                        self.assertNotIn(
                            target,
                            actual,
                            "%s query=%s: queried target must be excluded"
                            % (context, target),
                        )
                        # 每次查询后，根依赖、版本与依赖列表的内容和
                        # 顺序都保持不变。
                        self.assertEqual(
                            packages_map,
                            map_snapshot,
                            "%s query=%s: packages_map mutated"
                            % (context, target),
                        )
                        self.assertEqual(
                            root_deps,
                            root_snapshot,
                            "%s query=%s: root_deps mutated" % (context, target),
                        )

    def test_expected_shapes_on_key_graphs(self):
        """对组合空间中几张代表性图钉住具体期望，便于失败时人工复现。

        - 无边 / 仅自环：三个目标都返回空列表（自环不能让目标进入结果）；
        - alpha<->beta 循环且 beta 指向 leaf：查 leaf 得 ["alpha","beta"]，
          查 alpha 得 ["beta"]，循环中的另一成员保留；
        - 与根完全断开的声明者仍能成为祖先（根依赖不参与祖先计算）。
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
                {"alpha": ["beta"], "beta": ["alpha"], "leaf": ["alpha", "beta"]},
            ),
            (
                "disconnected-declarer",
                # leaf 声明 beta、beta 声明 alpha；根只声明 alpha 时，
                # leaf、beta 对 alpha 的上游关系依旧成立。
                [("leaf", "beta"), ("beta", "alpha")],
                {"alpha": ["beta", "leaf"], "beta": ["leaf"], "leaf": []},
            ),
        ]
        for name, edges, expected in cases:
            packages_map = build_packages_map(edges)
            for root_deps in ([], ["alpha"]):
                for target in NAMES:
                    self.assertEqual(
                        find_ancestors(root_deps, packages_map, target),
                        expected[target],
                        "case=%s edges={%s} root_deps=%r query=%s"
                        % (name, graph_label(edges), root_deps, target),
                    )

    def test_specified_cycle_example_exactly(self):
        # 任务明文钉住的例子：alpha 与 beta 互相依赖且 beta 依赖 leaf。
        edges = [("alpha", "beta"), ("beta", "alpha"), ("beta", "leaf")]
        packages_map = build_packages_map(edges)
        self.assertEqual(find_ancestors([], packages_map, "leaf"), ["alpha", "beta"])
        self.assertEqual(find_ancestors([], packages_map, "alpha"), ["beta"])
        self.assertEqual(
            find_ancestors(["alpha"], packages_map, "leaf"), ["alpha", "beta"]
        )
        self.assertEqual(
            find_ancestors(["alpha"], packages_map, "alpha"), ["beta"]
        )

    def test_missing_target_raises_not_found(self):
        # 未安装目标 ghost 的函数查询抛 NotFoundError，与根依赖、图内容无关。
        packages_map = build_packages_map(
            [("alpha", "beta"), ("beta", "leaf")]
        )
        for root_deps in ([], ["alpha"]):
            with self.assertRaises(NotFoundError):
                find_ancestors(root_deps, packages_map, "ghost")


class AncestorsLockfileConvention(unittest.TestCase):
    """组合测试的内存构造与 load_lockfile 返回约定逐字段一致。"""

    def _write_and_load(self, edges, root_deps):
        data = {
            "lockfileVersion": 3,
            "packages": {"": {}},
        }
        if root_deps:
            data["packages"][""]["dependencies"] = {
                name: "1.0.0" for name in root_deps
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
            return load_lockfile(path)

    def test_in_memory_map_matches_loader(self):
        edges = [
            ("alpha", "alpha"),
            ("alpha", "beta"),
            ("beta", "alpha"),
            ("beta", "leaf"),
            ("leaf", "leaf"),
        ]
        root_deps, packages_map = self._write_and_load(edges, ["alpha"])

        self.assertEqual(root_deps, ["alpha"])
        self.assertEqual(packages_map, build_packages_map(edges))
        for name in NAMES:
            self.assertEqual(packages_map[name]["version"], "1.0.0")
            # 依赖只指向这三个包。
            self.assertTrue(set(packages_map[name]["deps"]) <= set(NAMES))

    def test_empty_root_deps_lockfile_matches_in_memory(self):
        # 根不声明任何依赖：root_deps 为 []，三个包互相之间的边仍可查询。
        edges = [("leaf", "alpha"), ("alpha", "beta")]
        root_deps, packages_map = self._write_and_load(edges, [])

        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, build_packages_map(edges))
        for target in NAMES:
            self.assertEqual(
                find_ancestors(root_deps, packages_map, target),
                reference_ancestors(edges, target),
            )


class AncestorsSampleLockCli(unittest.TestCase):
    """用仓库自带 sample-lock.json 核对一次真实命令行查询（只读，不改样例）。

    alpha->beta->leaf 且 leaf->beta 构成循环；orphan 自根不可达但声明
    leaf，仍是 leaf 的上游。故 leaf 的祖先恰为 alpha、beta、orphan。
    """

    def test_sample_leaf_ancestors_document(self):
        result = subprocess.run(
            [sys.executable, "-m", "depinventory", "ancestors", SAMPLE_LOCK, "leaf"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 单个 JSON 文档加恰好一个末尾换行，且顶层只有 name 与 ancestors。
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(
            result.stdout,
            '{"name": "leaf", "ancestors": ["alpha", "beta", "orphan"]}\n',
        )
        document = json.loads(result.stdout)
        self.assertEqual(set(document), {"name", "ancestors"})
        self.assertEqual(
            document,
            {"name": "leaf", "ancestors": ["alpha", "beta", "orphan"]},
        )


if __name__ == "__main__":
    unittest.main()
