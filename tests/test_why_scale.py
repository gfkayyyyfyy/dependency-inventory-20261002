"""find_path 重构后的规模回归：宽依赖图、长依赖链与输入不变性。

两类固定规模的合法 package-lock.json v3 平铺图（均经 load_lockfile
加载，全程离线、仅用标准库，不安装或执行锁文件中的依赖）：

1. 宽图：根节点直接声明 5000 个互不相连的叶子包（V=5000，根连边
   E=5000），查询按名称 Unicode 码点排序最后的包，结果必须是仅含
   根节点与该包的两项路径。
2. 长链图：根节点连接由 2000 个包组成的单链（链内 1999 条边），
   链尾再连接 2000 个不同叶子包（V=4000，E=4000），查询排序最后
   的叶子，结果必须完整包含根节点、全部 2000 个链节点与该叶子，
   共 2002 项且顺序无误。

复杂度说明（与具体机器耗时无关，故不设任何计时阈值，只验证结果
结构与规模）：单次查询除根直依赖排序与最终路径重建外，BFS 借助
collections.deque 出队 O(1)，parent 指针表保证每个节点至多处理
一次，时间上界 O(V+E)；辅助存储为 parent 表与队列，各至多 O(V)
项；命中路径只在目标确定后回溯重建一次，O(V)。重构前的队列为每个
在途节点保存一条逐节点复制的路径，最坏 O(V^2) 存储，且 list.pop(0)
单次 O(V)；本文件以固定规模大图锁定结果兼容性，等长路径字典序、
循环依赖、不可达与未安装目标的行为另由 test_why_paths.py 保证。

本文件另验证：查询不修改根依赖列表、包版本与各依赖列表（连同列表
对象身份），同一份数据重复查询结果一致；并在多张种子固定的随机小
图上把新实现与重构前的逐路径 BFS 逐包对照。
"""

import copy
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

from depinventory import ROOT, NotFoundError, find_path, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WIDE_DIRECT_COUNT = 5000
CHAIN_LEN = 2000
LEAF_COUNT = 2000


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def wide_lock_data():
    """根节点直接声明 5000 个叶子包；声明顺序刻意与名称字典序相反。"""
    names = [f"wide-{i:05d}" for i in range(WIDE_DIRECT_COUNT)]
    packages = {
        "": {"dependencies": {name: "1.0.0" for name in reversed(names)}}
    }
    for name in names:
        packages[f"node_modules/{name}"] = {"version": "1.0.0"}
    return {"lockfileVersion": 3, "packages": packages}, names


def chain_lock_data():
    """根→2000 节点单链，链尾→2000 个叶子；另含不可达的自依赖包。

    链尾的叶子依赖同样按名称逆序声明，以证明结果与 JSON 键顺序无关。
    """
    chain = [f"chain-{i:05d}" for i in range(CHAIN_LEN)]
    leaves = [f"leaf-{i:05d}" for i in range(LEAF_COUNT)]
    packages = {"": {"dependencies": {chain[0]: "1.0.0"}}}
    for index, name in enumerate(chain):
        if index < CHAIN_LEN - 1:
            packages[f"node_modules/{name}"] = {
                "version": "1.0.0",
                "dependencies": {chain[index + 1]: "1.0.0"},
            }
        else:
            packages[f"node_modules/{name}"] = {
                "version": "1.0.0",
                "dependencies": {leaf: "1.0.0" for leaf in reversed(leaves)},
            }
    for leaf in leaves:
        packages[f"node_modules/{leaf}"] = {"version": "1.0.0"}
    # 已安装但根节点不可达，且依赖自身：BFS 必须正常结束并返回空路径。
    packages["node_modules/stranded"] = {
        "version": "1.0.0",
        "dependencies": {"stranded": "1.0.0"},
    }
    return {"lockfileVersion": 3, "packages": packages}, chain, leaves


class WideGraphRegression(unittest.TestCase):
    """根节点直连 5000 个包：排序最后的包仍走两项直连路径。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_last_sorted_direct_package_has_two_node_path_api(self):
        data, names = wide_lock_data()
        root_deps, packages_map = load_lockfile(write_lockfile(self.tmp, data))

        self.assertEqual(len(packages_map), WIDE_DIRECT_COUNT)
        # 加载器保留 JSON 声明顺序：这里刻意是名称降序。
        self.assertEqual(root_deps, list(reversed(names)))

        last = sorted(names)[-1]
        self.assertEqual(last, "wide-04999")
        result = find_path(root_deps, packages_map, last)
        self.assertEqual(result, [ROOT, last])
        self.assertEqual(len(result), 2)

        # 排序最前的包同样是两项路径，结论与声明先后无关。
        first = sorted(names)[0]
        self.assertEqual(find_path(root_deps, packages_map, first), [ROOT, first])

    def test_last_sorted_direct_package_has_two_node_path_cli(self):
        data, _ = wide_lock_data()
        path = write_lockfile(self.tmp, data, "wide.json")
        result = self.run_cli(["why", path, "wide-04999"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        parsed = json.loads(result.stdout)
        self.assertEqual(set(parsed), {"name", "path"})
        self.assertEqual(
            parsed,
            {"name": "wide-04999", "path": [ROOT, "wide-04999"]},
        )


class LongChainRegression(unittest.TestCase):
    """根→2000 链节点→2000 叶子：最后一个叶子的完整路径正确无缺。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        data, self.chain, self.leaves = chain_lock_data()
        self.lock_path = write_lockfile(self.tmp, data, "chain.json")
        self.root_deps, self.packages_map = load_lockfile(self.lock_path)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_full_path_to_last_leaf_api(self):
        self.assertEqual(len(self.packages_map), CHAIN_LEN + LEAF_COUNT + 1)
        last_leaf = sorted(self.leaves)[-1]
        self.assertEqual(last_leaf, "leaf-01999")

        expected = [ROOT] + self.chain + [last_leaf]
        self.assertEqual(len(expected), 1 + CHAIN_LEN + 1)
        result = find_path(self.root_deps, self.packages_map, last_leaf)
        self.assertEqual(result, expected)
        # 显式核对规模与逐段顺序，避免只比较长度。
        self.assertEqual(len(result), 2002)
        self.assertEqual(result[0], ROOT)
        self.assertEqual(result[1:-1], self.chain)
        self.assertEqual(result[-1], last_leaf)

        # 同深度的另一个叶子（名称最前者）路径仅末项不同，
        # 锁定长链末端等长路径仍按名称区分。
        first_leaf = sorted(self.leaves)[0]
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, first_leaf),
            [ROOT] + self.chain + [first_leaf],
        )

    def test_full_path_to_last_leaf_cli(self):
        result = self.run_cli(["why", self.lock_path, "leaf-01999"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        parsed = json.loads(result.stdout)
        self.assertEqual(set(parsed), {"name", "path"})
        self.assertEqual(parsed["name"], "leaf-01999")
        self.assertEqual(
            parsed["path"], [ROOT] + self.chain + ["leaf-01999"]
        )
        self.assertEqual(len(parsed["path"]), 2002)

    def test_unreachable_self_dependency_and_missing_target_on_scale_graph(self):
        # 已安装但根不可达且自依赖：返回空路径且正常结束。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "stranded"), []
        )
        # 未安装目标在函数入口抛 NotFoundError。
        with self.assertRaises(NotFoundError):
            find_path(self.root_deps, self.packages_map, "wide-00000")

    def test_repeated_queries_are_identical(self):
        targets = [
            sorted(self.leaves)[-1],
            sorted(self.leaves)[0],
            self.chain[0],
            self.chain[-1],
            "stranded",
        ]
        first_round = [
            find_path(self.root_deps, self.packages_map, target)
            for target in targets
        ]
        for _ in range(3):
            self.assertEqual(
                [
                    find_path(self.root_deps, self.packages_map, target)
                    for target in targets
                ],
                first_round,
            )


class InputNotMutated(unittest.TestCase):
    """查询前后根依赖列表、版本、依赖列表内容与对象身份均不变。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        data, self.chain, self.leaves = chain_lock_data()
        self.root_deps, self.packages_map = load_lockfile(
            write_lockfile(self.tmp, data)
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_find_path_does_not_mutate_loaded_data(self):
        root_before = copy.deepcopy(self.root_deps)
        map_before = copy.deepcopy(self.packages_map)
        root_list_id = id(self.root_deps)
        deps_list_ids = {
            name: id(info["deps"]) for name, info in self.packages_map.items()
        }

        # 含命中、长链、不可达与不存在目标的混合查询。
        find_path(self.root_deps, self.packages_map, sorted(self.leaves)[-1])
        find_path(self.root_deps, self.packages_map, self.chain[len(self.chain) // 2])
        find_path(self.root_deps, self.packages_map, "stranded")
        with self.assertRaises(NotFoundError):
            find_path(self.root_deps, self.packages_map, "ghost")

        self.assertEqual(id(self.root_deps), root_list_id)
        self.assertEqual(self.root_deps, root_before)
        self.assertEqual(self.packages_map, map_before)
        self.assertEqual(
            {name: id(info["deps"]) for name, info in self.packages_map.items()},
            deps_list_ids,
        )
        # 排序辅助不得原地作用于加载器产生的声明顺序列表。
        self.assertEqual(self.root_deps, [self.chain[0]])
        self.assertEqual(
            self.packages_map[self.chain[0]]["deps"], [self.chain[1]]
        )


def reference_find_path(root_deps, packages_map, target):
    """重构前的实现：队列元素携带完整路径副本、list.pop(0)。

    仅作结果对照基准；其 O(V^2) 存储正是本次重构要消除的开销。
    """
    if target not in packages_map:
        raise NotFoundError(target)
    visited = set()
    queue = []
    for name in sorted(root_deps):
        if name not in visited:
            visited.add(name)
            queue.append((name, [ROOT, name]))
    while queue:
        node, path = queue.pop(0)
        if node == target:
            return path
        for dep in sorted(packages_map[node]["deps"]):
            if dep in visited:
                continue
            visited.add(dep)
            queue.append((dep, path + [dep]))
    return []


class ResultParityWithReference(unittest.TestCase):
    """种子固定的随机小图：新实现与重构前 BFS 对每个包结果完全一致。

    图含自环、可达循环、不可达分量，且根依赖与各依赖列表均按随机
    顺序声明（由加载器保留为乱序列表），覆盖等长路径字典序裁决与
    JSON 键顺序无关两条性质。
    """

    NODE_COUNT = 24
    SEEDS = range(40)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _build_graph(self, seed):
        rng = random.Random(seed)
        names = [f"n{index:02d}" for index in range(self.NODE_COUNT)]
        packages = {}
        root_choice = rng.sample(names, rng.randint(3, self.NODE_COUNT - 2))
        rng.shuffle(root_choice)
        packages[""] = {"dependencies": {name: "1.0.0" for name in root_choice}}
        for name in names:
            deps = [dep for dep in names if rng.random() < 0.18]
            if rng.random() < 0.3:
                deps.append(name)  # 自依赖
            rng.shuffle(deps)
            # 经 JSON 对象去重后仍是合法声明；保留首次出现的乱序。
            node = {"version": "1.0.0"}
            if deps:
                node["dependencies"] = {dep: "1.0.0" for dep in deps}
            packages[f"node_modules/{name}"] = node
        return {"lockfileVersion": 3, "packages": packages}, names

    def test_every_target_matches_reference_on_all_graphs(self):
        for seed in self.SEEDS:
            data, names = self._build_graph(seed)
            root_deps, packages_map = load_lockfile(
                write_lockfile(self.tmp, data, f"g{seed}.json")
            )
            with self.subTest(seed=seed):
                for target in names:
                    self.assertEqual(
                        find_path(root_deps, packages_map, target),
                        reference_find_path(root_deps, packages_map, target),
                    )
                # 未安装目标两边都在入口抛 NotFoundError。
                with self.assertRaises(NotFoundError):
                    find_path(root_deps, packages_map, "absent")
                with self.assertRaises(NotFoundError):
                    reference_find_path(root_deps, packages_map, "absent")


if __name__ == "__main__":
    unittest.main()
