"""why --from 指定起点最短路径语义的组合回归测试。

组合部分：在 alpha、beta、gamma、leaf 四个已安装包之间枚举全部不含自环的
有向边组合（12 条候选边，共 2**12 = 4096 张图），对每张图核对全部
起点 × 目标组合（4 × 4 = 16 组）。期望结果由本模块内独立的参考实现
oracle_shortest_path 逐层 BFS 得出，不调用 find_path，也不复用
lockfile 模块的任何内部辅助函数。每张图分别采用空根依赖与根直接声明
alpha 两种根节点，指定起点时两者答案必须相同；同时以条目倒序、依赖列表
倒序的等价映射重查，结果仍相同；查询前后版本、根依赖及包依赖列表内容
保持不变。所有包版本统一为 1.0.0，映射结构遵循 load_lockfile 的返回
数据约定（root_deps 为包名列表，packages_map 为
{name: {"version": str, "deps": [str]}}）。

固定用例部分：补足自环、区分大小写的完整包名、@scope/pkg 作用域包与
名为 $root 的普通安装包；指定起点查询不额外插入虚拟根标记；起点或目标
不存在时统一抛 NotFoundError。固定用例经由临时目录中的合法
package-lock.json v3 文件由 load_lockfile 加载。

命令行部分：核对 sample-lock.json 上两个 --from 查询的退出码、标准错误
与标准输出字节。

全程离线、仅用标准库，可由 python -m unittest discover -s tests 发现。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import NotFoundError, find_path, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_LOCK = os.path.join(REPO_ROOT, "sample-lock.json")

VERSION = "1.0.0"
NAMES = ("alpha", "beta", "gamma", "leaf")
# 全部不含自环的有向边，共 4 * 3 = 12 条。
DIRECTED_EDGES = tuple(
    (source, target) for source in NAMES for target in NAMES if source != target
)


def oracle_shortest_path(adjacency, source, target):
    """独立参考实现：返回 source 到 target 边数最少的完整包名路径。

    adjacency 为 {包名: [依赖包名]}。同长度路径按包名序列的 Unicode 码点
    字典序取第一条（Python 字符串比较即码点比较，等长元组比较即序列比较）；
    起点等于目标时返回只含该名称的数组；不可达返回 []。

    逐层 BFS：第 L 层记录每个节点长度为 L 的字典序最小路径；下一层节点的
    候选路径为当前层各前驱最优路径追加该节点，取最小者。已到达过的节点
    不再扩展，自环与循环正常结束。
    """
    if source == target:
        return [source]
    best = {source: (source,)}
    frontier = (source,)
    while frontier:
        candidates = {}
        for node in frontier:
            for dep in adjacency[node]:
                if dep in best:
                    continue
                candidate = best[node] + (dep,)
                if dep not in candidates or candidate < candidates[dep]:
                    candidates[dep] = candidate
        if target in candidates:
            return list(candidates[target])
        best.update(candidates)
        frontier = tuple(candidates)
    return []


def build_adjacency(mask):
    """按位掩码从 DIRECTED_EDGES 挑选边，返回 {包名: [依赖包名]}。"""
    adjacency = {name: [] for name in NAMES}
    for bit, (source, target) in enumerate(DIRECTED_EDGES):
        if mask & (1 << bit):
            adjacency[source].append(target)
    return adjacency


def make_packages_map(adjacency, reversed_order=False):
    """按 load_lockfile 返回约定构造 packages_map，版本统一 1.0.0。

    reversed_order 为 True 时条目顺序与每条依赖列表均倒序，图关系不变，
    用于核对结果不受条目与依赖声明顺序影响。
    """
    names = list(adjacency)
    if reversed_order:
        names.reverse()
    packages_map = {}
    for name in names:
        deps = list(adjacency[name])
        if reversed_order:
            deps.reverse()
        packages_map[name] = {"version": VERSION, "deps": deps}
    return packages_map


def snapshot(packages_map):
    """packages_map 的独立深拷贝，用于查询前后的内容比对。"""
    return {
        name: {"version": info["version"], "deps": list(info["deps"])}
        for name, info in packages_map.items()
    }


def describe_edges(adjacency):
    return ",".join(
        "%s->%s" % (source, target)
        for source in NAMES
        for target in adjacency[source]
    ) or "(no edges)"


class WhyFromAllGraphs(unittest.TestCase):
    """全部无自环有向图 × 全部起点目标组合 × 两种根依赖 × 两种书写顺序。"""

    def test_every_graph_and_query_pair(self):
        for mask in range(1 << len(DIRECTED_EDGES)):
            adjacency = build_adjacency(mask)
            edges = describe_edges(adjacency)
            # 期望表只依赖图本身：两种根依赖、两种书写顺序的答案必须相同。
            expected = {
                (source, target): oracle_shortest_path(adjacency, source, target)
                for source in NAMES
                for target in NAMES
            }
            with self.subTest(mask=mask, edges=edges):
                for root_deps in ([], ["alpha"]):
                    for reversed_order in (False, True):
                        packages_map = make_packages_map(adjacency, reversed_order)
                        root_before = list(root_deps)
                        map_before = snapshot(packages_map)
                        for source in NAMES:
                            for target in NAMES:
                                actual = find_path(
                                    root_deps, packages_map, target, source
                                )
                                self.assertEqual(
                                    actual,
                                    expected[(source, target)],
                                    msg=(
                                        "edges=%s root_deps=%r reversed=%r "
                                        "source=%r target=%r"
                                        % (
                                            edges,
                                            root_deps,
                                            reversed_order,
                                            source,
                                            target,
                                        )
                                    ),
                                )
                        # 查询不得改动根依赖列表与 packages_map 内容。
                        self.assertEqual(root_deps, root_before)
                        self.assertEqual(packages_map, map_before)


class WhyFromLockfileConvention(unittest.TestCase):
    """组合用映射与 load_lockfile 真实返回逐字段一致（抽样核对）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_constructed_maps_match_load_lockfile(self):
        samples = (0, 0b101010101010, 0b010101010101, (1 << len(DIRECTED_EDGES)) - 1, 7)
        for mask in samples:
            with self.subTest(mask=mask):
                adjacency = build_adjacency(mask)
                packages = {
                    "": {"dependencies": {"alpha": VERSION}},
                }
                for name in NAMES:
                    entry = {"version": VERSION}
                    if adjacency[name]:
                        entry["dependencies"] = {
                            dep: VERSION for dep in adjacency[name]
                        }
                    packages["node_modules/" + name] = entry
                path = os.path.join(self._tmp.name, "lock-%d.json" % mask)
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(
                        {"lockfileVersion": 3, "packages": packages}, handle
                    )
                root_deps, packages_map = load_lockfile(path)
                self.assertEqual(root_deps, ["alpha"])
                self.assertEqual(packages_map, make_packages_map(adjacency))
                for info in packages_map.values():
                    self.assertEqual(info["version"], VERSION)


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def special_lock_data():
    """自环 + 大小写敏感名 + @scope/pkg + 字面 $root 普通包。

    依赖声明顺序刻意与字典序相反。所有包版本均为 1.0.0。
    """
    return {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"alpha": "1.0.0"}},
            "node_modules/alpha": {
                "version": "1.0.0",
                "dependencies": {"@scope/pkg": "1.0.0", "leaf": "1.0.0"},
            },
            "node_modules/leaf": {"version": "1.0.0"},
            "node_modules/loopy": {
                "version": "1.0.0",
                "dependencies": {"loopy": "1.0.0", "leaf": "1.0.0"},
            },
            "node_modules/Alpha": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/@scope/pkg": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
            "node_modules/$root": {
                "version": "1.0.0",
                "dependencies": {"leaf": "1.0.0"},
            },
        },
    }


class WhyFromSpecialNames(unittest.TestCase):
    """固定用例：特殊包名、自环与 NotFoundError 语义。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.lock_path = write_lockfile(cls._tmp.name, special_lock_data())
        cls.root_deps, cls.packages_map = load_lockfile(cls.lock_path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_self_loop_source_and_target(self):
        # 起点等于目标：只含该名称的数组，不展开自环。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "loopy", "loopy"),
            ["loopy"],
        )
        # 自环不妨碍沿其他依赖边到达目标，查询正常结束。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "loopy"),
            ["loopy", "leaf"],
        )

    def test_case_sensitive_full_names(self):
        # "Alpha" 与 "alpha" 是两个不同的已安装包，各自按完整名查询。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "Alpha"),
            ["Alpha", "leaf"],
        )
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "alpha"),
            ["alpha", "leaf"],
        )
        # 大小写不同的名字不作匹配。
        with self.assertRaises(NotFoundError):
            find_path(self.root_deps, self.packages_map, "LEAF", "alpha")
        with self.assertRaises(NotFoundError):
            find_path(self.root_deps, self.packages_map, "leaf", "ALPHA")
        with self.assertRaises(NotFoundError):
            find_path(self.root_deps, self.packages_map, "@Scope/pkg", "alpha")

    def test_scoped_package_as_source_and_target(self):
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "@scope/pkg"),
            ["@scope/pkg", "leaf"],
        )
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "@scope/pkg", "alpha"),
            ["alpha", "@scope/pkg"],
        )
        # 作用域包已安装但自指定起点不可达时返回空数组。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "@scope/pkg", "leaf"),
            [],
        )

    def test_literal_root_package_is_not_virtual_root(self):
        # 名为 $root 的普通安装包可作起点，路径即其自身依赖边。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "leaf", "$root"),
            ["$root", "leaf"],
        )
        # 指定起点查询不额外插入虚拟根标记：普通起点的路径首元素即起点本身。
        path = find_path(self.root_deps, self.packages_map, "leaf", "alpha")
        self.assertEqual(path, ["alpha", "leaf"])
        self.assertNotEqual(path[0], "$root")
        # 目标为 $root 时按普通包名匹配已安装包。
        self.assertEqual(
            find_path(self.root_deps, self.packages_map, "$root", "$root"),
            ["$root"],
        )

    def test_missing_source_or_target_raises_not_found(self):
        for target, source in (
            ("ghost", "alpha"),
            ("leaf", "ghost"),
            ("ghost", "phantom"),
        ):
            with self.subTest(target=target, source=source):
                with self.assertRaises(NotFoundError):
                    find_path(self.root_deps, self.packages_map, target, source)


class WhyFromSampleLockCli(unittest.TestCase):
    """sample-lock.json 上两个 --from 查询的命令行核对。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assert_why_output(self, result, name, path):
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        payload = json.loads(result.stdout)
        # 标准输出为仅含 name 和 path 的 JSON 对象及末尾换行。
        self.assertEqual(set(payload), {"name", "path"})
        self.assertEqual(payload, {"name": name, "path": path})
        self.assertEqual(
            result.stdout,
            json.dumps({"name": name, "path": path}, ensure_ascii=False) + "\n",
        )

    def test_leaf_from_orphan(self):
        result = self.run_cli(
            ["why", SAMPLE_LOCK, "leaf", "--from", "orphan"]
        )
        self.assert_why_output(result, "leaf", ["orphan", "leaf"])

    def test_orphan_from_leaf(self):
        result = self.run_cli(
            ["why", SAMPLE_LOCK, "orphan", "--from", "leaf"]
        )
        self.assert_why_output(result, "orphan", [])


if __name__ == "__main__":
    unittest.main()
