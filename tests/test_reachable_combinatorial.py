"""list --reachable 根可达清单的组合回归测试（独立核对，仅标准库、全程离线）。

在 alpha 1.0.0、beta 2.0.0、@scope/tool 3.0.0 三个已安装包之间枚举包含
自环的全部 9 条候选有向边（3×3）的所有组合，共 2^9 = 512 张图；根直接
依赖枚举三个包的全部 2^3 = 8 个子集。对每张图、每个根子集各核对两种
排列（条目/根依赖/包依赖声明原序与整体反转），并同时核对公开的
reachable_names 与 reachable_items 所描述的同一项行为：

- 期望结果由本模块独立的可达集不动点计算得出（根直接声明的包作为前沿
  播种，故即使没有任何边也保留；随后反复并入邻接直到集合不再增长），
  不调用 reachable_names、reachable_items 或其他产品查询来生成答案；
- 根直接声明的包及其逐层传递依赖都保留；同一包经多条路径、可达自环或
  循环到达只出现一次；根外孤立包与整体断开的循环一律排除；根依赖为空
  时可达集合与清单均为空；
- 清单按完整包名的 Unicode 码点升序排列（"@scope/tool" < "alpha" <
  "beta"），每项仅有 name、version、direct 三个键，版本字符串原样
  保留，direct 只由根 dependencies 是否直接声明该包决定；
- reachable_items 的名称集合必须与 reachable_names 的返回集合完全一致；
- 每次调用前后，root_deps 与 packages_map（含各依赖列表）的内容和顺序
  保持不变。

另用内存数据与真实 npm v3 平铺锁文件逐字段核对 load_lockfile 的返回
约定（含 node_modules/@scope/tool 路径），用仓库内现有 sample-lock.json
核对加载后的接口结果与命令行结果，并钉住“整份校验先于筛选”的边界：
不可达条目的 dependencies 为 null 或指向未安装 ghost 时，加载即抛
InputError，命令退出码 2、stdout 为空、stderr 仅 "INPUT_ERROR\\n"，
不输出部分结果或堆栈。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, load_lockfile, reachable_items, reachable_names

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_LOCK = os.path.join(REPO_ROOT, "sample-lock.json")

NAMES = ("alpha", "beta", "@scope/tool")
VERSIONS = {"alpha": "1.0.0", "beta": "2.0.0", "@scope/tool": "3.0.0"}
# 含自环的全部候选有向边：3 个包两两组合（含 source == target）共 9 条。
CANDIDATE_EDGES = [(source, target) for source in NAMES for target in NAMES]


def build_packages_map(edges, swapped=False):
    """按 load_lockfile 返回约定构造 packages_map，版本各自固定。

    swapped 为 True 时整体反转包条目顺序与每个包的依赖列表顺序；图本身
    不变，用于证明可达结果与条目、依赖声明的书写顺序无关。
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


def reference_reachable(edges, root_deps):
    """独立期望：邻接集上自根依赖出发的可达集不动点。

    根直接声明的包先整体并入可达集（即使没有任何边也保留），随后反复把
    前沿节点的邻接并入，直到没有新增节点。集合天然去重：多路径汇合、
    自环与循环都不会产生重复成员，循环正常终止；根未声明且无路径到达的
    包（包括整体断开的循环）保持不可达。与产品的 BFS 实现互不相关。
    """
    adjacency = {name: set() for name in NAMES}
    for source, target in edges:
        adjacency[source].add(target)

    reachable = set(root_deps)
    frontier = set(root_deps)
    while frontier:
        frontier = {
            nxt
            for node in frontier
            for nxt in adjacency[node]
            if nxt not in reachable
        }
        reachable |= frontier
    return reachable


def reference_items(edges, root_deps):
    """独立期望清单：可达集按完整包名 Unicode 码点升序排列后的条目。

    版本原样取自固定版本表；direct 只看根 dependencies 是否直接声明。
    Python 字符串比较即按 Unicode 码点：@scope/tool < alpha < beta。
    """
    direct = set(root_deps)
    return [
        {
            "name": name,
            "version": VERSIONS[name],
            "direct": name in direct,
        }
        for name in sorted(reference_reachable(edges, root_deps))
    ]


def all_root_subsets():
    """三个包的全部 8 个根直接依赖子集，按 NAMES 顺序给出（含空集）。"""
    subsets = []
    for mask in range(1 << len(NAMES)):
        subsets.append(
            tuple(NAMES[i] for i in range(len(NAMES)) if mask & (1 << i))
        )
    return subsets


def graph_label(edges):
    return ",".join("%s->%s" % edge for edge in edges) or "(no edges)"


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def lockfile_data(edges, root_deps, versions=None):
    """按 NAMES/edges 构造 npm v3 平铺锁文件数据（依赖值统一为 "*"）。"""
    versions = versions or VERSIONS
    data = {
        "lockfileVersion": 3,
        "packages": {"": {"dependencies": {dep: "*" for dep in root_deps}}},
    }
    for name in NAMES:
        node = {"version": versions[name]}
        node_deps = {
            target: "*" for source, target in edges if source == name
        }
        if node_deps:
            node["dependencies"] = node_deps
        data["packages"]["node_modules/" + name] = node
    return data


class ReachableCombinatorial(unittest.TestCase):
    """512 种边组合 × 8 个根依赖子集 × 两种排列的全量核对。"""

    def test_all_graphs_root_subsets_and_orders(self):
        for edge_mask in range(1 << len(CANDIDATE_EDGES)):
            edges = [
                CANDIDATE_EDGES[i]
                for i in range(len(CANDIDATE_EDGES))
                if edge_mask & (1 << i)
            ]
            edge_label = graph_label(edges)
            for root_deps in all_root_subsets():
                expected_set = reference_reachable(edges, root_deps)
                expected_list = reference_items(edges, root_deps)
                for order_tag, swapped in (("declared", False), ("reversed", True)):
                    packages_map = build_packages_map(edges, swapped=swapped)
                    # 反转排列时根依赖声明顺序也整体反转；direct 只取决于
                    # 成员资格，结果不应改变。
                    query_root = (
                        list(reversed(root_deps)) if swapped else list(root_deps)
                    )
                    context = "edges={%s} root_deps=%r order=%s" % (
                        edge_label,
                        query_root,
                        order_tag,
                    )
                    map_snapshot = copy.deepcopy(packages_map)
                    root_snapshot = list(query_root)

                    names = reachable_names(query_root, packages_map)
                    self.assertEqual(
                        names,
                        expected_set,
                        "%s: reachable_names set mismatch" % context,
                    )
                    items = reachable_items(query_root, packages_map)
                    self.assertEqual(
                        items,
                        expected_list,
                        "%s: reachable_items list mismatch" % context,
                    )

                    # 两个公开接口描述同一项行为：名称集合完全一致。
                    self.assertEqual(
                        {item["name"] for item in items},
                        names,
                        "%s: names/items disagree" % context,
                    )

                    # 清单按完整包名 Unicode 码点升序、无重复；每项只有
                    # name、version、direct；版本原样保留，direct 只由根
                    # 是否直接声明决定。
                    item_names = [item["name"] for item in items]
                    self.assertEqual(
                        item_names,
                        sorted(item_names),
                        "%s: items not sorted by full name" % context,
                    )
                    self.assertEqual(
                        len(item_names),
                        len(set(item_names)),
                        "%s: duplicate reachable items" % context,
                    )
                    direct_set = set(query_root)
                    for item in items:
                        self.assertEqual(
                            set(item.keys()),
                            {"name", "version", "direct"},
                            "%s: unexpected item keys" % context,
                        )
                        self.assertEqual(
                            item["version"],
                            VERSIONS[item["name"]],
                            "%s: version altered" % context,
                        )
                        self.assertEqual(
                            item["direct"],
                            item["name"] in direct_set,
                            "%s: direct flag mismatch" % context,
                        )

                    # 每次调用前后输入数据的内容及顺序一致。
                    self.assertEqual(
                        packages_map,
                        map_snapshot,
                        "%s: packages_map mutated" % context,
                    )
                    self.assertEqual(
                        query_root,
                        root_snapshot,
                        "%s: root_deps mutated" % context,
                    )

    def test_empty_root_subset_is_always_empty(self):
        # 钉住边界：空根依赖在任意图（含全包自环与三圈循环）下都产生
        # 空集合与空清单。
        cyclic_edges = [
            ("alpha", "alpha"),
            ("alpha", "beta"),
            ("beta", "@scope/tool"),
            ("@scope/tool", "alpha"),
        ]
        for edge_mask in range(1 << len(CANDIDATE_EDGES)):
            edges = [
                CANDIDATE_EDGES[i]
                for i in range(len(CANDIDATE_EDGES))
                if edge_mask & (1 << i)
            ]
            packages_map = build_packages_map(edges)
            self.assertEqual(
                reachable_names([], packages_map),
                set(),
                "edges={%s}: empty root must reach nothing" % graph_label(edges),
            )
            self.assertEqual(
                reachable_items([], packages_map),
                [],
                "edges={%s}: empty root must list nothing" % graph_label(edges),
            )
        # 含自环的三节点循环同样整体排除。
        packages_map = build_packages_map(cyclic_edges)
        self.assertEqual(reachable_names([], packages_map), set())
        self.assertEqual(reachable_items([], packages_map), [])

    def test_disconnected_cycle_and_isolated_nodes_excluded(self):
        # 根只声明 alpha 且 alpha 无任何出边：beta 与 @scope/tool 之间的
        # 循环（含 @scope/tool 自环）与根断开，整体排除；beta 即使被
        # 孤立声明也不出现。
        edges = [
            ("beta", "@scope/tool"),
            ("@scope/tool", "beta"),
            ("@scope/tool", "@scope/tool"),
        ]
        for order_tag, swapped in (("declared", False), ("reversed", True)):
            packages_map = build_packages_map(edges, swapped=swapped)
            context = "disconnected-cycle order=%s" % order_tag
            self.assertEqual(
                reachable_names(["alpha"], packages_map),
                {"alpha"},
                context,
            )
            self.assertEqual(
                reachable_items(["alpha"], packages_map),
                [{"name": "alpha", "version": "1.0.0", "direct": True}],
                context,
            )

    def test_direct_root_dependencies_kept_without_edges(self):
        # 根直接声明的包即使没有任何出边也全部保留，direct 全为 true。
        packages_map = build_packages_map([])
        for root_deps in all_root_subsets():
            items = reachable_items(list(root_deps), packages_map)
            self.assertEqual(
                [item["name"] for item in items],
                sorted(root_deps),
                "root_deps=%r" % (root_deps,),
            )
            self.assertTrue(
                all(item["direct"] is True for item in items),
                "root_deps=%r: direct flags" % (root_deps,),
            )

    def test_reachable_self_loop_and_cycle_dont_duplicate(self):
        edges = [
            ("alpha", "alpha"),
            ("alpha", "beta"),
            ("beta", "beta"),
            ("beta", "@scope/tool"),
            ("@scope/tool", "alpha"),
        ]
        packages_map = build_packages_map(edges)
        names = reachable_names(["alpha"], packages_map)
        items = reachable_items(["alpha"], packages_map)
        self.assertEqual(names, {"alpha", "beta", "@scope/tool"})
        self.assertEqual(
            items,
            [
                {"name": "@scope/tool", "version": "3.0.0", "direct": False},
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )


class ReachableLockfileConvention(unittest.TestCase):
    """组合测试的内存构造与 load_lockfile 的真实返回逐字段一致。"""

    def test_in_memory_map_matches_loader(self):
        edges = [
            ("alpha", "alpha"),
            ("alpha", "beta"),
            ("beta", "@scope/tool"),
            ("@scope/tool", "alpha"),
        ]
        data = lockfile_data(edges, ("beta",))
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lockfile(tmp, data)
            root_deps, packages_map = load_lockfile(path)

        # JSON 按 NAMES 顺序书写，与 build_packages_map 的列表顺序可比。
        self.assertEqual(root_deps, ["beta"])
        self.assertEqual(packages_map, build_packages_map(edges))
        self.assertEqual(packages_map["alpha"]["version"], "1.0.0")
        self.assertEqual(packages_map["beta"]["version"], "2.0.0")
        self.assertEqual(packages_map["@scope/tool"]["version"], "3.0.0")
        self.assertEqual(
            reachable_names(root_deps, packages_map),
            reference_reachable(edges, root_deps),
        )
        self.assertEqual(
            reachable_items(root_deps, packages_map),
            reference_items(edges, root_deps),
        )

    def test_empty_root_deps_lockfile_matches_in_memory(self):
        edges = [("@scope/tool", "beta"), ("beta", "alpha"), ("alpha", "alpha")]
        data = lockfile_data(edges, ())
        # 空根依赖：根节点 dependencies 写成空对象。
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lockfile(tmp, data)
            root_deps, packages_map = load_lockfile(path)

        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, build_packages_map(edges))
        self.assertEqual(reachable_names(root_deps, packages_map), set())
        self.assertEqual(reachable_items(root_deps, packages_map), [])


class SampleLockCliReachable(unittest.TestCase):
    """现有 sample-lock.json 的加载接口与 list --reachable 命令行结果。"""

    EXPECTED = [
        {"name": "alpha", "version": "1.0.0", "direct": True},
        {"name": "beta", "version": "1.0.0", "direct": False},
        {"name": "leaf", "version": "1.0.0", "direct": False},
    ]

    def test_loaded_api_matches_expected(self):
        root_deps, packages_map = load_lockfile(SAMPLE_LOCK)
        self.assertEqual(reachable_items(root_deps, packages_map), self.EXPECTED)
        self.assertEqual(
            reachable_names(root_deps, packages_map),
            {"alpha", "beta", "leaf"},
        )

    def test_cli_reachable_exact_output(self):
        before = read_bytes(SAMPLE_LOCK)
        result = run_cli(["list", SAMPLE_LOCK, "--reachable"])
        after = read_bytes(SAMPLE_LOCK)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 单个 JSON 数组，且恰好以一个换行结束。
        self.assertEqual(
            result.stdout,
            json.dumps(self.EXPECTED, ensure_ascii=False) + "\n",
        )
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))
        payload = json.loads(result.stdout)
        self.assertIsInstance(payload, list)
        self.assertEqual(payload, self.EXPECTED)
        # 读操作不得改动样例文件字节。
        self.assertEqual(after, before)


class InvalidUnreachableEntryRejectedBeforeFiltering(unittest.TestCase):
    """不可达条目的非法 dependencies 仍在筛选前拒绝整份输入。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def base_data(self):
        # 根只声明 alpha 且 alpha 无出边；stray 不可达，其非法依赖
        # 也必须在整份校验阶段被发现。
        data = lockfile_data([], ("alpha",))
        data["packages"]["node_modules/stray"] = {"version": "9.9.9"}
        return data

    def assert_rejected_before_filtering(self, path):
        before = read_bytes(path)

        with self.assertRaises(InputError):
            load_lockfile(path)

        result = run_cli(["list", path, "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")

        # 失败调用同样不得改动输入文件字节。
        self.assertEqual(read_bytes(path), before)

    def test_null_dependencies_on_unreachable_entry_rejected(self):
        data = self.base_data()
        data["packages"]["node_modules/stray"]["dependencies"] = None
        self.assert_rejected_before_filtering(
            write_lockfile(self.tmp, data, "stray-null.json")
        )

    def test_dependency_on_uninstalled_ghost_rejected(self):
        data = self.base_data()
        data["packages"]["node_modules/stray"]["dependencies"] = {"ghost": "*"}
        self.assert_rejected_before_filtering(
            write_lockfile(self.tmp, data, "stray-ghost.json")
        )


if __name__ == "__main__":
    unittest.main()
