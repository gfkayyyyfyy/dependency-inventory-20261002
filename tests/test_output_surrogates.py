"""结果文本含孤立 Unicode 代理码点时输出边界的回归测试（仅标准库、离线）。

JSON 字符串可用 \\uXXXX 转义解码出孤立高代理（U+D800–U+DBFF）或孤立低
代理（U+DC00–U+DFFF）：文件字节仍是合法 UTF-8（转义本身是 ASCII），
json 解析也接受，Python 字符串可以持有该码点，但严格 UTF-8 无法对其
编码。约定：

- 实际输出的任意字符串含孤立代理时，命令退出码 2、stderr 恰为
  "INPUT_ERROR\\n"、stdout 为空、无堆栈或部分清单；
- 只判断实际输出内容：未参与结果的元数据、被筛选排除（如 --reachable、
  --unreachable）的版本字符串不得拖垮本可成功的结果；只输出包名/路径
  的 why / parents / ancestors / descendants 不受版本影响；
- 正常 Unicode（中文）与合法代理对解码出的补充平面字符原样保留，
  成功时输出单个 JSON 文档加末尾一个换行，退出码 0、stderr 为空；
- 结构校验仍覆盖整份输入，查询包不存在仍为退出码 1 / NOT_FOUND；
- load_lockfile 与各分析函数的返回值、异常语义不变（代理码点原样出现在
  返回结构中），sbom --with-purl 对无法编码的标识仍抛 InputError。

仓库夹具 surrogate-lock.json 由 demo-lock.json 复制而来，依赖关系不变，
仅 beta 的 version 写成 JSON 转义字符串 "2.0-\\ud83f"。所有命令前后输入
文件字节保持一致。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import (
    InputError,
    NotFoundError,
    diff_items,
    find_ancestors,
    find_descendants,
    find_path,
    find_parents,
    list_items,
    load_lockfile,
    reachable_items,
    sbom_document,
    unreachable_items,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
SURROGATE_LOCK = os.path.join(REPO_ROOT, "surrogate-lock.json")

HIGH_SURROGATE = "\ud83f"
LOW_SURROGATE = "\udfff"
SMILE = "\U0001f600"  # 合法代理对 😀 解码后的 U+1F600


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
    )


def write_text(directory, text, name="lock.json"):
    # 模板中的 \\udfff 等是写给文件的 ASCII JSON 转义，文件字节合法 UTF-8。
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def assert_input_error(test, result):
    test.assertEqual(result.returncode, 2)
    test.assertEqual(result.stderr, b"INPUT_ERROR\n")
    test.assertEqual(result.stdout, b"")
    test.assertNotIn(b"Traceback", result.stderr)


def assert_success_json(test, result):
    test.assertEqual(result.returncode, 0, result.stderr)
    test.assertEqual(result.stderr, b"")
    test.assertTrue(result.stdout.endswith(b"\n"))
    # 单个 JSON 文档：恰一个末尾换行，无部分输出。
    test.assertEqual(result.stdout.count(b"\n"), 1)
    return json.loads(result.stdout.decode("utf-8"))


class SurrogateFixtureAcceptance(unittest.TestCase):
    """surrogate-lock.json：直接验收数据与两条例行命令。"""

    def test_fixture_is_demo_with_only_beta_version_escaped(self):
        demo = read_bytes(DEMO_LOCK)
        raw = read_bytes(SURROGATE_LOCK)
        # 文件字节合法 UTF-8，且孤立代理以 ASCII 转义形式出现，没有任何
        # 原始代理字节（原始代理码点本就不可能出现在 UTF-8 中）。
        raw.decode("utf-8")
        self.assertIn(b"2.0-\\ud83f", raw)
        # 除 beta 的 version 行外，字节与 demo-lock.json 完全一致。
        self.assertEqual(raw.count(b'"version": "2.0.0"'), 0)
        self.assertEqual(
            raw,
            demo.replace(b'"version": "2.0.0"', b'"version": "2.0-\\ud83f"'),
        )

    def test_fixture_relations_preserved(self):
        root_deps, packages_map = load_lockfile(SURROGATE_LOCK)
        self.assertEqual(root_deps, ["alpha"])
        self.assertEqual(packages_map["alpha"]["version"], "1.0.0")
        self.assertEqual(packages_map["alpha"]["deps"], ["beta"])
        self.assertEqual(packages_map["beta"]["deps"], [])
        self.assertEqual(packages_map["beta"]["version"], "2.0-" + HIGH_SURROGATE)

    def test_list_is_input_error(self):
        assert_input_error(self, run_cli(["list", SURROGATE_LOCK]))

    def test_why_beta_still_succeeds_with_name_only_path(self):
        result = run_cli(["why", SURROGATE_LOCK, "beta"])
        doc = assert_success_json(self, result)
        self.assertEqual(doc, {"name": "beta", "path": ["$root", "alpha", "beta"]})

    def test_version_bearing_and_name_only_commands(self):
        # 输出版本的命令失败。
        assert_input_error(self, run_cli(["sbom", SURROGATE_LOCK]))
        assert_input_error(
            self, run_cli(["diff", DEMO_LOCK, SURROGATE_LOCK])
        )
        # 只输出包名/路径的命令照常成功，版本不进入结果。
        doc = assert_success_json(
            self, run_cli(["parents", SURROGATE_LOCK, "beta"])
        )
        self.assertEqual(
            doc, {"name": "beta", "direct": False, "parents": ["alpha"]}
        )
        doc = assert_success_json(
            self, run_cli(["ancestors", SURROGATE_LOCK, "beta"])
        )
        self.assertEqual(doc, {"name": "beta", "ancestors": ["alpha"]})
        doc = assert_success_json(
            self, run_cli(["descendants", SURROGATE_LOCK, "alpha"])
        )
        self.assertEqual(doc, {"name": "alpha", "descendants": ["beta"]})
        doc = assert_success_json(
            self, run_cli(["why", SURROGATE_LOCK, "beta", "--from", "alpha"])
        )
        self.assertEqual(doc, {"name": "beta", "path": ["alpha", "beta"]})

    def test_missing_query_still_not_found(self):
        result = run_cli(["why", SURROGATE_LOCK, "ghost"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"NOT_FOUND\n")
        self.assertEqual(result.stdout, b"")

    def test_fixture_bytes_unchanged_after_runs(self):
        before = read_bytes(SURROGATE_LOCK)
        run_cli(["list", SURROGATE_LOCK])
        run_cli(["why", SURROGATE_LOCK, "beta"])
        run_cli(["sbom", SURROGATE_LOCK])
        run_cli(["diff", DEMO_LOCK, SURROGATE_LOCK])
        run_cli(["parents", SURROGATE_LOCK, "beta"])
        self.assertEqual(read_bytes(SURROGATE_LOCK), before)
        self.assertEqual(
            hashlib.sha256(before).hexdigest(),
            hashlib.sha256(read_bytes(SURROGATE_LOCK)).hexdigest(),
        )


LOW_SURROGATE_LOCK = (
    '{"lockfileVersion": 3, "packages": {'
    '"": {"dependencies": {"alpha": "1.0.0"}},'
    '"node_modules/alpha": {"version": "1.0.0",'
    ' "dependencies": {"beta": "2.0.0"}},'
    '"node_modules/beta": {"version": "2.0-\\udfff"}'
    "}}"
)


class LoneLowSurrogateOutput(unittest.TestCase):
    """孤立低代理 U+DFFF 与高代理走同一条输出编码边界。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = write_text(self._tmp.name, LOW_SURROGATE_LOCK, "low.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_bytes_valid_utf8_but_version_unencodable(self):
        raw = read_bytes(self.path)
        raw.decode("utf-8")
        self.assertIn(b"\\udfff", raw)
        _root, packages_map = load_lockfile(self.path)
        self.assertEqual(packages_map["beta"]["version"], "2.0-" + LOW_SURROGATE)
        with self.assertRaises(UnicodeEncodeError):
            packages_map["beta"]["version"].encode("utf-8")

    def test_list_input_error_why_succeeds(self):
        assert_input_error(self, run_cli(["list", self.path]))
        doc = assert_success_json(self, run_cli(["why", self.path, "beta"]))
        self.assertEqual(doc, {"name": "beta", "path": ["$root", "alpha", "beta"]})

    def test_input_bytes_unchanged(self):
        before = read_bytes(self.path)
        run_cli(["list", self.path])
        run_cli(["why", self.path, "beta"])
        self.assertEqual(read_bytes(self.path), before)


SUPPLEMENTARY_LOCK = (
    '{"lockfileVersion": 3, "packages": {'
    '"": {"dependencies": {"alpha": "1.0.0"}},'
    '"node_modules/alpha": {"version": "1.0-\\ud83d\\ude00",'
    ' "dependencies": {"beta": "2.0-中文"}},'
    '"node_modules/beta": {"version": "2.0-中文"}'
    "}}"
)


class SupplementaryPlanePreserved(unittest.TestCase):
    """合法代理对解码为补充平面字符，与中文一样原样输出，不报错。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = write_text(self._tmp.name, SUPPLEMENTARY_LOCK, "supp.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_preserves_characters_as_raw_utf8(self):
        _root, packages_map = load_lockfile(self.path)
        self.assertEqual(packages_map["alpha"]["version"], "1.0-" + SMILE)
        self.assertEqual(packages_map["beta"]["version"], "2.0-中文")

        result = run_cli(["list", self.path])
        doc = assert_success_json(self, result)
        self.assertEqual(
            doc,
            [
                {"name": "alpha", "version": "1.0-" + SMILE, "direct": True},
                {"name": "beta", "version": "2.0-中文", "direct": False},
            ],
        )
        # 原样 UTF-8 字节：😀 = F0 9F 98 80；中文 = E4 B8 AD / E6 96 87；
        # 不得回退成 ASCII 的 \\uXXXX 转义。
        self.assertIn("😀".encode("utf-8"), result.stdout)
        self.assertIn("中文".encode("utf-8"), result.stdout)
        self.assertNotIn(b"\\u", result.stdout)

    def test_why_and_purl_succeed(self):
        doc = assert_success_json(self, run_cli(["why", self.path, "beta"]))
        self.assertEqual(doc["path"], ["$root", "alpha", "beta"])
        # 补充平面字符在 purl 中按 UTF-8 字节百分号编码，既有功能正常。
        doc = assert_success_json(
            self, run_cli(["sbom", self.path, "--with-purl"])
        )
        purls = {item["name"]: item["purl"] for item in doc["components"]}
        self.assertEqual(purls["alpha"], "pkg:npm/alpha@1.0-%F0%9F%98%80")
        self.assertTrue(purls["beta"].endswith("@2.0-%E4%B8%AD%E6%96%87"))

    def test_input_bytes_unchanged(self):
        before = read_bytes(self.path)
        run_cli(["list", self.path])
        run_cli(["sbom", self.path, "--with-purl"])
        self.assertEqual(read_bytes(self.path), before)


# 根只依赖 alpha→beta；orphan 根不可达且版本含孤立高代理；alpha 的
# description 元数据也含孤立代理，但该字段从不进入任何结果。
FILTERED_LOCK = (
    '{"lockfileVersion": 3, "packages": {'
    '"": {"dependencies": {"alpha": "1.0.0"}},'
    '"node_modules/alpha": {"version": "1.0.0",'
    ' "description": "note-\\ud83f",'
    ' "dependencies": {"beta": "2.0.0"}},'
    '"node_modules/beta": {"version": "2.0.0"},'
    '"node_modules/orphan": {"version": "9.0-\\ud83f"}'
    "}}"
)


class FilteredResultsExcludeBadText(unittest.TestCase):
    """判断范围是实际输出：筛选后或名字类结果不含异常文本即成功。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = write_text(self._tmp.name, FILTERED_LOCK, "filtered.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_inventory_fails_but_reachable_output_succeeds(self):
        # 完整清单含 orphan 的问题版本：失败。
        assert_input_error(self, run_cli(["list", self.path]))
        assert_input_error(self, run_cli(["list", "--unreachable", self.path]))
        assert_input_error(self, run_cli(["sbom", self.path]))

        # 只留可达条目后，结果文本全部干净：成功（description 元数据
        # 与被排除的 orphan 版本都不参与输出）。
        doc = assert_success_json(
            self, run_cli(["list", "--reachable", self.path])
        )
        self.assertEqual(
            doc,
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )
        doc = assert_success_json(
            self, run_cli(["sbom", "--reachable", self.path])
        )
        self.assertEqual(
            [item["name"] for item in doc["components"]], ["alpha", "beta"]
        )
        # --with-purl 只为筛选后的干净组件构造标识，同样成功。
        doc = assert_success_json(
            self,
            run_cli(
                ["sbom", self.path, "--reachable", "--with-purl", "--with-paths"]
            ),
        )
        by_name = {item["name"]: item for item in doc["components"]}
        self.assertEqual(by_name["alpha"]["purl"], "pkg:npm/alpha@1.0.0")
        self.assertEqual(by_name["beta"]["path"], ["$root", "alpha", "beta"])

    def test_name_only_queries_succeed_even_for_unreachable_package(self):
        doc = assert_success_json(
            self, run_cli(["why", self.path, "orphan"])
        )
        # 已安装但根不可达：路径为空数组，版本不出现。
        self.assertEqual(doc, {"name": "orphan", "path": []})
        doc = assert_success_json(
            self, run_cli(["ancestors", self.path, "orphan"])
        )
        self.assertEqual(doc, {"name": "orphan", "ancestors": []})

    def test_input_bytes_unchanged(self):
        before = read_bytes(self.path)
        for argv in (
            ["list", self.path],
            ["list", "--reachable", self.path],
            ["list", "--unreachable", self.path],
            ["sbom", self.path],
            ["sbom", "--reachable", self.path],
            ["why", self.path, "orphan"],
        ):
            run_cli(argv)
        self.assertEqual(read_bytes(self.path), before)


class AnalysisFunctionsSemanticsUnchanged(unittest.TestCase):
    """加载与分析函数不抛新异常：代理码点原样出现在返回结构中。"""

    def test_functions_pass_surrogate_through(self):
        root_deps, packages_map = load_lockfile(SURROGATE_LOCK)

        items = list_items(root_deps, packages_map)
        self.assertEqual(items[1]["version"], "2.0-" + HIGH_SURROGATE)

        reachable = reachable_items(root_deps, packages_map)
        self.assertEqual(
            {item["name"] for item in reachable}, {"alpha", "beta"}
        )
        self.assertEqual(unreachable_items(root_deps, packages_map), [])

        doc = sbom_document(root_deps, packages_map)
        beta = {c["name"]: c for c in doc["components"]}["beta"]
        self.assertEqual(beta["version"], "2.0-" + HIGH_SURROGATE)
        self.assertEqual(beta["license"], "unknown")
        self.assertEqual(beta["securityStatus"], "unknown")

        self.assertEqual(
            find_path(root_deps, packages_map, "beta"),
            ["$root", "alpha", "beta"],
        )
        self.assertEqual(
            find_parents(root_deps, packages_map, "beta")["parents"],
            ["alpha"],
        )
        self.assertEqual(
            find_ancestors(root_deps, packages_map, "beta"), ["alpha"]
        )
        self.assertEqual(
            find_descendants(root_deps, packages_map, "alpha"), ["beta"]
        )

        # diff 记录同样原样携带无法编码的版本字符串；CLI 失败仅发生在
        # 输出阶段，函数自身语义不变。
        clean_root, clean_map = load_lockfile(DEMO_LOCK)
        records = diff_items(clean_map, packages_map)
        self.assertEqual(
            records,
            [
                {
                    "name": "beta",
                    "change": "changed",
                    "before": "2.0.0",
                    "after": "2.0-" + HIGH_SURROGATE,
                }
            ],
        )

    def test_missing_name_still_raises_not_found(self):
        root_deps, packages_map = load_lockfile(SURROGATE_LOCK)
        with self.assertRaises(NotFoundError):
            find_path(root_deps, packages_map, "ghost")
        with self.assertRaises(NotFoundError):
            find_parents(root_deps, packages_map, "ghost")

    def test_sbom_with_purl_still_raises_input_error(self):
        # beta 自根可达且版本含孤立代理：即使 --reachable 也无法构造其
        # purl，既有 InputError 语义不变；筛掉问题组件（只查只含干净
        # 组件的场景由 FilteredResultsExcludeBadText 覆盖）后才成功。
        root_deps, packages_map = load_lockfile(SURROGATE_LOCK)
        with self.assertRaises(InputError):
            sbom_document(root_deps, packages_map, with_purl=True)
        with self.assertRaises(InputError):
            sbom_document(
                root_deps, packages_map, reachable=True, with_purl=True
            )


if __name__ == "__main__":
    unittest.main()
