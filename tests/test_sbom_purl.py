"""sbom --with-purl 组件 Package URL 标识的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例（loader-example.json 除外，只读核对），不安装或
执行锁文件中的依赖；调用真实的 depinventory.load_lockfile 与
depinventory.sbom_document，不以替身代替。覆盖：

- 基本验收：loader-example.json --with-purl 下普通包 alpha 的 purl 为
  pkg:npm/alpha@1.0.0、作用域包 @scope/leaf 为
  pkg:npm/%40scope/leaf@2.0.0，根不可达的 orphan 同样带 purl；
- 百分号编码按 UTF-8 字节进行：仅保留 ASCII 字母、数字与 -._~，空格、
  +、% 分别为 %20、%2B、%25，十六进制字母大写；已有百分号文本不先解码；
  非 ASCII 字符按 UTF-8 字节逐字节转义；结构分隔的 / 与版本前 @ 保留；
- name 与 version 原样不变，不解析版本范围、不改大小写；
- 省略选项时组件字段集合仍是既有字段；两参数调用、reachable 的位置参数
  与关键字调用、with_purl 默认 False 全部保持兼容；
- 与 --reachable、--with-paths、--with-dependencies 任意组合：筛选、
  排序与既有附加字段语义不变，只有根节点时 components 仍为 []；
- 成功时退出码 0、stderr 为空、stdout 为带末尾换行的单个 JSON 文档；
- 不放宽校验：文件不可读、非法 UTF-8、损坏 JSON、结构违规统一退出码 2、
  stdout 为空、stderr 仅 "INPUT_ERROR\\n"；标识文本无法按 UTF-8 编码
  （JSON 转义出的孤对代理）时接口抛 InputError、CLI 同样退出 2；
- 导出前后输入文件字节不变，函数不修改入参。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, load_lockfile, sbom_document

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOADER_EXAMPLE = os.path.join(REPO_ROOT, "loader-example.json")
BAD_UTF8 = os.path.join(REPO_ROOT, "bad-utf8.json")

BASE_FIELDS = {"name", "version", "ecosystem", "direct", "license", "securityStatus"}


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def lock_data(root_deps, packages):
    """由根依赖声明与 {名称: 版本或 (版本, 依赖)} 构造平铺 v3 锁文件数据。"""
    entries = {"": {"dependencies": dict(root_deps)}}
    for name, spec in packages.items():
        if isinstance(spec, tuple):
            version, deps = spec
        else:
            version, deps = spec, None
        node = {"version": version}
        if deps is not None:
            node["dependencies"] = dict(deps)
        entries["node_modules/" + name] = node
    return {"lockfileVersion": 3, "packages": entries}


def base_component(name, version, direct):
    return {
        "name": name,
        "version": version,
        "ecosystem": "npm",
        "direct": direct,
        "license": "unknown",
        "securityStatus": "unknown",
    }


class SbomPurlLoaderExampleAcceptance(unittest.TestCase):
    """基本验收：loader-example.json 的普通包与作用域包 purl。"""

    def setUp(self):
        self.root_deps, self.packages_map = load_lockfile(LOADER_EXAMPLE)

    def test_plain_and_scoped_purl_api(self):
        doc = sbom_document(self.root_deps, self.packages_map, with_purl=True)
        by_name = {item["name"]: item for item in doc["components"]}
        self.assertEqual(by_name["alpha"]["purl"], "pkg:npm/alpha@1.0.0")
        self.assertEqual(
            by_name["@scope/leaf"]["purl"], "pkg:npm/%40scope/leaf@2.0.0"
        )
        # 未筛选时根不可达的 orphan 同样获得标识。
        self.assertEqual(by_name["orphan"]["purl"], "pkg:npm/orphan@3.0.0")

    def test_cli_single_json_document_matches_function(self):
        doc = sbom_document(self.root_deps, self.packages_map, with_purl=True)
        result = run_cli(["sbom", LOADER_EXAMPLE, "--with-purl"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout), doc)

    def test_name_and_version_fields_unchanged(self):
        doc = sbom_document(self.root_deps, self.packages_map, with_purl=True)
        for item in doc["components"]:
            self.assertEqual(
                item["version"], self.packages_map[item["name"]]["version"]
            )
            self.assertEqual(set(item), BASE_FIELDS | {"purl"})


class SbomPurlEncoding(unittest.TestCase):
    """百分号编码：UTF-8 字节、unreserved 集合、大写十六进制。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 名称与版本覆盖：空格、加号、已有百分号文本、点/连字符/下划线/
        # 波浪号保留、非 ASCII 字符（ï = U+00EF -> UTF-8 C3 AF）。
        self.data = lock_data(
            {"a b": "1.0 0"},
            {
                "a b": "1.0 0",
                "keep-it.UP_~": "1.0.0",
                "plus": "1+2",
                "pct": "100%20down",
                "naïve": "v ä",
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "sample.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_percent_encoding_rules(self):
        root_deps, packages_map = load_lockfile(self.path)
        by_name = {
            item["name"]: item
            for item in sbom_document(root_deps, packages_map, with_purl=True)[
                "components"
            ]
        }
        # 空格 -> %20（名称与版本各自编码）。
        self.assertEqual(by_name["a b"]["purl"], "pkg:npm/a%20b@1.0%200")
        # - . _ ~ 与 ASCII 字母数字保留，大小写不改。
        self.assertEqual(
            by_name["keep-it.UP_~"]["purl"], "pkg:npm/keep-it.UP_~@1.0.0"
        )
        # 加号 -> %2B。
        self.assertEqual(by_name["plus"]["purl"], "pkg:npm/plus@1%2B2")
        # 已有百分号文本只作普通字符：% 编码为 %25，绝不先解码
        # （不得变成 "100%20down" 解码再编码后的形态）。
        self.assertEqual(
            by_name["pct"]["purl"], "pkg:npm/pct@100%2520down"
        )
        # 非 ASCII 按 UTF-8 字节逐字节转义，十六进制字母大写。
        self.assertEqual(
            by_name["naïve"]["purl"], "pkg:npm/na%C3%AFve@v%20%C3%A4"
        )

    def test_scoped_scope_at_encoded_but_separator_kept(self):
        data = lock_data(
            {"@s cope/pkg": "1.0"},
            {"@s cope/pkg": "1.0", "@x/y": "1+1"},
        )
        path = write_lockfile(self.tmp, data, "scoped.json")
        root_deps, packages_map = load_lockfile(path)
        by_name = {
            item["name"]: item
            for item in sbom_document(root_deps, packages_map, with_purl=True)[
                "components"
            ]
        }
        # 作用域开头的 @ 编码为 %40，作用域中的空格为 %20；结构斜杠与
        # 版本前的 @ 原样保留。
        self.assertEqual(
            by_name["@s cope/pkg"]["purl"], "pkg:npm/%40s%20cope/pkg@1.0"
        )
        self.assertEqual(by_name["@x/y"]["purl"], "pkg:npm/%40x/y@1%2B1")

    def test_version_with_at_and_special_chars(self):
        # 版本中的 @ 不是结构分隔符，必须编码；版本范围形态原样保留，
        # 不解析、不改大小写。
        data = lock_data({"a": "^1.2.3-Beta.0"}, {"a": "^1.2.3-Beta.0@tag"})
        path = write_lockfile(self.tmp, data, "ver.json")
        root_deps, packages_map = load_lockfile(path)
        item = sbom_document(
            root_deps, packages_map, with_purl=True
        )["components"][0]
        self.assertEqual(item["version"], "^1.2.3-Beta.0@tag")
        self.assertEqual(
            item["purl"], "pkg:npm/a@%5E1.2.3-Beta.0%40tag"
        )


class SbomPurlCompatibility(unittest.TestCase):
    """不带选项时输出不变；位置/关键字调用兼容。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(
            self.tmp,
            lock_data(
                {"@scope/a": "1.0.0", "alpha": "1.0.0"},
                {
                    "@scope/a": ("1.0.0", {"beta": "2.0.0"}),
                    "alpha": ("1.0.0", {"beta": "2.0.0"}),
                    "beta": "2.0.0",
                    "orphan": "3.0.0",
                },
            ),
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_outputs_without_flag_unchanged(self):
        root_deps, packages_map = load_lockfile(self.path)
        for call in (
            lambda: sbom_document(root_deps, packages_map),
            lambda: sbom_document(root_deps, packages_map, False),
            lambda: sbom_document(root_deps, packages_map, reachable=False),
            lambda: sbom_document(root_deps, packages_map, False, False),
            lambda: sbom_document(
                root_deps, packages_map, False, False, False, False
            ),
            lambda: sbom_document(root_deps, packages_map, with_purl=False),
        ):
            doc = call()
            for item in doc["components"]:
                self.assertEqual(set(item), BASE_FIELDS)
                self.assertNotIn("purl", item)
        result = run_cli(["sbom", self.path])
        self.assertEqual(result.returncode, 0)
        for item in json.loads(result.stdout)["components"]:
            self.assertNotIn("purl", item)

    def test_positional_and_keyword_calls_equivalent(self):
        root_deps, packages_map = load_lockfile(self.path)
        # reachable, with_paths, with_dependencies, with_purl 的位置形式。
        positional = sbom_document(root_deps, packages_map, True, True, True, True)
        keyword = sbom_document(
            root_deps,
            packages_map,
            reachable=True,
            with_paths=True,
            with_dependencies=True,
            with_purl=True,
        )
        self.assertEqual(positional, keyword)
        for item in positional["components"]:
            self.assertEqual(
                set(item), BASE_FIELDS | {"purl", "path", "dependencies"}
            )

    def test_root_only_empty_components(self):
        path = write_lockfile(
            self.tmp,
            {"lockfileVersion": 3, "packages": {"": {}}},
            "root-only.json",
        )
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(
            sbom_document(root_deps, packages_map, with_purl=True),
            {"format": "depinventory-sbom", "formatVersion": 1, "components": []},
        )
        result = run_cli(["sbom", path, "--with-purl"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"format": "depinventory-sbom", "formatVersion": 1, "components": []},
        )


class SbomPurlCombos(unittest.TestCase):
    """--with-purl 与既有三个选项组合，筛选、排序、附加字段语义不变。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # alpha 直接依赖并依赖 @scope/leaf；orphan 已安装但根不可达。
        self.data = lock_data(
            {"alpha": "*"},
            {
                "alpha": ("1.0.0", {"@scope/leaf": "*"}),
                "@scope/leaf": "2.0.0",
                "orphan": "3.0.0",
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "sample.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_unreachable_kept_without_reachable(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(root_deps, packages_map, with_purl=True)
        self.assertEqual(
            [item["name"] for item in doc["components"]],
            ["@scope/leaf", "alpha", "orphan"],
        )
        self.assertTrue(all("purl" in item for item in doc["components"]))

    def test_reachable_filters_before_purl(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(
            root_deps, packages_map, reachable=True, with_purl=True
        )
        self.assertEqual(
            [item["name"] for item in doc["components"]], ["@scope/leaf", "alpha"]
        )

    def test_all_four_flags_cli(self):
        result = run_cli(
            [
                "sbom",
                self.path,
                "--reachable",
                "--with-paths",
                "--with-dependencies",
                "--with-purl",
            ]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        doc = json.loads(result.stdout)
        leaf, alpha = doc["components"]
        self.assertEqual(leaf["name"], "@scope/leaf")
        self.assertEqual(leaf["purl"], "pkg:npm/%40scope/leaf@2.0.0")
        self.assertEqual(leaf["path"], ["$root", "alpha", "@scope/leaf"])
        self.assertEqual(leaf["dependencies"], [])
        self.assertEqual(alpha["purl"], "pkg:npm/alpha@1.0.0")
        self.assertEqual(alpha["path"], ["$root", "alpha"])
        self.assertEqual(alpha["dependencies"], ["@scope/leaf"])

    def test_order_independence(self):
        packages = {}
        for key in reversed(list(self.data["packages"].keys())):
            packages[key] = self.data["packages"][key]
        reversed_path = write_lockfile(
            self.tmp,
            {"lockfileVersion": 3, "packages": packages},
            "reversed.json",
        )
        doc_a = sbom_document(*load_lockfile(self.path), with_purl=True)
        doc_b = sbom_document(*load_lockfile(reversed_path), with_purl=True)
        self.assertEqual(doc_a, doc_b)


class SbomPurlValidationAndIO(unittest.TestCase):
    """新选项不放宽校验；标识无法编码为 UTF-8 同样整体失败。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_input_error_cli(self, argv):
        result = run_cli(argv)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_unreadable_bad_utf8_broken_json_and_bad_structure(self):
        self.assert_input_error_cli(
            ["sbom", os.path.join(self.tmp, "missing.json"), "--with-purl"]
        )
        self.assert_input_error_cli(["sbom", BAD_UTF8, "--with-purl"])
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.assert_input_error_cli(["sbom", broken, "--with-purl"])
        # 结构违规：包条目缺少非空 version，即使根不可达也整份拒绝。
        bad = write_lockfile(
            self.tmp,
            lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": "2.0.0"}),
            "bad.json",
        )
        import json as _json

        with open(bad, encoding="utf-8") as handle:
            data = _json.load(handle)
        del data["packages"]["node_modules/orphan"]["version"]
        with open(bad, "w", encoding="utf-8") as handle:
            _json.dump(data, handle)
        self.assert_input_error_cli(["sbom", bad, "--with-purl"])

    def test_unencodable_identifier_raises_input_error(self):
        # JSON 转义出的孤对代理 U+D83F：Python 字符串合法（surrogatepass），
        # 但无法编码为 UTF-8；带 purl 时必须整体失败，不输出部分结果。
        path = os.path.join(self.tmp, "surrogate.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                '{"lockfileVersion":3,"packages":{'
                '"":{"dependencies":{"bad":"1"}},'
                '"node_modules/bad":{"version":"1.0-\\ud83f"}}}'
            )
        root_deps, packages_map = load_lockfile(path)
        with self.assertRaises(InputError):
            sbom_document(root_deps, packages_map, with_purl=True)
        self.assert_input_error_cli(["sbom", path, "--with-purl"])
        # 与其他选项组合同样失败。
        self.assert_input_error_cli(
            ["sbom", path, "--with-purl", "--reachable", "--with-paths"]
        )

    def test_input_file_unchanged(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"a b": "1.0.0"},
                {"a b": ("1.0 0", {"@s/c": "2.0.0"}), "@s/c": "2.0.0"},
            ),
        )
        before = read_bytes(path)
        run_cli(["sbom", path, "--with-purl"])
        run_cli(
            [
                "sbom",
                path,
                "--with-purl",
                "--reachable",
                "--with-paths",
                "--with-dependencies",
            ]
        )
        self.assertEqual(read_bytes(path), before)

    def test_arguments_not_mutated(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"alpha": "1.0.0"},
                {"alpha": ("1.0.0", {"beta": "2.0.0"}), "beta": "2.0.0"},
            ),
        )
        root_deps, packages_map = load_lockfile(path)
        deps_before = copy.deepcopy(root_deps)
        map_before = copy.deepcopy(packages_map)
        doc = sbom_document(root_deps, packages_map, with_purl=True)
        self.assertEqual(root_deps, deps_before)
        self.assertEqual(packages_map, map_before)
        # 改写组件不回写入参。
        doc["components"][0]["purl"] = "MUTATED"
        self.assertEqual(packages_map, map_before)


if __name__ == "__main__":
    unittest.main()
