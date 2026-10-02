"""find_path 内部重构后的规模与兼容性回归测试（仅标准库、全程离线）。

针对两类固定规模的合法平铺 v3 锁文件验证 O(V+E) 重构：
1. 宽依赖图：根节点直接声明 5000 个包，查询名称排序最后的包，
   结果必须仍是 ["$root", name] 两项路径；
2. 长依赖链：根节点连接 2000 个包组成的单链，链尾再连 2000 个不同叶子，
   查询最后一个叶子，结果必须完整包含根节点、全部链节点与该叶子，顺序无误。

除结果正确性外，本模块还固定以下行为，全部与具体机器的墙钟时间无关：
- 查询不修改传入的根依赖列表、包版本与依赖列表（深拷贝快照逐元素比较，
  包括声明顺序），重复查询同一份数据得到相同结果；
- 线性上界通过“每个节点的邻接表至多被扫描一次”的访问计数验证，
  不以耗时阈值代替复杂度验证；
- 既有等长路径字典序、循环/自依赖、不可达结论在此模块用独立构造的
  样例再次确认，保证重构后查询结果兼容。
"""

import copy
import json
import os
import tempfile
import unittest
from collections.abc import Sequence

from depinventory import ROOT, NotFoundError, find_path, load_lockfile

_DIRECT_COUNT = 5000
_CHAIN_COUNT = 2000
_LEAF_COUNT = 2000


def _pkg(version="1.0.0", deps=None):
    node = {"version": version}
    if deps is not None:
        node["dependencies"] = dict(deps)
    return node


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def wide_lock_data():
    """根节点直接声明 _DIRECT_COUNT 个包的宽依赖图。

    包名统一 8 位宽（d00000000..d00004999），名称排序次序与编号次序一致，
    目标名称即为排序最后的包；刻意按逆序声明以确认结果与声明顺序无关。
    另加入一个根不可达的包，保证 V 大于被访问节点数时上界断言仍成立。
    """
    names = ["d{:08d}".format(i) for i in range(_DIRECT_COUNT)]
    packages = {"": {"dependencies": {name: "1.0.0" for name in reversed(names)}}}
    for name in names:
        packages["node_modules/" + name] = {"version": "1.0.0"}
    packages["node_modules/zz-unreachable"] = {"version": "1.0.0"}
    return {"lockfileVersion": 3, "packages": packages}


def chain_lock_data():
    """根节点 -> 2000 个链节点组成的单链 -> 链尾连 2000 个不同叶子。

    链节点名为 c0000..c1999（4 位宽，c1999 排序最后），叶子名为
    a-leaf-0000..a-leaf-1999，使叶子整体排在链节点之前。目标取
    a-leaf-1999：其最短路径必须经整链到达链尾，长度 2002。
    叶子按降序声明，证明字典序结果不取决于 dependencies 的书写顺序；
    链尾再回指第一个链节点构成循环，验证长链上的循环边被即时剪枝。
    """
    chain = ["c{:04d}".format(i) for i in range(_CHAIN_COUNT)]
    leaves = ["a-leaf-{:04d}".format(i) for i in range(_LEAF_COUNT)]
    packages = {
        "": {"dependencies": {chain[0]: "1.0.0"}},
        "node_modules/" + chain[0]: _pkg(deps=[(chain[1], "1.0.0")]),
    }
    for i in range(1, _CHAIN_COUNT - 1):
        packages["node_modules/" + chain[i]] = _pkg(deps=[(chain[i + 1], "1.0.0")])
    # 链尾：按降序声明 2000 个叶子，并回指 chain[0] 形成可达循环。
    packages["node_modules/" + chain[-1]] = _pkg(
        deps=[(name, "1.0.0") for name in reversed(leaves)]
        + [(chain[0], "1.0.0")]
    )
    for name in leaves:
        packages["node_modules/" + name] = {"version": "1.0.0"}
    return {"lockfileVersion": 3, "packages": packages}


class _CountingList(Sequence):
    """记录索引读取次数的只读序列，用于度量邻接表被扫描的次数。

    find_path 除名称排序外不得反复扫描邻接表：sorted() 经索引协议恰好
    完整读取一次（n 次取元素加 1 次 IndexError），任何额外的线性扫描
    都会让读取计数超出该常数。
    """

    def __init__(self, items):
        self._items = tuple(items)
        self.index_reads = 0

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        self.index_reads += 1
        return self._items[index]


def instrumented(root_deps, packages_map):
    """复制一份图，把根依赖与每个包的依赖列表替换为计数序列。

    计数序列为只读，被测函数若尝试就地修改会立即抛出；此处复制一份图，
    使计数断言与“原输入不被修改”的断言互不干扰。
    """
    root = _CountingList(root_deps)
    packages = {}
    for name, info in packages_map.items():
        packages[name] = {
            "version": info["version"],
            "deps": _CountingList(info["deps"]),
        }
    return root, packages


def reads_of(packages, name):
    return packages[name]["deps"].index_reads


class ScaleRegression(unittest.TestCase):
    """两类固定规模图上的结果正确性、规模上界与输入不变性。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _load(self, data, filename):
        return load_lockfile(write_lockfile(self.tmp, data, filename))

    # ---- 第一类：五千个根直接依赖 -------------------------------------

    def test_wide_graph_direct_dependency_two_entry_path(self):
        root_deps, packages_map = self._load(wide_lock_data(), "wide.json")
        self.assertEqual(len(root_deps), _DIRECT_COUNT)
        target = "d{:08d}".format(_DIRECT_COUNT - 1)
        self.assertEqual(sorted(root_deps)[-1], target)

        self.assertEqual(find_path(root_deps, packages_map, target), [ROOT, target])

    def test_wide_graph_first_sorted_dependency(self):
        # 根依赖按声明逆序提供，排序最前者同样必须给出两项路径，
        # 覆盖“根层排序后入队”而非按声明次序处理。
        root_deps, packages_map = self._load(wide_lock_data(), "wide.json")
        self.assertEqual(
            find_path(root_deps, packages_map, "d00000000"), [ROOT, "d00000000"]
        )

    def test_wide_graph_linear_adjacency_scan_bound(self):
        # 目标是排序最后的根依赖：根层全部入队后，它前面的 4999 个包
        # 都会出队一次。每个可达包的空邻接表恰被 sorted() 扫描一次
        # （0 个元素 + 1 次越界），不可达包一次也不扫描；
        # 根依赖表恰好被完整读取一次（n 次索引 + 1 次越界）。
        root_deps, packages_map = self._load(wide_lock_data(), "wide.json")
        root, packages = instrumented(root_deps, packages_map)
        target = "d{:08d}".format(_DIRECT_COUNT - 1)
        find_path(root, packages, target)

        self.assertEqual(root.index_reads, _DIRECT_COUNT + 1)
        for name in packages:
            # 排序最后的目标出队即返回，其自身邻接表不展开；
            # 不可达包从未入队；其余根直依各展开一次空邻接表。
            expected_reads = 0 if name in (target, "zz-unreachable") else 1
            self.assertEqual(reads_of(packages, name), expected_reads)

        # 另查排序最前的根依赖：目标首次出队即返回，任何包条目的
        # 邻接表都不应被展开。
        first_root, first_packages = instrumented(root_deps, packages_map)
        find_path(first_root, first_packages, "d00000000")
        self.assertEqual(first_root.index_reads, _DIRECT_COUNT + 1)
        self.assertEqual(
            [name for name in first_packages if reads_of(first_packages, name)],
            [],
        )

    def test_wide_graph_unreachable_and_missing(self):
        root_deps, packages_map = self._load(wide_lock_data(), "wide.json")
        self.assertEqual(find_path(root_deps, packages_map, "zz-unreachable"), [])
        with self.assertRaises(NotFoundError):
            find_path(root_deps, packages_map, "not-installed")

    # ---- 第二类：两千链节点 + 链尾两千叶子 -----------------------------

    def test_long_chain_full_path_order_and_contents(self):
        root_deps, packages_map = self._load(chain_lock_data(), "chain.json")
        target = "a-leaf-{:04d}".format(_LEAF_COUNT - 1)
        path = find_path(root_deps, packages_map, target)

        chain = ["c{:04d}".format(i) for i in range(_CHAIN_COUNT)]
        expected = [ROOT] + chain + [target]
        self.assertEqual(path, expected)
        self.assertEqual(len(path), _CHAIN_COUNT + 2)
        # 路径无重复节点：循环回指不得把 chain[0] 再次写入路径。
        self.assertEqual(len(path), len(set(path)))
        # 相邻名称确实对应图中的一条边（含根节点的直接声明）。
        self.assertEqual(path[1], root_deps[0])
        for parent, child in zip(chain[:-1], chain[1:]):
            self.assertIn(child, packages_map[parent]["deps"])
        self.assertIn(target, packages_map[chain[-1]]["deps"])

    def test_long_chain_every_chain_node_uses_shortest_path(self):
        # 每个链节点的最短路径必须是沿链的前缀路径（长度 i+2），
        # 循环边 chain[-1] -> chain[0] 不得产生更短或等长但错误的替代路径。
        root_deps, packages_map = self._load(chain_lock_data(), "chain.json")
        chain = ["c{:04d}".format(i) for i in range(_CHAIN_COUNT)]
        for i, name in enumerate(chain):
            with self.subTest(chain_index=i):
                self.assertEqual(
                    find_path(root_deps, packages_map, name),
                    [ROOT] + chain[: i + 1],
                )

    def test_long_chain_adjacency_scanned_at_most_once(self):
        # 到达目标叶子需展开根与全部链节点；排序最后的叶子排在队尾，
        # 它前面的 1999 个叶子都会先出队一次：
        # - 每个被展开节点的邻接表读取次数恰为“度数 + 1”，即 sorted()
        #   完整扫描一次，不存在第二次扫描（链尾 2001 条边含循环回指）；
        # - 目标叶子出队即返回，未展开；不可达情形不存在于本图。
        root_deps, packages_map = self._load(chain_lock_data(), "chain.json")
        root, packages = instrumented(root_deps, packages_map)
        target = "a-leaf-{:04d}".format(_LEAF_COUNT - 1)
        find_path(root, packages, target)

        self.assertEqual(root.index_reads, 1 + 1)  # 根层仅 1 条边
        chain_names = ["c{:04d}".format(i) for i in range(_CHAIN_COUNT)]
        for name in chain_names:
            self.assertEqual(
                reads_of(packages, name),
                len(packages_map[name]["deps"]) + 1,
            )
        for i in range(_LEAF_COUNT):
            name = "a-leaf-{:04d}".format(i)
            # 目标不展开；其余等长叶子虽无关答案但仍各出队一次，
            # 其空邻接表同样只被扫描一次。
            self.assertEqual(reads_of(packages, name), 0 if name == target else 1)

    def test_long_chain_first_leaf_avoids_other_leaf_scans(self):
        # 查排序最前的叶子时它排在叶子层队首，出队即返回：
        # 其余 1999 个叶子的邻接表一次也不应被扫描。
        root_deps, packages_map = self._load(chain_lock_data(), "chain.json")
        root, packages = instrumented(root_deps, packages_map)
        target = "a-leaf-0000"
        find_path(root, packages, target)

        self.assertEqual(root.index_reads, 1 + 1)
        for name in packages:
            if name.startswith("a-leaf-") and name != target:
                self.assertEqual(reads_of(packages, name), 0)
        self.assertEqual(reads_of(packages, target), 0)

    def test_long_chain_first_leaf_lexicographic(self):
        # 2000 个叶子等长路径：即使按降序声明，也取名称序列字典序最小者。
        root_deps, packages_map = self._load(chain_lock_data(), "chain.json")
        chain = ["c{:04d}".format(i) for i in range(_CHAIN_COUNT)]
        self.assertEqual(
            find_path(root_deps, packages_map, "a-leaf-0000"),
            [ROOT] + chain + ["a-leaf-0000"],
        )

    # ---- 输入不变性与重复查询确定性 -----------------------------------

    def test_inputs_unchanged_before_and_after_queries(self):
        wide_target = "d{:08d}".format(_DIRECT_COUNT - 1)
        cases = (
            (
                wide_lock_data(),
                "wide.json",
                ("d00000000", wide_target, "zz-unreachable"),
            ),
            (
                chain_lock_data(),
                "chain.json",
                ("c0000", "c1999", "a-leaf-1999"),
            ),
        )
        for data, filename, targets in cases:
            with self.subTest(graph=filename):
                root_deps, packages_map = self._load(data, filename)
                root_snapshot = copy.deepcopy(root_deps)
                map_snapshot = copy.deepcopy(packages_map)

                for target in targets:
                    find_path(root_deps, packages_map, target)
                with self.assertRaises(NotFoundError):
                    find_path(root_deps, packages_map, "ghost-package")

                # 逐元素相等已包含声明顺序比较；容器类型也显式固定。
                self.assertEqual(root_deps, root_snapshot)
                self.assertEqual(packages_map, map_snapshot)
                self.assertIsInstance(root_deps, list)
                for info in packages_map.values():
                    self.assertIsInstance(info["deps"], list)
                    self.assertIsInstance(info["version"], str)

    def test_repeated_calls_return_identical_results(self):
        wide_target = "d{:08d}".format(_DIRECT_COUNT - 1)
        cases = (
            (wide_lock_data(), "wide.json", wide_target, "zz-unreachable"),
            (chain_lock_data(), "chain.json", "a-leaf-1999", None),
        )
        for data, filename, target, unreachable in cases:
            with self.subTest(graph=filename):
                root_deps, packages_map = self._load(data, filename)
                first = find_path(root_deps, packages_map, target)
                for _ in range(3):
                    self.assertEqual(find_path(root_deps, packages_map, target), first)
                if unreachable is not None:
                    self.assertEqual(
                        find_path(root_deps, packages_map, unreachable), []
                    )
                    for _ in range(2):
                        self.assertEqual(
                            find_path(root_deps, packages_map, unreachable), []
                        )
                with self.assertRaises(NotFoundError):
                    find_path(root_deps, packages_map, "ghost-package")

    # ---- 结果兼容性：等长字典序、循环、自依赖、不可达、大小写 ----------

    def test_compatibility_tie_cycles_self_dep_unreachable(self):
        # 独立于既有 test_why_paths 的最小构造：
        # 根声明 b、a；a 依赖 z、y、b；b 依赖 z、a。z 有两条等长路径：
        #   [$root,a,z] 与 [$root,b,z]，取字典序 [$root,a,z]。
        # a->b->a 构成可达循环；iso 自依赖且根不可达；ISO 未安装。
        root_deps = ["b", "a"]
        packages_map = {
            "a": {"version": "1.0.0", "deps": ["z", "y", "b"]},
            "b": {"version": "1.0.0", "deps": ["z", "a"]},
            "y": {"version": "1.0.0", "deps": []},
            "z": {"version": "1.0.0", "deps": []},
            "iso": {"version": "1.0.0", "deps": ["iso"]},
        }
        snapshot = copy.deepcopy({"root": root_deps, "map": packages_map})

        self.assertEqual(find_path(root_deps, packages_map, "a"), [ROOT, "a"])
        self.assertEqual(find_path(root_deps, packages_map, "b"), [ROOT, "b"])
        self.assertEqual(
            find_path(root_deps, packages_map, "y"), [ROOT, "a", "y"]
        )
        self.assertEqual(
            find_path(root_deps, packages_map, "z"), [ROOT, "a", "z"]
        )
        self.assertEqual(find_path(root_deps, packages_map, "iso"), [])
        with self.assertRaises(NotFoundError):
            find_path(root_deps, packages_map, "ISO")  # 区分大小写

        self.assertEqual(root_deps, snapshot["root"])
        self.assertEqual(packages_map, snapshot["map"])


if __name__ == "__main__":
    unittest.main()
