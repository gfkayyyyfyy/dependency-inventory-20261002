"""find_ancestors 全部上游查询（不带 --reachable）的组合回归测试（独立核对）。

在 alpha、beta、leaf 三个已安装包之间枚举包含自环的全部 9 条候选有向边
（3×3）的所有组合，共 2^9 = 512 张图，对每张图逐一查询三个目标：

- 期望结果由本模块独立的反向可达集不动点计算得出（沿自身 dependencies
  至少一条边能到达目标的其他已安装包；直接与传递上游都保留；目标自身与根
  项目始终排除；按完整包名 Unicode 码点升序排列且无重复），不调用
  find_ancestors、其他产品查询函数或任何私有遍历函数来生成答案；
- 同一张图分别采用空根依赖与根只声明 alpha 两种根依赖：不带
  --reachable 的上游查询覆盖整份清单，根不可达的包照样参与，故两种根
  依赖下任一目标的结果都相同；
- 再颠倒包条目顺序与每个包的依赖列表顺序（图本身不变），结果仍一致；
- 每次查询后，根依赖、包版本及各依赖列表的内容和顺序均保持不变。

自环（无边或仅自环返回空列表；自环不能让目标进入结果）、循环
（alpha<->beta 且 beta->leaf 时查 leaf 得 ["alpha","beta"]、查 alpha
得 ["beta"]，循环中的其他上游保留）与根断开关系（与根断开的声明者仍是
上游）都由 512 张图自然覆盖。

组合图验证只做函数级调用，不逐图启动命令行进程；命令行只对仓库已有
sample-lock.json 的 leaf 查询核对一次，且不改写该样例。全程离线、仅
使用标准库。内存数据遵循 load_lockfile 的返回数据约定：
(root_deps, {name: {"version", "deps"}})，三个包版本统一为 1.0.0，
依赖只指向这三个包。
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
    用于证明查询结果与条目、依赖声明及遍历书写顺序无关。
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
    """独立期望：反向邻接集上的可达集不动点，与产品实现互不相关。

    祖先为沿自身 dependencies 经过至少一条边能到达 target 的全部包名：
    先依据边集独立构建反向邻接 dep -> {声明 dep 的包}，前沿直接由 target
    的直接声明者播种（至少一条边），随后反复并入前沿节点的声明者，直到
    集合不再增长；target 自身在结果中显式剔除，故自环与沿循环回到 target
    都不把 target 计入，循环中的其他包保留。根依赖不参与计算——根项目不
    是图节点，与根断开的包只要能到达 target 同样保留。最后按完整包名
    Unicode 码点升序排列（Python 字符串比较即按码点）；集合天然去重。
    """
    reverse = {name: set() for name in NAMES}
    for source, dep in edges:
        reverse[dep].add(source)

    reachable = set()
    frontier = set(reverse[target])
    while frontier:
        reachable |= frontier
        frontier = {
            declarer
            for node in frontier
            for declarer in reverse[node]
            if declarer not in reachable
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
            # 期望表只依赖边与传递关系本身，与根依赖、条目及依赖书写
            # 顺序无关。
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
                            "%s query=%s: queried package must be excluded"
                            % (context, target),
                        )
                        # 不带 --reachable：根不可达的包也参与查询，
                        # 两种根依赖共用同一期望表即证明答案不受根依赖
                        # 影响（另见 test_root_deps_never_change_the_answer）。
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

    def test_root_deps_never_change_the_answer(self):
        """两种根依赖下每个目标的结果逐一相等（512 张图全覆盖）。"""
        for mask in range(1 << len(CANDIDATE_EDGES)):
            edges = [
                CANDIDATE_EDGES[i]
                for i in range(len(CANDIDATE_EDGES))
                if mask & (1 << i)
            ]
            packages_map = build_packages_map(edges)
            for target in NAMES:
                with_empty_root = find_ancestors([], packages_map, target)
                with_alpha_root = find_ancestors(["alpha"], packages_map, target)
                self.assertEqual(
                    with_empty_root,
                    with_alpha_root,
                    "edges={%s} query=%s: root_deps must not affect ancestors"
                    % (graph_label(edges), target),
                )

    def test_expected_shapes_on_key_graphs(self):
        """对组合空间中几张代表性图钉住具体期望，便于失败时人工复现。

        - 无边 / 仅自环：三个包都返回空列表，自环不能让目标进入结果；
        - alpha<->beta 循环且 beta 指向 leaf：查 leaf 得 ["alpha","beta"]，
          查 alpha 得 ["beta"]，循环中的另一成员保留、leaf 自身排除；
        - 与根完全断开的声明者仍是上游：空根依赖下 alpha 声明 leaf，
          leaf 的上游照常包含 alpha。
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
                "disconnected-upstream",
                [("alpha", "leaf")],
                {"alpha": [], "beta": [], "leaf": ["alpha"]},
            ),
        ]
        for name, edges, expected in cases:
            packages_map = build_packages_map(edges)
            swapped_map = build_packages_map(edges, swapped=True)
            for root_deps in ([], ["alpha"]):
                for order_tag, candidate_map in (
                    ("declared", packages_map),
                    ("swapped", swapped_map),
                ):
                    for target in NAMES:
                        self.assertEqual(
                            find_ancestors(root_deps, candidate_map, target),
                            expected[target],
                            "case=%s edges={%s} root_deps=%r order=%s query=%s"
                            % (
                                name,
                                graph_label(edges),
                                root_deps,
                                order_tag,
                                target,
                            ),
                        )


class AncestorsLockfileConvention(unittest.TestCase):
    """组合测试的内存构造与 load_lockfile 返回约定逐字段一致。"""

    def _write_and_load(self, data):
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

        root_deps, packages_map = self._write_and_load(data)

        self.assertEqual(root_deps, ["alpha"])
        self.assertEqual(packages_map, build_packages_map(edges))
        for name in NAMES:
            self.assertEqual(packages_map[name]["version"], "1.0.0")
            # 依赖只指向这三个包。
            self.assertTrue(set(packages_map[name]["deps"]) <= set(NAMES))

    def test_empty_root_deps_lockfile_matches_in_memory(self):
        # 根不声明任何依赖：root_deps 为 []，三个包互相之间的边仍可查询，
        # 根不可达的声明者照常进入上游结果。
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

        root_deps, packages_map = self._write_and_load(data)

        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, build_packages_map(edges))
        for target in NAMES:
            self.assertEqual(
                find_ancestors(root_deps, packages_map, target),
                reference_ancestors(edges, target),
            )


class AncestorsSampleLockCli(unittest.TestCase):
    """用仓库已有 sample-lock.json 核对一次真实命令行 leaf 查询。

    只读取样例，不改写它；组合图验证不在此逐图启动进程。
    sample-lock.json 中 alpha->beta、beta->leaf、leaf->beta、orphan->leaf，
    且 orphan 自根不可达：不带 --reachable 时
    leaf 的上游为 ["alpha","beta","orphan"]。
    """

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_sample_leaf_ancestors_document(self):
        result = self.run_cli(["ancestors", SAMPLE_LOCK, "leaf"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 单个 JSON 文档加恰好一个末尾换行。
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        expected_text = (
            '{"name": "leaf", "ancestors": '
            '["alpha", "beta", "orphan"]}\n'
        )
        self.assertEqual(result.stdout, expected_text)
        # 再以 JSON 解析独立核对文档形状：仅含 name 与 ancestors 两个字段。
        document = json.loads(result.stdout)
        self.assertEqual(set(document), {"name", "ancestors"})
        self.assertEqual(
            document,
            {"name": "leaf", "ancestors": ["alpha", "beta", "orphan"]},
        )

    def test_missing_target_ghost_raises_not_found(self):
        # 函数级查询：未安装目标 ghost 抛 NotFoundError（CLI 出口不在本
        # 组合模块重复，已由 tests/test_ancestors.py 固定）。
        root_deps, packages_map = load_lockfile(SAMPLE_LOCK)
        with self.assertRaises(NotFoundError):
            find_ancestors(root_deps, packages_map, "ghost")


if __name__ == "__main__":
    unittest.main()
