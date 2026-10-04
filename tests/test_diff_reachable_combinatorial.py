"""diff --reachable 根可达差异比较的组合回归测试（独立核对，仅标准库、全程离线）。

在已安装的 alpha、beta 两个包之间枚举包含自环的全部 4 条候选有向边
（2×2：alpha→alpha、alpha→beta、beta→alpha、beta→beta）。根条目与
两个包条目各自可声明这两个包的任意子集，即 3 个声明者 × 2 个声明目标
= 6 个声明比特，每侧共 2^6 = 64 种依赖结构（根依赖为空写为空对象，
覆盖自环、互相依赖与根为空）。枚举所有旧、新依赖结构配对
（64×64 = 4096 对），并对每对枚举新版本组合（alpha、beta 各自独立取
1.0.0 或 2.0.0，共 4 种；旧侧两包版本均为 1.0.0），合计
64×64×4 = 16384 个核对情形。所有输入均为临时目录内独立创建的
lockfileVersion 3 平铺 package-lock.json，dependencies 的声明值统一为
字符串 "*"。

核对公开的 reachable_names 与 diff_items 组合的结果（先按各自根可达集合
过滤两侧映射再比较，与命令行 diff --reachable 同一流程），期望完全由
本模块独立依据输入确定：

- 可达集合用独立的邻接集不动点计算（根直接声明先播种，随后反复并入
  邻接直到不再增长），差异记录按规则从两侧可达集合与版本表直接推导，
  不调用 reachable_names/diff_items 或其他产品查询来生成答案；
- 仅旧侧可达为 removed（after 为 null），仅新侧可达为 added（before 为
  null），两侧都可达且版本字符串不同才为 changed，版本相同不输出，
  两侧都不可达也不输出；缺失侧版本一律为 null；
- 记录只含 name、change、before、after 四个现有字段，版本原样保留，
  按包名 Unicode 码点排序（"alpha" < "beta"），每包最多一项，
  根项目不进入结果；
- 每次调用前后 root_deps 与 packages_map 的内容保持不变。

命令行只验证指定场景：旧侧根声明 alpha、alpha 声明 beta（两包均
1.0.0），新侧根只声明 beta、两包不声明依赖，alpha 改为 2.0.0、beta
不变——带 --reachable 仅输出 alpha 的 removed（before 1.0.0、after
null），省略选项仅输出 alpha 的 changed（before 1.0.0、after 2.0.0）。
再把新侧不可达 alpha 的版本置为空字符串：load_lockfile 抛 InputError，
命令退出码 2、stdout 为空、stderr 恰为 "INPUT_ERROR\\n"；成功命令
退出码 0、stderr 为空、stdout 为完整 JSON 数组加恰好一个末尾换行。
全程不联网、不安装或执行依赖，也不修改输入文件。
"""

import copy
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, diff_items, load_lockfile, reachable_names

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NAMES = ("alpha", "beta")
# 含自环的全部候选有向边：2 个包两两组合（含 source == target）共 4 条。
CANDIDATE_EDGES = [(source, target) for source in NAMES for target in NAMES]
# 三个声明者：空串根条目与两个包条目；各自独立声明 NAMES 的任意子集。
DECLARERS = ("",) + NAMES
OLD_VERSIONS = {"alpha": "1.0.0", "beta": "1.0.0"}
# 新侧每包独立取 1.0.0 或 2.0.0 的四种组合，按 NAMES 顺序给出。
NEW_VERSION_COMBOS = [
    {"alpha": alpha_version, "beta": beta_version}
    for alpha_version, beta_version in itertools.product(("1.0.0", "2.0.0"), repeat=2)
]


def all_structures():
    """枚举一侧的全部 2^6 种依赖结构。

    结构为 (root_subset, edges)：root_subset 是根条目 dependencies 声明
    的包名元组；edges 是两个包条目声明的有向边列表（含自环）。6 个
    比特中前 2 位对应根声明的 alpha、beta，后 4 位对应按
    CANDIDATE_EDGES 顺序的包条目声明（声明者与目标一一对应）；空子集
    写为空对象，故根为空、无任何包依赖的结构也被覆盖。
    """
    structures = []
    for mask in range(1 << (len(NAMES) + len(CANDIDATE_EDGES))):
        root_subset = tuple(
            NAMES[i] for i in range(len(NAMES)) if mask & (1 << i)
        )
        edges = [
            CANDIDATE_EDGES[i]
            for i in range(len(CANDIDATE_EDGES))
            if mask & (1 << (len(NAMES) + i))
        ]
        structures.append((root_subset, tuple(edges)))
    return structures


def reference_reachable(root_subset, edges):
    """独立期望：邻接集上自根依赖出发的可达集不动点。

    根直接声明的包先整体并入（即使没有任何边也保留），随后反复把前沿
    节点的邻接并入直到没有新增。集合天然去重：多路径汇合、自环与循环
    不会重复；根未声明且无路径到达的包（含与根断开的自环/互相依赖）
    保持不可达。与产品的 BFS 实现互不相关。
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


def reference_reachable_diff(old_reach, new_reach, new_versions):
    """独立期望：两侧可达集合与新侧版本表直接推导出的差异记录。

    仅旧侧可达 -> removed（after 为 null）；仅新侧可达 -> added
    （before 为 null，版本取自新侧）；两侧都可达且版本字符串不同 ->
    changed（旧侧版本恒为 1.0.0），相同不输出；两侧都不可达不输出。
    按包名 Unicode 码点排序，每包最多一项。不调用任何产品函数。
    """
    records = []
    for name in sorted(NAMES):
        in_old = name in old_reach
        in_new = name in new_reach
        if in_old and not in_new:
            records.append(
                {"name": name, "change": "removed",
                 "before": OLD_VERSIONS[name], "after": None}
            )
        elif in_new and not in_old:
            records.append(
                {"name": name, "change": "added",
                 "before": None, "after": new_versions[name]}
            )
        elif in_old and in_new and OLD_VERSIONS[name] != new_versions[name]:
            records.append(
                {"name": name, "change": "changed",
                 "before": OLD_VERSIONS[name], "after": new_versions[name]}
            )
    return records


def build_packages_map(root_subset, edges, versions):
    """按 load_lockfile 返回约定构造 (root_deps, packages_map)。

    版本按 versions 表原样保留；包的 deps 只取以该包为声明者的边，
    声明顺序与 CANDIDATE_EDGES 一致（alpha→alpha、alpha→beta、
    beta→alpha、beta→beta）。
    """
    packages_map = {}
    for name in NAMES:
        packages_map[name] = {
            "version": versions[name],
            "deps": [target for source, target in edges if source == name],
        }
    return list(root_subset), packages_map


def lockfile_data(root_subset, edges, versions):
    """构造 npm v3 平铺锁文件数据：空串根条目与两个包条目均可声明依赖。

    dependencies 的声明值统一为 "*"；空子集写为空对象（根为空时根条目
    仍显式带 {"dependencies": {}}，包无依赖时同样写空对象）。
    """
    root_deps = {name: "*" for name in root_subset}
    packages = {"": {"dependencies": root_deps}}
    for name in NAMES:
        packages["node_modules/" + name] = {
            "version": versions[name],
            "dependencies": {
                target: "*" for source, target in edges if source == name
            },
        }
    return {"lockfileVersion": 3, "packages": packages}


def structure_label(tag, root_subset, edges):
    edge_text = ",".join("%s->%s" % edge for edge in edges) or "(no edges)"
    return "%s root=%r edges={%s}" % (tag, list(root_subset), edge_text)


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def write_lockfile(directory, data, name):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class ReachableDiffCombinatorial(unittest.TestCase):
    """所有旧、新依赖结构配对 × 四种新版本组合的全量核对。"""

    def test_all_structure_pairs_and_version_combos(self):
        structures = all_structures()
        self.assertEqual(len(structures), 1 << (len(NAMES) + len(CANDIDATE_EDGES)))

        for old_root, old_edges in structures:
            old_reach = reference_reachable(old_root, old_edges)
            old_root_deps, old_map = build_packages_map(
                old_root, old_edges, OLD_VERSIONS
            )
            for new_root, new_edges in structures:
                new_reach = reference_reachable(new_root, new_edges)
                context_base = "%s ; %s" % (
                    structure_label("old", old_root, old_edges),
                    structure_label("new", new_root, new_edges),
                )
                for new_versions in NEW_VERSION_COMBOS:
                    new_root_deps, new_map = build_packages_map(
                        new_root, new_edges, new_versions
                    )
                    context = "%s ; versions=%r" % (
                        context_base,
                        {name: new_versions[name] for name in NAMES},
                    )

                    # 公开接口组合：各自用 reachable_names 求可达集合后
                    # 过滤映射，再交 diff_items 比较，与 CLI 同流程。
                    old_keep = reachable_names(old_root_deps, old_map)
                    new_keep = reachable_names(new_root_deps, new_map)
                    self.assertEqual(
                        old_keep,
                        old_reach,
                        "%s: old reachable_names mismatch" % context,
                    )
                    self.assertEqual(
                        new_keep,
                        new_reach,
                        "%s: new reachable_names mismatch" % context,
                    )

                    old_snapshot = copy.deepcopy(old_map)
                    new_snapshot = copy.deepcopy(new_map)
                    old_root_snapshot = list(old_root_deps)
                    new_root_snapshot = list(new_root_deps)

                    filtered_old = {
                        name: info for name, info in old_map.items()
                        if name in old_keep
                    }
                    filtered_new = {
                        name: info for name, info in new_map.items()
                        if name in new_keep
                    }
                    records = diff_items(filtered_old, filtered_new)
                    expected = reference_reachable_diff(
                        old_reach, new_reach, new_versions
                    )
                    self.assertEqual(records, expected, context)

                    # 记录只含四字段、按 Unicode 码点排序、每包一项、
                    # 版本原样保留、根项目不出现。
                    self.assertEqual(
                        [record["name"] for record in records],
                        sorted(record["name"] for record in records),
                        "%s: records not sorted" % context,
                    )
                    names_seen = [record["name"] for record in records]
                    self.assertEqual(
                        len(names_seen), len(set(names_seen)),
                        "%s: duplicate package records" % context,
                    )
                    self.assertNotIn("", names_seen, "%s: root in records" % context)
                    for record in records:
                        self.assertEqual(
                            set(record.keys()),
                            {"name", "change", "before", "after"},
                            "%s: unexpected record keys" % context,
                        )
                        name = record["name"]
                        if record["change"] == "removed":
                            self.assertIn(name, old_reach, context)
                            self.assertNotIn(name, new_reach, context)
                            self.assertEqual(
                                record["before"], OLD_VERSIONS[name], context
                            )
                            self.assertIsNone(record["after"], context)
                        elif record["change"] == "added":
                            self.assertNotIn(name, old_reach, context)
                            self.assertIn(name, new_reach, context)
                            self.assertIsNone(record["before"], context)
                            self.assertEqual(
                                record["after"], new_versions[name], context
                            )
                        else:
                            self.assertEqual(record["change"], "changed", context)
                            self.assertIn(name, old_reach, context)
                            self.assertIn(name, new_reach, context)
                            self.assertEqual(
                                record["before"], OLD_VERSIONS[name], context
                            )
                            self.assertEqual(
                                record["after"], new_versions[name], context
                            )
                            self.assertNotEqual(
                                record["before"], record["after"], context
                            )

                    # 调用不得修改输入映射与根依赖列表。
                    self.assertEqual(old_map, old_snapshot, context)
                    self.assertEqual(new_map, new_snapshot, context)
                    self.assertEqual(old_root_deps, old_root_snapshot, context)
                    self.assertEqual(new_root_deps, new_root_snapshot, context)


class ReachableDiffCliScenarios(unittest.TestCase):
    """命令行仅验证代表场景：旧 root->alpha->beta 对新 root->beta。"""

    EXPECTED_REACHABLE = [
        {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
    ]
    EXPECTED_FULL = [
        {"name": "alpha", "change": "changed", "before": "1.0.0", "after": "2.0.0"},
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 旧侧：根声明 alpha，alpha 声明 beta，两包版本均为 1.0.0。
        old_data = lockfile_data(
            ("alpha",), (("alpha", "beta"),), OLD_VERSIONS
        )
        # 新侧：根只声明 beta，两包不声明依赖，alpha 2.0.0、beta 不变。
        new_versions = {"alpha": "2.0.0", "beta": "1.0.0"}
        new_data = lockfile_data(("beta",), (), new_versions)
        self.old_path = write_lockfile(self.tmp, old_data, "old.json")
        self.new_path = write_lockfile(self.tmp, new_data, "new.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_reachable_outputs_only_removed_alpha(self):
        before_old = read_bytes(self.old_path)
        before_new = read_bytes(self.new_path)

        result = run_cli(["diff", self.old_path, self.new_path, "--reachable"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            json.dumps(self.EXPECTED_REACHABLE, ensure_ascii=False) + "\n",
        )
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))
        self.assertEqual(json.loads(result.stdout), self.EXPECTED_REACHABLE)
        # 读操作不得改写输入文件。
        self.assertEqual(read_bytes(self.old_path), before_old)
        self.assertEqual(read_bytes(self.new_path), before_new)

    def test_without_flag_outputs_only_changed_alpha(self):
        result = run_cli(["diff", self.old_path, self.new_path])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            json.dumps(self.EXPECTED_FULL, ensure_ascii=False) + "\n",
        )
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))
        self.assertEqual(json.loads(result.stdout), self.EXPECTED_FULL)

    def test_unreachable_empty_version_rejected(self):
        # 新侧不可达的 alpha（根只声明 beta 且 beta 无出边）版本置为空串：
        # 整份校验先于可达筛选，加载接口与命令行都必须失败。
        bad_versions = {"alpha": "", "beta": "1.0.0"}
        bad_data = lockfile_data(("beta",), (), bad_versions)
        bad_path = write_lockfile(self.tmp, bad_data, "new-empty-version.json")
        before_bad = read_bytes(bad_path)

        with self.assertRaises(InputError):
            load_lockfile(bad_path)

        result = run_cli(["diff", self.old_path, bad_path, "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")

        # 新侧作为第一参数时同样失败，且失败调用不改写输入。
        reversed_result = run_cli(["diff", bad_path, self.old_path, "--reachable"])
        self.assertEqual(reversed_result.returncode, 2)
        self.assertEqual(reversed_result.stdout, "")
        self.assertEqual(reversed_result.stderr, "INPUT_ERROR\n")
        self.assertEqual(read_bytes(bad_path), before_bad)


if __name__ == "__main__":
    unittest.main()
