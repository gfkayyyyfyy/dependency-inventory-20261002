"""diff --reachable 根可达差异比较在两包空间上的组合回归测试。

仅标准库、全程离线：样例均为临时目录内独立生成的合法
package-lock.json v3 平铺锁文件，不修改仓库自带样例，不联网、不安装
或执行锁文件中的依赖，测试本身也不改写任何已写入的输入文件。

输入空间（旧、新两侧同构）：
- 只安装 alpha、beta 两个包，平铺键 node_modules/alpha、node_modules/beta；
- 根条目 "" 与两个包条目都各自声明 dependencies，声明值统一为 "*"；
  每个声明者可取这两个包的任意子集（2^2 = 4 种，空子集写为空对象
  {}），故自环（alpha->alpha、beta->beta）、互相依赖
  （alpha<->beta）与根依赖为空都在枚举内；
- 一个"依赖结构"= 根子集（4 种）× 四条候选有向边子集（2^4 = 16 种），
  共 4 × 16 = 64 种取值空间；本测试用一个 6 位掩码编码，低两位为根
  子集、高四位为边子集，恰好枚举 64 种；
- 旧侧两包版本恒为 1.0.0；新侧每包独立取 1.0.0 或 2.0.0，共四种版本
  组合。

覆盖：所有旧、新依赖结构配对（64 × 64）× 四种新版本组合，主体核对公开
接口 reachable_names 与 diff_items 的组合（先各自按根可达集合过滤，再
比较），并钉住记录语义：

- 仅旧侧可达为 removed（after 为 null），仅新侧可达为 added（before 为
  null），即使两侧都安装且版本相同也按可达性输出；两侧都可达仅在版本
  字符串不同时输出 changed，版本相同不输出；两侧都不可达也不输出；
- 记录只含 name、change、before、after 四个现有字段，版本原样保留，
  按包名 Unicode 码点排序（alpha < beta），每包最多一项，根项目不进入
  结果；
- 期望由本模块独立的可达集不动点与逐条分类规则确定，不调用
  reachable_names/diff_items 或其他产品查询生成答案。

命令行只钉死指定代表场景：旧侧根声明 alpha、alpha 声明 beta（两包
1.0.0）；新侧根只声明 beta、两包不声明依赖，alpha 2.0.0、beta 1.0.0。
带 --reachable 仅输出 alpha 的 removed（before 1.0.0、after null）；
省略选项仅输出 alpha 的 changed（1.0.0 -> 2.0.0）。再把新侧不可达
alpha 的版本置为空字符串：加载接口抛 InputError，命令退出码 2、
stdout 为空、stderr 恰为 "INPUT_ERROR\\n"（筛选不放宽整份校验）。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, diff_items, load_lockfile, reachable_names

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NAMES = ("alpha", "beta")
OLD_VERSIONS = {"alpha": "1.0.0", "beta": "1.0.0"}
# 新侧四种版本组合：每包独立取 1.0.0 或 2.0.0。
NEW_VERSION_COMBOS = (
    {"alpha": "1.0.0", "beta": "1.0.0"},
    {"alpha": "1.0.0", "beta": "2.0.0"},
    {"alpha": "2.0.0", "beta": "1.0.0"},
    {"alpha": "2.0.0", "beta": "2.0.0"},
)
# 根 dependencies 的四个子集，按掩码低两位枚举（含空集）。
ROOT_SUBSETS = (
    (),
    ("alpha",),
    ("beta",),
    ("alpha", "beta"),
)
# 四条候选有向边（含自环），按掩码第 2..5 位枚举。
CANDIDATE_EDGES = (
    ("alpha", "alpha"),
    ("alpha", "beta"),
    ("beta", "alpha"),
    ("beta", "beta"),
)

STRUCTURE_COUNT = len(ROOT_SUBSETS) * (1 << len(CANDIDATE_EDGES))  # 64


def decode_structure(code):
    """把 6 位结构掩码解码为 (根直接依赖元组, 有向边元组)。"""
    root_deps = tuple(
        NAMES[i] for i in range(len(NAMES)) if code & (1 << i)
    )
    edges = tuple(
        CANDIDATE_EDGES[i]
        for i in range(len(CANDIDATE_EDGES))
        if code & (1 << (i + len(NAMES)))
    )
    return root_deps, edges


def structure_label(code):
    """供失败信息定位：旧/新结构的根子集与边子集一目了然。"""
    root_deps, edges = decode_structure(code)
    edge_text = ",".join("%s->%s" % edge for edge in edges) if edges else "(no edges)"
    root_text = ",".join(root_deps) if root_deps else "(empty)"
    return "struct#%d{root=%s edges=%s}" % (code, root_text, edge_text)


def versions_label(versions):
    return "alpha=%s,beta=%s" % (versions["alpha"], versions["beta"])


def lockfile_data(code, versions, reversed_order=False):
    """按结构掩码与版本表构造平铺 v3 锁文件数据。

    根条目与两个包条目始终显式写 dependencies（空子集写为 {}），声明值
    统一为 "*"。reversed_order 为 True 时整体反转包条目、根声明与各包
    依赖目标的书写顺序，图与版本不变，用于证明结果与声明书写顺序无关。
    """
    root_deps, edges = decode_structure(code)
    names = list(reversed(NAMES) if reversed_order else NAMES)
    root_order = list(reversed(root_deps) if reversed_order else root_deps)

    packages = {"": {"dependencies": {name: "*" for name in root_order}}}
    for name in names:
        targets = [target for source, target in edges if source == name]
        if reversed_order:
            targets.reverse()
        packages["node_modules/" + name] = {
            "version": versions[name],
            "dependencies": {target: "*" for target in targets},
        }
    return {"lockfileVersion": 3, "packages": packages}


def write_lockfile(directory, data, name):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def reference_reachable(code):
    """独立期望：边集上自根直接依赖出发的可达集不动点。

    根声明的包先整体并入（即使没有任何边也保留），随后反复并入前沿节点
    的邻接直到不再增长；与产品的 BFS 实现互不相关，也不调用任何产品
    函数。自环、互相依赖天然收敛，根未声明且无路可达的包保持不可达。
    """
    root_deps, edges = decode_structure(code)
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


def reference_reachable_diff(old_code, new_code, new_versions):
    """独立期望：两侧各自取根可达集合后逐条分类得到的差异记录。

    仅旧侧可达 removed、仅新侧可达 added（缺失侧版本为 null）；两侧都
    可达且版本字符串不同才 changed，相同不输出；两侧都不可达不输出。
    按包名 Unicode 码点排序（sorted 即码点序），每包至多一项。旧侧版本
    恒为 1.0.0，新侧版本取自版本组合表，原样写入。
    """
    old_reachable = reference_reachable(old_code)
    new_reachable = reference_reachable(new_code)
    records = []
    for name in sorted(old_reachable | new_reachable):
        in_old = name in old_reachable
        in_new = name in new_reachable
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


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def reachable_diff_via_api(before_path, after_path):
    """用公开接口复刻 diff --reachable 的主体：各自根可达过滤后比较。

    返回 (记录, 两侧加载结果)，调用方据此核对输入未被修改。
    """
    before_root, before_map = load_lockfile(before_path)
    after_root, after_map = load_lockfile(after_path)
    before_keep = reachable_names(before_root, before_map)
    after_keep = reachable_names(after_root, after_map)
    before_filtered = {
        name: info for name, info in before_map.items() if name in before_keep
    }
    after_filtered = {
        name: info for name, info in after_map.items() if name in after_keep
    }
    records = diff_items(before_filtered, after_filtered)
    return records, (before_root, before_map, after_root, after_map,
                     before_keep, after_keep)


class ReachableDiffCombinatorial(unittest.TestCase):
    """64 × 64 个结构配对 × 4 种新版本组合的全量核对。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 旧侧 64 个结构（版本恒为 1.0.0）；新侧 64 结构 × 4 版本组合，
        # 每种输入只写一次、用唯一文件名，测试期间不改写。
        self.before_paths = {}
        for code in range(STRUCTURE_COUNT):
            self.before_paths[code] = write_lockfile(
                self.tmp,
                lockfile_data(code, OLD_VERSIONS),
                "before-c%02d.json" % code,
            )
        self.after_paths = {}
        self.after_reversed_paths = {}
        for version_index, versions in enumerate(NEW_VERSION_COMBOS):
            for code in range(STRUCTURE_COUNT):
                self.after_paths[(version_index, code)] = write_lockfile(
                    self.tmp,
                    lockfile_data(code, versions),
                    "after-c%02d-v%d.json" % (code, version_index),
                )
                self.after_reversed_paths[(version_index, code)] = write_lockfile(
                    self.tmp,
                    lockfile_data(code, versions, reversed_order=True),
                    "after-c%02d-v%d-rev.json" % (code, version_index),
                )

    def tearDown(self):
        self._tmp.cleanup()

    def assertRecordShape(self, records, old_code, new_code, version_index):
        context = "old=%s new=%s new_versions={%s}" % (
            structure_label(old_code),
            structure_label(new_code),
            versions_label(NEW_VERSION_COMBOS[version_index]),
        )
        names = [record["name"] for record in records]
        # 按包名 Unicode 码点排序、每包最多一项、根项目不进入结果。
        self.assertEqual(names, sorted(names), "%s: records not sorted" % context)
        self.assertEqual(
            len(names), len(set(names)), "%s: duplicate records" % context
        )
        self.assertTrue(
            set(names) <= set(NAMES), "%s: non-package name in records" % context
        )
        valid_changes = {"added", "removed", "changed"}
        for record in records:
            # 记录只含现有四字段，版本字符串原样保留（不解析、不改写）。
            self.assertEqual(
                set(record.keys()),
                {"name", "change", "before", "after"},
                "%s: unexpected record keys %r" % (context, record),
            )
            self.assertIn(
                record["change"], valid_changes,
                "%s: unexpected change label" % context,
            )
            self.assertIn(
                record["before"], (None, "1.0.0"),
                "%s: before version not preserved verbatim" % context,
            )
            self.assertIn(
                record["after"], (None, "1.0.0", "2.0.0"),
                "%s: after version not preserved verbatim" % context,
            )

    def test_all_structure_pairs_and_version_combos(self):
        # 快照全部输入字节：整轮核对不得改写任何清单。
        snapshots = {
            path: read_bytes(path)
            for path in list(self.before_paths.values())
            + list(self.after_paths.values())
        }

        for old_code in range(STRUCTURE_COUNT):
            for new_code in range(STRUCTURE_COUNT):
                for version_index, versions in enumerate(NEW_VERSION_COMBOS):
                    context = "old=%s new=%s new_versions={%s}" % (
                        structure_label(old_code),
                        structure_label(new_code),
                        versions_label(versions),
                    )
                    before_path = self.before_paths[old_code]
                    after_path = self.after_paths[(version_index, new_code)]

                    # 先加载并快照，再走 reachable_names + diff_items 组合，
                    # 核对查询不修改加载得到的入参数据。
                    before_root, before_map = load_lockfile(before_path)
                    after_root, after_map = load_lockfile(after_path)
                    loaded_snapshot = (
                        copy.deepcopy(before_root),
                        copy.deepcopy(before_map),
                        copy.deepcopy(after_root),
                        copy.deepcopy(after_map),
                    )
                    before_keep = reachable_names(before_root, before_map)
                    after_keep = reachable_names(after_root, after_map)
                    before_filtered = {
                        name: info for name, info in before_map.items()
                        if name in before_keep
                    }
                    after_filtered = {
                        name: info for name, info in after_map.items()
                        if name in after_keep
                    }
                    records = diff_items(before_filtered, after_filtered)

                    # reachable_names 本身与独立不动点一致（两侧都核对）。
                    self.assertEqual(
                        before_keep, reference_reachable(old_code),
                        "%s: old reachable_names mismatch" % context,
                    )
                    self.assertEqual(
                        after_keep, reference_reachable(new_code),
                        "%s: new reachable_names mismatch" % context,
                    )

                    # 主体：组合结果与独立分类期望逐条一致。
                    expected = reference_reachable_diff(old_code, new_code, versions)
                    self.assertEqual(records, expected, context)
                    self.assertRecordShape(records, old_code, new_code, version_index)

                    self.assertEqual(
                        (before_root, before_map, after_root, after_map),
                        loaded_snapshot,
                        "%s: inputs mutated" % context,
                    )

        for path, snapshot in snapshots.items():
            self.assertEqual(read_bytes(path), snapshot, "input file rewritten: %s" % path)

    def test_declaration_order_does_not_change_results(self):
        # 新侧整体反转条目与依赖声明顺序：可达集合与差异记录必须完全一致。
        for old_code in range(STRUCTURE_COUNT):
            for new_code in range(STRUCTURE_COUNT):
                for version_index, versions in enumerate(NEW_VERSION_COMBOS):
                    context = "old=%s reversed-new=%s new_versions={%s}" % (
                        structure_label(old_code),
                        structure_label(new_code),
                        versions_label(versions),
                    )
                    canonical = reachable_diff_via_api(
                        self.before_paths[old_code],
                        self.after_paths[(version_index, new_code)],
                    )[0]
                    reversed_records, reversed_loaded = reachable_diff_via_api(
                        self.before_paths[old_code],
                        self.after_reversed_paths[(version_index, new_code)],
                    )
                    self.assertEqual(
                        reversed_loaded[2],  # after_root
                        list(reversed(decode_structure(new_code)[0])),
                        "%s: root deps altered" % context,
                    )
                    self.assertEqual(
                        reversed_records, canonical,
                        "%s: reversed declaration order changed records" % context,
                    )
                    self.assertEqual(
                        reversed_records,
                        reference_reachable_diff(old_code, new_code, versions),
                        context,
                    )

    def test_both_roots_empty_outputs_nothing(self):
        # 显式钉住边界：根为空对象时两侧可达集合均为空，任何边结构
        # （含两个自环与互相依赖）与新版本组合都输出 []。
        empty_root_codes = [
            code for code in range(STRUCTURE_COUNT)
            if decode_structure(code)[0] == ()
        ]
        for old_code in empty_root_codes:
            for new_code in empty_root_codes:
                for version_index, versions in enumerate(NEW_VERSION_COMBOS):
                    records, _ = reachable_diff_via_api(
                        self.before_paths[old_code],
                        self.after_paths[(version_index, new_code)],
                    )
                    self.assertEqual(
                        records, [],
                        "old=%s new=%s {%s}" % (
                            structure_label(old_code),
                            structure_label(new_code),
                            versions_label(versions),
                        ),
                    )

    def test_classification_examples(self):
        # 四个最小固定场景，把 removed/added/changed/静默 四类结果钉死，
        # 失败时可直接读出结构与版本组合。
        cases = [
            # 旧：根声明 alpha（alpha 可达）；新：根声明 beta（beta 可达），
            # 两包版本相同 -> 互相 removed/added。
            (
                _code(root=("alpha",), edges=()),
                _code(root=("beta",), edges=()),
                NEW_VERSION_COMBOS[0],
                [
                    {"name": "alpha", "change": "removed",
                     "before": "1.0.0", "after": None},
                    {"name": "beta", "change": "added",
                     "before": None, "after": "1.0.0"},
                ],
            ),
            # 两侧都只经自环保留各自可达：旧根 alpha + alpha 自环，
            # 新根 beta + beta 自环，beta 新版本 2.0.0。
            (
                _code(root=("alpha",), edges=(("alpha", "alpha"),)),
                _code(root=("beta",), edges=(("beta", "beta"),)),
                NEW_VERSION_COMBOS[1],
                [
                    {"name": "alpha", "change": "removed",
                     "before": "1.0.0", "after": None},
                    {"name": "beta", "change": "added",
                     "before": None, "after": "2.0.0"},
                ],
            ),
            # 两侧均经"根 alpha -> beta"使两包可达；仅 alpha 变 2.0.0。
            (
                _code(root=("alpha",), edges=(("alpha", "beta"),)),
                _code(root=("alpha", "beta"), edges=()),
                NEW_VERSION_COMBOS[2],
                [
                    {"name": "alpha", "change": "changed",
                     "before": "1.0.0", "after": "2.0.0"},
                ],
            ),
            # 两侧均全包可达且版本全部相同：无记录。
            (
                _code(root=("alpha", "beta"), edges=(("alpha", "beta"),
                                                     ("beta", "alpha"))),
                _code(root=("alpha",), edges=(("alpha", "alpha"),
                                              ("alpha", "beta"),
                                              ("beta", "beta"))),
                NEW_VERSION_COMBOS[0],
                [],
            ),
        ]
        for old_code, new_code, versions, expected in cases:
            # 用临时清单直接落这四个场景，避免依赖 setUp 的枚举文件命名。
            with tempfile.TemporaryDirectory() as tmp:
                before_path = write_lockfile(
                    tmp, lockfile_data(old_code, OLD_VERSIONS), "before.json"
                )
                after_path = write_lockfile(
                    tmp, lockfile_data(new_code, versions), "after.json"
                )
                records, _ = reachable_diff_via_api(before_path, after_path)
            context = "old=%s new=%s {%s}" % (
                structure_label(old_code), structure_label(new_code),
                versions_label(versions),
            )
            self.assertEqual(records, expected, context)


def _code(root=(), edges=()):
    """把具名根子集与边集合翻译成结构掩码，仅供固定场景用例查表。"""
    code = 0
    for i, name in enumerate(NAMES):
        if name in root:
            code |= 1 << i
    for i, edge in enumerate(CANDIDATE_EDGES):
        if edge in edges:
            code |= 1 << (i + len(NAMES))
    return code


class RepresentativeScenario(unittest.TestCase):
    """命令行代表场景与公开接口组合的端到端核对。"""

    EXPECTED_REMOVED = [
        {"name": "alpha", "change": "removed",
         "before": "1.0.0", "after": None},
    ]
    EXPECTED_CHANGED = [
        {"name": "alpha", "change": "changed",
         "before": "1.0.0", "after": "2.0.0"},
    ]
    NEW_VERSIONS = NEW_VERSION_COMBOS[2]  # alpha 2.0.0，beta 1.0.0

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 旧侧：根声明 alpha，alpha 声明 beta，两包 1.0.0。
        old_data = lockfile_data(
            _code(root=("alpha",), edges=(("alpha", "beta"),)), OLD_VERSIONS
        )
        # 新侧：根只声明 beta，两包不声明依赖（空对象），alpha 2.0.0。
        new_data = lockfile_data(
            _code(root=("beta",), edges=()), self.NEW_VERSIONS
        )
        self.before_path = write_lockfile(self.tmp, old_data, "before.json")
        self.after_path = write_lockfile(self.tmp, new_data, "after.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_api_combination(self):
        # 公开接口组合：旧侧 {alpha, beta} 可达，新侧仅 {beta} 可达。
        before_root, before_map = load_lockfile(self.before_path)
        after_root, after_map = load_lockfile(self.after_path)
        self.assertEqual(reachable_names(before_root, before_map), {"alpha", "beta"})
        self.assertEqual(reachable_names(after_root, after_map), {"beta"})

        before_keep = reachable_names(before_root, before_map)
        after_keep = reachable_names(after_root, after_map)
        records = diff_items(
            {name: info for name, info in before_map.items() if name in before_keep},
            {name: info for name, info in after_map.items() if name in after_keep},
        )
        self.assertEqual(records, self.EXPECTED_REMOVED)

        # 省略可达过滤（对应不带 --reachable）：两包两侧都安装，
        # 仅 alpha 版本不同。
        self.assertEqual(diff_items(before_map, after_map), self.EXPECTED_CHANGED)

    def test_cli_reachable_outputs_removed_only(self):
        before_bytes = read_bytes(self.before_path)
        after_bytes = read_bytes(self.after_path)

        result = run_cli(["diff", self.before_path, self.after_path, "--reachable"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 完整 JSON 数组加恰好一个末尾换行，无多余输出。
        self.assertEqual(
            result.stdout,
            json.dumps(self.EXPECTED_REMOVED, ensure_ascii=False) + "\n",
        )
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))
        self.assertEqual(json.loads(result.stdout), self.EXPECTED_REMOVED)
        # 读操作不得改写两侧输入。
        self.assertEqual(read_bytes(self.before_path), before_bytes)
        self.assertEqual(read_bytes(self.after_path), after_bytes)

    def test_cli_without_flag_outputs_changed_only(self):
        result = run_cli(["diff", self.before_path, self.after_path])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            json.dumps(self.EXPECTED_CHANGED, ensure_ascii=False) + "\n",
        )
        self.assertEqual(json.loads(result.stdout), self.EXPECTED_CHANGED)


class UnreachableInvalidVersionRejected(unittest.TestCase):
    """新侧不可达 alpha 版本为空字符串：整份输入在筛选前失败。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        old_data = lockfile_data(
            _code(root=("alpha",), edges=(("alpha", "beta"),)), OLD_VERSIONS
        )
        # 与代表场景相同的新侧，但不可达 alpha 的 version 置为 ""。
        new_data = lockfile_data(
            _code(root=("beta",), edges=()), NEW_VERSION_COMBOS[2]
        )
        new_data["packages"]["node_modules/alpha"]["version"] = ""
        self.before_path = write_lockfile(self.tmp, old_data, "before.json")
        self.after_path = write_lockfile(self.tmp, new_data, "after-invalid.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_lockfile_raises_input_error(self):
        before_snapshot = read_bytes(self.before_path)
        after_snapshot = read_bytes(self.after_path)

        with self.assertRaises(InputError):
            load_lockfile(self.after_path)
        # 旧侧本身仍可正常加载。
        root_deps, packages_map = load_lockfile(self.before_path)
        self.assertEqual(reachable_names(root_deps, packages_map), {"alpha", "beta"})

        self.assertEqual(read_bytes(self.before_path), before_snapshot)
        self.assertEqual(read_bytes(self.after_path), after_snapshot)

    def test_cli_exit_2_with_input_error_only(self):
        before_snapshot = read_bytes(self.before_path)
        after_snapshot = read_bytes(self.after_path)

        result = run_cli(
            ["diff", self.before_path, self.after_path, "--reachable"]
        )
        # 不可达包的校验错误不被 --reachable 筛选绕过。
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")

        self.assertEqual(read_bytes(self.before_path), before_snapshot)
        self.assertEqual(read_bytes(self.after_path), after_snapshot)


if __name__ == "__main__":
    unittest.main()
