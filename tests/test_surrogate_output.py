"""结果文本含孤立 Unicode 代理码点时输出边界的回归测试（仅标准库、离线）。

JSON 转义文本（如 "2.0-\\ud83f"）可被标准 json 解码为含孤立代理码点的
Python 字符串，文件字节本身仍是合法 UTF-8，结构校验也照常通过。边界只
存在于结果输出阶段：

- 实际输出的任意字符串含孤立高代理（U+D800..U+DBFF）或孤立低代理
  （U+DC00..U+DFFF）时，各子命令一律退出码 2、stderr 恰为
  "INPUT_ERROR\\n"、stdout 为空（无堆栈、无部分清单）；
- 判断范围是实际结果文本：未参与结果的元数据、被 --reachable 等筛选
  排除的版本字符串含孤立代理不影响本来可以输出的结果；
- 正常 Unicode（中文、合法代理对解码出的补充平面字符）原样保留，成功
  时 stdout 为带单个末尾换行的单个 JSON 文档、退出码 0、stderr 为空；
- 查询包不存在仍优先返回退出码 1 与 NOT_FOUND（结果里没有异常文本时）。

仓库夹具 surrogate-lock.json 由 demo-lock.json 逐字节派生，仅把 beta
的 version 改为 "2.0-\\ud83f"：list/sbom 的结果含该版本故输入错误，
why beta 的路径只含包名故仍成功。load_lockfile 与各分析函数返回值及
异常语义不变——函数层仍返回含代理码点的字符串，不做替换或拒绝。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import list_items, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SURROGATE_LOCK = os.path.join(REPO_ROOT, "surrogate-lock.json")
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
    )


def write_ascii(path, text):
    # 以 ASCII 字节写入，\\uXXXX 是 JSON 源码转义而非真实 UTF-8 代理
    # 字节（CESU-8/UTF-16 中转均不合法），故文件仍是合法 UTF-8。
    with open(path, "wb") as handle:
        handle.write(text.encode("ascii"))
    return path


def write_text(directory, name, text):
    # 源码中含真实 CJK 字符时按 UTF-8 直接写入；\\uXXXX 转义仍是 ASCII。
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class SurrogateFixtureAcceptance(unittest.TestCase):
    """surrogate-lock.json 的直接验收：版本进入结果则失败，路径则成功。"""

    def assert_input_error(self, argv):
        result = run_cli(argv)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, b"INPUT_ERROR\n")
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"Traceback", result.stderr)

    def assert_success_json(self, argv, expected):
        result = run_cli(argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        # 单个 JSON 文档且恰有一个末尾换行。
        self.assertEqual(result.stdout.count(b"\n"), 1)
        self.assertTrue(result.stdout.endswith(b"\n"))
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), expected)
        return result.stdout

    def test_fixture_bytes_are_valid_utf8_with_escaped_high_surrogate(self):
        raw = read_bytes(SURROGATE_LOCK)
        # 文件字节合法 UTF-8；代理码点只以六个 ASCII 字符的转义存在。
        raw.decode("utf-8")
        self.assertIn(b'"version": "2.0-\\ud83f"', raw)
        data = json.loads(raw.decode("utf-8"))
        version = data["packages"]["node_modules/beta"]["version"]
        self.assertEqual(version, "2.0-\ud83f")
        self.assertEqual(ord(version[-1]), 0xD83F)
        # 依赖关系与 demo-lock.json 保持一致。
        self.assertEqual(data["packages"][""]["dependencies"], {"alpha": "1.0.0"})
        self.assertEqual(
            data["packages"]["node_modules/alpha"]["dependencies"], {"beta": "2.0.0"}
        )

    def test_list_fixture_is_input_error(self):
        self.assert_input_error(["list", SURROGATE_LOCK])

    def test_list_reachable_fixture_is_input_error(self):
        # beta 自根可达，其版本进入结果。
        self.assert_input_error(["list", SURROGATE_LOCK, "--reachable"])

    def test_sbom_fixture_is_input_error(self):
        self.assert_input_error(["sbom", SURROGATE_LOCK])
        self.assert_input_error(["sbom", SURROGATE_LOCK, "--reachable"])

    def test_sbom_with_purl_keeps_existing_input_error(self):
        # 既有约定：无法编码的标识在分析层即抛 InputError，输出约定相同。
        self.assert_input_error(["sbom", SURROGATE_LOCK, "--with-purl"])

    def test_diff_fixture_against_demo_is_input_error_in_both_directions(self):
        # beta 版本差异记录的 before/after 一侧含孤立代理。
        self.assert_input_error(["diff", SURROGATE_LOCK, DEMO_LOCK])
        self.assert_input_error(["diff", DEMO_LOCK, SURROGATE_LOCK])

    def test_why_beta_fixture_succeeds_without_version(self):
        # 版本不进入路径结果：why 仍成功。
        self.assert_success_json(
            ["why", SURROGATE_LOCK, "beta"],
            {"name": "beta", "path": ["$root", "alpha", "beta"]},
        )

    def test_why_beta_from_alpha_fixture_succeeds(self):
        self.assert_success_json(
            ["why", SURROGATE_LOCK, "beta", "--from", "alpha"],
            {"name": "beta", "path": ["alpha", "beta"]},
        )

    def test_parents_ancestors_descendants_fixture_succeed(self):
        # 这些结果只含包名，不含版本字符串。
        self.assert_success_json(
            ["parents", SURROGATE_LOCK, "beta"],
            {"name": "beta", "direct": False, "parents": ["alpha"]},
        )
        self.assert_success_json(
            ["ancestors", SURROGATE_LOCK, "beta"],
            {"name": "beta", "ancestors": ["alpha"]},
        )
        self.assert_success_json(
            ["descendants", SURROGATE_LOCK, "beta"],
            {"name": "beta", "descendants": []},
        )

    def test_list_unreachable_fixture_succeeds_empty(self):
        # alpha、beta 均自根可达：被排除的 beta 版本不进入结果，
        # 输出为 [] 而非输入错误。
        self.assert_success_json(["list", SURROGATE_LOCK, "--unreachable"], [])

    def test_why_missing_package_still_not_found(self):
        # 结果文本不含异常内容时，不存在的包仍按 NOT_FOUND 处理。
        result = run_cli(["why", SURROGATE_LOCK, "ghost"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"NOT_FOUND\n")
        self.assertEqual(result.stdout, b"")

    def test_fixture_bytes_unchanged_after_all_invocations(self):
        before = hashlib.sha256(read_bytes(SURROGATE_LOCK)).digest()
        for argv in (
            ["list", SURROGATE_LOCK],
            ["list", SURROGATE_LOCK, "--reachable"],
            ["list", SURROGATE_LOCK, "--unreachable"],
            ["why", SURROGATE_LOCK, "beta"],
            ["parents", SURROGATE_LOCK, "beta"],
            ["ancestors", SURROGATE_LOCK, "beta"],
            ["descendants", SURROGATE_LOCK, "beta"],
            ["sbom", SURROGATE_LOCK],
            ["diff", SURROGATE_LOCK, DEMO_LOCK],
        ):
            run_cli(argv)
        after = hashlib.sha256(read_bytes(SURROGATE_LOCK)).digest()
        self.assertEqual(before, after)


class LoneSurrogateOutputRejected(unittest.TestCase):
    """孤立低代理与包名中的孤立代理同样在输出阶段整体失败。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_input_error(self, argv):
        result = run_cli(argv)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, b"INPUT_ERROR\n")
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"Traceback", result.stderr)

    def test_lone_low_surrogate_in_version_rejected(self):
        # 孤立低代理 U+DCFF：alpha 根可达，list 结果含其版本。
        path = write_ascii(
            os.path.join(self.tmp, "low.json"),
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1"}},'
            '"node_modules/alpha":{"version":"1.0-\\udcff"}}}',
        )
        raw = read_bytes(path)
        raw.decode("utf-8")  # 文件字节本身合法 UTF-8。
        self.assert_input_error(["list", path])
        self.assert_input_error(["sbom", path])

    def test_lone_surrogate_in_package_name_rejected_for_that_result(self):
        # 孤立代理位于包名（结构校验接受任意平铺名称）：完整清单的 name
        # 字段含异常文本故 list 失败；只查干净包 alpha 的路径不经过异常
        # 名称，why 仍成功；--reachable 将不可达的异常名称包排除后同样
        # 可以输出 alpha。
        path = write_ascii(
            os.path.join(self.tmp, "badname.json"),
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1"}},'
            '"node_modules/alpha":{"version":"1.0.0"},'
            '"node_modules/bad-\\ud83f":{"version":"1.0.0"}}}',
        )
        self.assert_input_error(["list", path])
        result = run_cli(["why", path, "alpha"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            {"name": "alpha", "path": ["$root", "alpha"]},
        )
        result = run_cli(["list", path, "--reachable"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [{"name": "alpha", "version": "1.0.0", "direct": True}],
        )


class SurrogateTextFilteredOrIgnored(unittest.TestCase):
    """不含于实际结果的孤立代理文本不得拖垮本来可输出的结果。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_unreachable_package_surrogate_version_excluded_by_reachable(self):
        # orphan 已安装但根不可达，版本含孤立高代理。
        path = write_ascii(
            os.path.join(self.tmp, "orphan.json"),
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1"}},'
            '"node_modules/alpha":{"version":"1.0.0"},'
            '"node_modules/orphan":{"version":"9.0-\\ud83f"}}}',
        )
        # 完整清单含 orphan -> 失败。
        result = run_cli(["list", path])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        # 只列可达：orphan 被排除 -> 成功且不出现其版本。
        result = run_cli(["list", path, "--reachable"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [{"name": "alpha", "version": "1.0.0", "direct": True}],
        )
        result = run_cli(["sbom", path, "--reachable"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [c["name"] for c in json.loads(result.stdout)["components"]],
            ["alpha"],
        )

    def test_reachable_package_surrogate_version_excluded_by_unreachable(self):
        # 反向场景：根可达包版本含孤立代理，--unreachable 只输出另一个
        # 不可达的干净包，异常版本被筛选排除。
        path = write_ascii(
            os.path.join(self.tmp, "split.json"),
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1"}},'
            '"node_modules/alpha":{"version":"1.0-\\udc00"},'
            '"node_modules/orphan":{"version":"9.0.0"}}}',
        )
        result = run_cli(["list", path, "--unreachable"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [{"name": "orphan", "version": "9.0.0", "direct": False}],
        )

    def test_diff_and_relation_filters_exclude_surrogate_text(self):
        # 两侧的不可达包 orphan 版本分别含孤立高/低代理：
        # 普通 diff 结果含版本 -> 失败；--reachable 先筛掉 orphan ->
        # 两侧无差异，成功输出 []。parents/ancestors --reachable 按成员
        # 筛选后不含该异常名称，查询仍成功。
        before = write_ascii(
            os.path.join(self.tmp, "before.json"),
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1"}},'
            '"node_modules/alpha":{"version":"1.0.0"},'
            '"node_modules/orphan":{"version":"9.0-\\ud83f"}}}',
        )
        after = write_ascii(
            os.path.join(self.tmp, "after.json"),
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1"}},'
            '"node_modules/alpha":{"version":"1.0.0"},'
            '"node_modules/orphan":{"version":"9.1-\\udc00"}}}',
        )
        result = run_cli(["diff", before, after])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"INPUT_ERROR\n")
        self.assertEqual(result.stdout, b"")
        result = run_cli(["diff", before, after, "--reachable"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), [])
        result = run_cli(["parents", before, "orphan", "--reachable"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            {"name": "orphan", "direct": False, "parents": []},
        )
        result = run_cli(["ancestors", before, "orphan", "--reachable"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            {"name": "orphan", "ancestors": []},
        )
        for path in (before, after):
            self.assertIn(b"\\ud", read_bytes(path))

    def test_surrogate_in_unused_metadata_does_not_reject_output(self):        # 顶层 name 与根条目 version 均不参与任何分析结果；其中的孤立
        # 代理不影响 list 输出。
        path = write_ascii(
            os.path.join(self.tmp, "meta.json"),
            '{"lockfileVersion":3,"name":"root-\\ud83f","packages":{'
            '"":{"name":"root-\\udcff","version":"1.0-\\ud83f",'
            '"dependencies":{"alpha":"1"}},'
            '"node_modules/alpha":{"version":"1.0.0"}}}',
        )
        result = run_cli(["list", path])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [{"name": "alpha", "version": "1.0.0", "direct": True}],
        )

    def test_input_file_bytes_unchanged(self):
        path = write_ascii(
            os.path.join(self.tmp, "keep.json"),
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1"}},'
            '"node_modules/alpha":{"version":"1.0-\\ud83f"}}}',
        )
        before = read_bytes(path)
        run_cli(["list", path])  # 失败路径
        run_cli(["why", path, "alpha"])  # 成功路径
        self.assertEqual(read_bytes(path), before)


class ValidUnicodePreserved(unittest.TestCase):
    """中文与合法代理对解码出的补充平面字符继续原样输出。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_supplementary_pair_and_cjk_preserved_as_utf8(self):
        # beta 版本含合法代理对 😀（解码为 U+1F600 😀）；
        # 另有中文包名 中文包。二者均须原样保留。
        path = write_text(
            self.tmp,
            "wide.json",
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"beta":"1","中文包":"1"}},'
            '"node_modules/beta":{"version":"2.0-\\ud83d\\ude00"},'
            '"node_modules/中文包":{"version":"1.0.0"}}}',
        )
        result = run_cli(["list", path])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout.count(b"\n"), 1)
        self.assertTrue(result.stdout.endswith(b"\n"))
        # 直接核对输出字节：补充平面字符编码为 4 字节 F0 9F 98 80，
        # 中文按其 3 字节 UTF-8 编码出现，不做 ASCII 转义。
        self.assertIn("2.0-😀".encode("utf-8"), result.stdout)
        self.assertIn("中文包".encode("utf-8"), result.stdout)
        parsed = json.loads(result.stdout.decode("utf-8"))
        by_name = {item["name"]: item for item in parsed}
        self.assertEqual(by_name["beta"]["version"], "2.0-\U0001F600")
        self.assertEqual(by_name["中文包"]["version"], "1.0.0")
        # why 路径中的中文包名同样原样保留。
        result = run_cli(["why", path, "中文包"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            {"name": "中文包", "path": ["$root", "中文包"]},
        )

    def test_supplementary_character_in_version_not_rejected_by_sbom(self):
        # 与孤立代理唯一差别是代理成对出现：sbom 必须成功。
        path = write_ascii(
            os.path.join(self.tmp, "pair.json"),
            '{"lockfileVersion":3,"packages":{'
            '"":{"dependencies":{"alpha":"1"}},'
            '"node_modules/alpha":{"version":"\\ud83e\\udd80"}}}',  # 🦀 U+1F980
        )
        result = run_cli(["sbom", path])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8"))["components"][0]["version"],
            "\U0001F980",
        )


class LibrarySemanticsUnchanged(unittest.TestCase):
    """修复只在输出边界：加载与分析函数继续返回含代理码点的原文本。"""

    def test_load_lockfile_returns_surrogate_text(self):
        root_deps, packages_map = load_lockfile(SURROGATE_LOCK)
        self.assertEqual(root_deps, ["alpha"])
        self.assertEqual(packages_map["alpha"]["deps"], ["beta"])
        version = packages_map["beta"]["version"]
        self.assertEqual(version, "2.0-\ud83f")
        self.assertEqual(ord(version[-1]), 0xD83F)

    def test_list_items_returns_surrogate_text_unchanged(self):
        root_deps, packages_map = load_lockfile(SURROGATE_LOCK)
        items = list_items(root_deps, packages_map)
        self.assertEqual(items[1], {"name": "beta", "version": "2.0-\ud83f",
                                   "direct": False})
        # 函数层不替换、不拒绝；调用方仍拿到原字符串。
        self.assertEqual(items[1]["version"], packages_map["beta"]["version"])


if __name__ == "__main__":
    unittest.main()
