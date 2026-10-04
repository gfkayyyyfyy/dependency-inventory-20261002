"""list --reachable 根可达清单的组合回归测试（独立核对，仅标准库、全程离线）。

第一部分在 alpha、beta、@scope/tool 三个已安装包（版本分别为 1.0.0、
2.0.0、3.0.0）之间枚举包含自环的全部 9 条候选有向边（3×3）的所有组合，
共 2^9 = 512 张图；每张图再搭配根直接依赖的全部 2^3 = 8 种子集，并分别
按书写顺序与“条目、根依赖、包依赖列表全部反转”的顺序各核对一遍：

- 期望可达集由本模块独立的邻接集不动点计算得出（自根直接声明的包出发，
  反复并入前沿节点的邻接直到不再增长），不调用 reachable_names、
  reachable_items 或任何其他产品查询来生成答案；
- 同一组输入同时核对 reachable_names（集合）与 reachable_items（按完整
  包名 Unicode 码点升序的清单）所描述的同一项行为：两者包名一致，清单
  每项仅有 name、version、direct，版本原样保留，direct 只由根是否直接
  声明该包决定；根直接声明的包及其传递依赖都保留，可达自环与循环不产生
  重复，根外孤立包与断开的循环整体排除，空根依赖产生空集合与空清单；
- 每次调用前后，root_deps 与 packages_map 的内容及顺序保持不变；
- 失败信息定位到边组合、根依赖子集与排列方式。

第二部分用仓库现有的 sample-lock.json 核对加载后的接口与真实命令行：
python -m depinventory list sample-lock.json --reachable 只输出 alpha、
beta、leaf，版本均为 1.0.0，direct 依次为 true、false、false；退出码 0，
stderr 为空，stdout 是单个 JSON 数组并以一个换行结束。

第三部分核对“整份校验先于筛选”的边界：把 sample-lock.json 中不可达条目
的 dependencies 单独改成 null，或单独改成声明未安装的 ghost，加载仍抛
InputError，命令退出码 2、stdout 为空、stderr 仅为 "INPUT_ERROR\\n"，
不输出部分结果或堆栈，输入文件字节前后一致。

数据遵循 load_lockfile 的返回数据约定：(root_deps, {name: {"version",
"deps"}})。不逐组合启动命令行进程。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import (
    InputError,
    load_lockfile,
    reachable_items,
    reachable_names,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_LOCK = os.path.join(REPO_ROOT, "sample-lock.json")

VERSIONS = {"alpha": "1.0.0", "beta": "2.0.0", "@scope/tool": "3.0.0"}
NAMES = ("alpha", "beta", "@scope/tool")
# 含自环的全部候选有向边：3 个包两两组合（含 source == target）共 9 条。
CANDIDATE_EDGES = [(source, target) for source in NAMES for target in NAMES]

SAMPLE_EXPECTED_ITEMS = [
    {"name": "alpha", "version": "1.0.0", "direct": True},
    {"name": "beta", "version": "1.0.0", "direct": False},
    {"name": "leaf", "version": "1.0.0", "direct": False},
]


def build_packages_map(edges, swapped=False):
    """按 load_lockfile 返回约定构造 packages_map，版本取自 VERSIONS。

    swapped 为 True 时颠倒包条目顺序与每个包的依赖列表顺序；图本身不变，
    用于证明结果与条目及依赖声明的书写顺序无关。
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
        packages_map[name] = {"version": VERSIONS[name], "deps": dep_list}
    return packages_map


def reference_reachable(edges, root_subset):
    """独立期望：邻接集上的可达集不动点，与产品的 BFS 实现互不相关。

    自根直接声明的包出发，前沿反复并入前沿节点的邻接，直到集合不再增长；
    自环与循环由成员判定自然终止，根外孤立包与断开的循环从不进入前沿。
    返回集合，天然去重。
    """
    adjacency = {name: set() for name in NAMES}
    for source, target in edges:
        adjacency[source].add(target)

    reachable = set(root_subset)
    frontier = set(root_subset)
    while frontier:
        frontier = {
            nxt
            for node in frontier
            for nxt in adjacency[node]
            if nxt not in reachable
        }
        reachable |= frontier
    return reachable


def expected_items(reachable, root_subset):
    """由独立可达集推出清单：按完整包名 Unicode 码点升序，direct 只看根。"""
    return [
        {"name": name, "version": VERSIONS[name], "direct": name in root_subset}
        for name in sorted(reachable)
    ]


def graph_label(edges):
    return ",".join("%s->%s" % edge for edge in edges) or "(no edges)"


class ReachableCombinatorial(unittest.TestCase):
    """512 种边组合 × 8 种根依赖子集 × 2 种排列的全量核对。"""

    def test_all_edge_and_root_combinations(self):
        for mask in range(1 << len(CANDIDATE_EDGES)):
            edges = [
                CANDIDATE_EDGES[i]
                for i in range(len(CANDIDATE_EDGES))
                if mask & (1 << i)
            ]
            label = graph_label(edges)
            for root_mask in range(1 << len(NAMES)):
                root_subset = {
                    NAMES[i] for i in range(len(NAMES)) if root_mask & (1 << i)
                }
                # 期望只依赖图关系与根子集本身，与任何书写顺序无关。
                want_names = reference_reachable(edges, root_subset)
                want_items = expected_items(want_names, root_subset)
                for order_tag, swapped in (("declared", False), ("swapped", True)):
                    packages_map = build_packages_map(edges, swapped=swapped)
                    root_deps = [name for name in NAMES if name in root_subset]
                    if swapped:
                        root_deps.reverse()
                    context = "edges={%s} root_deps=%r order=%s" % (
                        label,
                        root_deps,
                        order_tag,
                    )
                    map_snapshot = copy.deepcopy(packages_map)
                    root_snapshot = list(root_deps)

                    got_names = reachable_names(root_deps, packages_map)
                    self.assertIsInstance(
                        got_names, set, context + ": names must be a set"
                    )
                    self.assertEqual(
                        got_names, want_names, context + ": reachable_names"
                    )

                    got_items = reachable_items(root_deps, packages_map)
                    self.assertEqual(
                        got_items, want_items, context + ": reachable_items"
                    )
                    # 两个公开接口描述同一项行为：清单包名即可达集合。
                    self.assertEqual(
                        {item["name"] for item in got_items},
                        got_names,
                        context + ": names/items disagree",
                    )
                    # 清单按完整包名 Unicode 码点升序且无重复。
                    item_names = [item["name"] for item in got_items]
                    self.assertEqual(
                        item_names,
                        sorted(item_names),
                        context + ": items not sorted by codepoint",
                    )
                    self.assertEqual(
                        len(item_names),
                        len(set(item_names)),
                        context + ": duplicate entries in items",
                    )
                    for item in got_items:
                        self.assertEqual(
                            set(item.keys()),
                            {"name", "version", "direct"},
                            context + ": unexpected item keys",
                        )
                        self.assertEqual(
                            item["version"],
                            VERSIONS[item["name"]],
                            context + ": version not preserved",
                        )
                        self.assertIs(
                            item["direct"],
                            item["name"] in root_subset,
                            context + ": direct must only reflect root deps",
                        )

                    # 每次调用前后，输入数据的内容及顺序保持一致。
                    self.assertEqual(
                        packages_map,
                        map_snapshot,
                        context + ": packages_map mutated",
                    )
                    self.assertEqual(
                        root_deps,
                        root_snapshot,
                        context + ": root_deps mutated",
                    )

    def test_expected_shapes_on_key_graphs(self):
        """对组合空间中几张代表性图钉住具体期望，便于失败时人工复现。

        - 空根依赖：任何图（含合法循环）都产生空集合与空清单；
        - 仅自环：根声明的包可达，自环不产生重复，未声明的包排除；
        - 根经 alpha 进入 alpha<->beta 循环：两包保留且无重复；
        - 与根断开的循环整体排除，即使其内部边完整。
        """
        cases = [
            (
                "empty-roots",
                [("alpha", "beta"), ("beta", "alpha")],
                [],
                set(),
            ),
            (
                "self-loops-only",
                [("alpha", "alpha"), ("beta", "beta"), ("@scope/tool", "@scope/tool")],
                ["alpha", "@scope/tool"],
                {"alpha", "@scope/tool"},
            ),
            (
                "cycle-via-root",
                [("alpha", "beta"), ("beta", "alpha")],
                ["alpha"],
                {"alpha", "beta"},
            ),
            (
                "disconnected-cycle-excluded",
                [("alpha", "beta"), ("beta", "alpha")],
                ["@scope/tool"],
                {"@scope/tool"},
            ),
        ]
        for name, edges, root_deps, want_names in cases:
            packages_map = build_packages_map(edges)
            context = "case=%s edges={%s} root_deps=%r" % (
                name,
                graph_label(edges),
                root_deps,
            )
            self.assertEqual(
                reachable_names(root_deps, packages_map), want_names, context
            )
            self.assertEqual(
                reachable_items(root_deps, packages_map),
                expected_items(want_names, set(root_deps)),
                context,
            )


class SampleLockReachable(unittest.TestCase):
    """用现有 sample-lock.json 核对加载后的接口与命令行结果。"""

    def test_api_results_on_sample_lock(self):
        before = read_bytes(SAMPLE_LOCK)
        root_deps, packages_map = load_lockfile(SAMPLE_LOCK)

        self.assertEqual(
            reachable_names(root_deps, packages_map), {"alpha", "beta", "leaf"}
        )
        self.assertEqual(
            reachable_items(root_deps, packages_map), SAMPLE_EXPECTED_ITEMS
        )
        # 读取与查询不得改动输入文件字节。
        self.assertEqual(read_bytes(SAMPLE_LOCK), before)

    def test_cli_reachable_on_sample_lock(self):
        before = read_bytes(SAMPLE_LOCK)
        result = subprocess.run(
            [sys.executable, "-m", "depinventory", "list", "sample-lock.json",
             "--reachable"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 单个 JSON 数组，且恰好以一个换行结束。
        self.assertEqual(
            result.stdout,
            json.dumps(SAMPLE_EXPECTED_ITEMS, ensure_ascii=False) + "\n",
        )
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))
        self.assertEqual(json.loads(result.stdout), SAMPLE_EXPECTED_ITEMS)
        self.assertEqual(
            [item["direct"] for item in json.loads(result.stdout)],
            [True, False, False],
        )
        self.assertEqual(read_bytes(SAMPLE_LOCK), before)


class InvalidUnreachableEntryRejected(unittest.TestCase):
    """不可达条目的非法 dependencies 仍在筛选前拒绝整份输入。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        with open(SAMPLE_LOCK, "r", encoding="utf-8") as handle:
            self.sample = json.load(handle)

    def tearDown(self):
        self._tmp.cleanup()

    def write_variant(self, mutate, name):
        data = copy.deepcopy(self.sample)
        mutate(data)
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        return path

    def assert_rejected_before_filtering(self, path):
        before = read_bytes(path)

        with self.assertRaises(InputError):
            root_deps, packages_map = load_lockfile(path)
            reachable_names(root_deps, packages_map)
            reachable_items(root_deps, packages_map)

        result = subprocess.run(
            [sys.executable, "-m", "depinventory", "list", path, "--reachable"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        # 不输出部分结果或堆栈：stdout 为空，stderr 仅 INPUT_ERROR 加换行。
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")

        # 失败调用同样不得改动输入文件字节。
        self.assertEqual(read_bytes(path), before)

    def test_null_dependencies_on_unreachable_orphan_rejected(self):
        path = self.write_variant(
            lambda data: data["packages"]["node_modules/orphan"].update(
                dependencies=None
            ),
            "orphan-null.json",
        )
        self.assert_rejected_before_filtering(path)

    def test_ghost_dependency_on_unreachable_isolated_rejected(self):
        path = self.write_variant(
            lambda data: data["packages"]["node_modules/isolated"].update(
                dependencies={"ghost": "*"}
            ),
            "isolated-ghost.json",
        )
        self.assert_rejected_before_filtering(path)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


if __name__ == "__main__":
    unittest.main()
