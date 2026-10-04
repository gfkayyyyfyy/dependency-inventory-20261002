"""sbom --with-purl 组件 Package URL 导出的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖；调用真实的
depinventory.load_lockfile 与 depinventory.sbom_document，不以替身
代替。覆盖：

- 基本验收：loader-example.json --with-purl 下 alpha 为
  pkg:npm/alpha@1.0.0、@scope/leaf 为 pkg:npm/%40scope/leaf@2.0.0，
  根不可达的 orphan 同样获得标识；name、version 原样保留，组件仅在既有
  六个字段外多出 purl；CLI 退出码 0、stderr 空、stdout 为带末尾换行的
  单个 JSON 文档，且与函数结果一致；
- 编码规则：仅保留 ASCII 字母、数字及 -._~，其余字节按 UTF-8 百分号
  编码且十六进制大写，空格/+/% 分别为 %20/%2B/%25，已有百分号文本不解码，
  非 ASCII 按 UTF-8 字节编码，大小写原样保留，结构分隔的 / 与 @ 保留；
- 作用域包结构：pkg:npm/作用域/包名@版本，作用域含开头的 @；
- 组合语义：--reachable 只影响筛选、--with-paths/--with-dependencies
  附加字段互不影响，排序与其他字段语义不变；省略选项时没有 purl 字段；
  空清单 components 仍为 []；
- 位置/关键字兼容：with_purl 默认 False，既有位置参数语义不变；
- 不放宽校验：悬空依赖、缺失版本、文件不可读、非法 UTF-8、损坏 JSON
  统一退出码 2；名称或版本含 lone surrogate 导致标识无法按 UTF-8 编码时
  接口抛 InputError、CLI 退出 2、stdout 为空、stderr 仅 "INPUT_ERROR\\n"，
  不输出部分结果；不带 --with-purl 时同份文件仍可正常导出；
- 导出前后输入文件字节不变，函数不修改入参；其他查询入口不出现 purl。
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


def write_lockfile_text(directory, text, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
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
    """基本验收：python -m depinventory sbom loader-example.json --with-purl。"""

    EXPECTED_PURLS = {
        "@scope/leaf": "pkg:npm/%40scope/leaf@2.0.0",
        "alpha": "pkg:npm/alpha@1.0.0",
        "orphan": "pkg:npm/orphan@3.0.0",
    }

    def test_api_purls_and_fields(self):
        root_deps, packages_map = load_lockfile(LOADER_EXAMPLE)
        doc = sbom_document(root_deps, packages_map, with_purl=True)
        self.assertEqual(doc["format"], "depinventory-sbom")
        self.assertIs(doc["formatVersion"], 1)
        names = [item["name"] for item in doc["components"]]
        # 排序不变：@ 排在字母之前，alpha 在 orphan 之前。
        self.assertEqual(names, ["@scope/leaf", "alpha", "orphan"])
        for item in doc["components"]:
            # 仅比既有六个字段多出一个 purl，且 purl 排在最后。
            self.assertEqual(set(item), BASE_FIELDS | {"purl"})
            self.assertEqual(list(item)[-1], "purl")
            self.assertEqual(item["purl"], self.EXPECTED_PURLS[item["name"]])
            self.assertIsInstance(item["purl"], str)
            # name 与 version 原样保留，license/securityStatus 仍为 unknown。
            self.assertEqual(item["version"], packages_map[item["name"]]["version"])
            self.assertEqual(item["license"], "unknown")
            self.assertEqual(item["securityStatus"], "unknown")
        # 关键两例与任务给定的逐字结果核对。
        by_name = {item["name"]: item for item in doc["components"]}
        self.assertEqual(by_name["alpha"]["purl"], "pkg:npm/alpha@1.0.0")
        self.assertEqual(
            by_name["@scope/leaf"]["purl"], "pkg:npm/%40scope/leaf@2.0.0"
        )
        # orphan 根不可达，未筛选时同样获得标识。
        self.assertEqual(by_name["orphan"]["purl"], "pkg:npm/orphan@3.0.0")

    def test_cli_matches_function_and_output_conventions(self):
        root_deps, packages_map = load_lockfile(LOADER_EXAMPLE)
        result = run_cli(["sbom", LOADER_EXAMPLE, "--with-purl"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(
            json.loads(result.stdout),
            sbom_document(root_deps, packages_map, with_purl=True),
        )


class SbomPurlEncodingRules(unittest.TestCase):
    """百分号编码与作用域结构规则。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_percent_encoding_byte_rules(self):
        # 名称与版本故意覆盖：保留集 -._~、空格、+、已有百分号文本、
        # 非 ASCII（é 的 UTF-8 为 0xC3 0xA9）与大小写。
        packages = {
            # 保留字符原样；空格/%2B/%25；"20" 是百分号后的普通文本。
            "a-b.c_d~e": "1.0 2+3%20",
            # 非 ASCII 按 UTF-8 字节编码，十六进制字母大写。
            "café": "1.0",
            # 已有百分号文本不解码：% 一律成为 %25。
            "pct": "100%ok",
            # 大小写不改。
            "Alpha": "V1.0-RC",
        }
        data = lock_data({name: "x" for name in packages}, packages)
        path = write_lockfile(self.tmp, data)
        root_deps, packages_map = load_lockfile(path)
        by_name = {
            item["name"]: item
            for item in sbom_document(root_deps, packages_map, with_purl=True)[
                "components"
            ]
        }
        # '.' 保留；空格→%20、+→%2B、%→%25，后续 "20" 原样。
        self.assertEqual(
            by_name["a-b.c_d~e"]["purl"], "pkg:npm/a-b.c_d~e@1.0%202%2B3%2520"
        )
        self.assertEqual(by_name["café"]["purl"], "pkg:npm/caf%C3%A9@1.0")
        self.assertEqual(by_name["pct"]["purl"], "pkg:npm/pct@100%25ok")
        self.assertEqual(by_name["Alpha"]["purl"], "pkg:npm/Alpha@V1.0-RC")
        for item in by_name.values():
            # 结果本身只含 ASCII：非 ASCII 全部已编码。
            item["purl"].encode("ascii")

    def test_scoped_structure_and_separators(self):
        packages = {
            "@scope/leaf": "2.0.0",
            # 作用域与包名片段内的空格仍编码，分隔 / 保留。
            "@s c/na mé": "1.0",
            "alpha": "1.0.0",
        }
        data = lock_data({name: "x" for name in packages}, packages)
        path = write_lockfile(self.tmp, data)
        root_deps, packages_map = load_lockfile(path)
        by_name = {
            item["name"]: item
            for item in sbom_document(root_deps, packages_map, with_purl=True)[
                "components"
            ]
        }
        self.assertEqual(
            by_name["@scope/leaf"]["purl"], "pkg:npm/%40scope/leaf@2.0.0"
        )
        # @ → %40，片段内空格 → %20，é → %C3%A9；两个结构分隔符保留。
        self.assertEqual(
            by_name["@s c/na mé"]["purl"],
            "pkg:npm/%40s%20c/na%20m%C3%A9@1.0",
        )
        self.assertEqual(by_name["alpha"]["purl"], "pkg:npm/alpha@1.0.0")

    def test_name_and_version_remain_verbatim(self):
        data = lock_data(
            {"Alpha": "^1.2.3-rc.1+build.7"},
            {"Alpha": "^1.2.3-rc.1+build.7"},
        )
        path = write_lockfile(self.tmp, data)
        root_deps, packages_map = load_lockfile(path)
        item = sbom_document(root_deps, packages_map, with_purl=True)["components"][0]
        # 不解析版本范围、不改大小写；版本中的 + 在 purl 里编码，
        # 但 version 字段保持原样。
        self.assertEqual(item["name"], "Alpha")
        self.assertEqual(item["version"], "^1.2.3-rc.1+build.7")
        self.assertEqual(
            item["purl"], "pkg:npm/Alpha@%5E1.2.3-rc.1%2Bbuild.7"
        )


class SbomPurlCombinations(unittest.TestCase):
    """与 reachable/with_paths/with_dependencies 组合及默认行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 根声明 alpha；alpha→@scope/leaf；orphan 已安装但根不可达。
        self.data = lock_data(
            {"alpha": "1.0.0"},
            {
                "alpha": ("1.0.0", {"@scope/leaf": "2.0.0"}),
                "@scope/leaf": "2.0.0",
                "orphan": "3.0.0",
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "combo.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_reachable_combo(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(
            root_deps, packages_map, reachable=True, with_purl=True
        )
        self.assertEqual(
            [item["name"] for item in doc["components"]],
            ["@scope/leaf", "alpha"],
        )
        for item in doc["components"]:
            self.assertEqual(set(item), BASE_FIELDS | {"purl"})
        self.assertEqual(
            doc["components"][0]["purl"], "pkg:npm/%40scope/leaf@2.0.0"
        )
        result = run_cli(["sbom", self.path, "--with-purl", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), doc)

    def test_paths_and_dependencies_combos(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(
            root_deps,
            packages_map,
            with_purl=True,
            with_paths=True,
            with_dependencies=True,
        )
        by_name = {item["name"]: item for item in doc["components"]}
        for item in doc["components"]:
            self.assertEqual(
                set(item), BASE_FIELDS | {"purl", "path", "dependencies"}
            )
            self.assertEqual(list(item)[-1], "purl")
        self.assertEqual(by_name["alpha"]["path"], ["$root", "alpha"])
        self.assertEqual(by_name["alpha"]["dependencies"], ["@scope/leaf"])
        self.assertEqual(by_name["alpha"]["purl"], "pkg:npm/alpha@1.0.0")
        self.assertEqual(by_name["orphan"]["path"], [])
        self.assertEqual(by_name["orphan"]["dependencies"], [])
        self.assertEqual(by_name["orphan"]["purl"], "pkg:npm/orphan@3.0.0")
        result = run_cli(
            [
                "sbom",
                self.path,
                "--with-purl",
                "--with-paths",
                "--with-dependencies",
            ]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), doc)

    def test_without_flag_field_absent(self):
        root_deps, packages_map = load_lockfile(self.path)
        baseline = sbom_document(root_deps, packages_map)
        # 默认、位置参数到第五项、with_purl=False 关键字调用均无 purl，
        # 且与既有结果逐字段一致。
        for call in (
            lambda: sbom_document(root_deps, packages_map),
            lambda: sbom_document(root_deps, packages_map, False),
            lambda: sbom_document(root_deps, packages_map, False, False),
            lambda: sbom_document(root_deps, packages_map, False, False, False),
            lambda: sbom_document(
                root_deps, packages_map, False, False, False, False
            ),
            lambda: sbom_document(root_deps, packages_map, with_purl=False),
        ):
            doc = call()
            self.assertEqual(doc, baseline)
            for item in doc["components"]:
                self.assertNotIn("purl", item)
        result = run_cli(["sbom", self.path])
        self.assertEqual(result.returncode, 0)
        for item in json.loads(result.stdout)["components"]:
            self.assertNotIn("purl", item)

    def test_positional_sixth_argument(self):
        root_deps, packages_map = load_lockfile(self.path)
        positional = sbom_document(
            root_deps, packages_map, False, True, True, True
        )
        keyword = sbom_document(
            root_deps,
            packages_map,
            reachable=False,
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
        root_only = write_lockfile(
            self.tmp,
            {"lockfileVersion": 3, "packages": {"": {}}},
            "root-only.json",
        )
        root_deps, packages_map = load_lockfile(root_only)
        self.assertEqual(
            sbom_document(root_deps, packages_map, with_purl=True),
            {"format": "depinventory-sbom", "formatVersion": 1, "components": []},
        )
        result = run_cli(["sbom", root_only, "--with-purl"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"format": "depinventory-sbom", "formatVersion": 1, "components": []},
        )


class SbomPurlUnencodableInput(unittest.TestCase):
    """名称或版本含 lone surrogate：with_purl=True 时整体 InputError。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write_surrogate_lockfile(self, key_name, version):
        # 用 ASCII 转义写 JSON（"\\ud800"）：Python 解析后得到含 lone
        # surrogate 的字符串，能通过既有结构校验，但无法编码为 UTF-8。
        text = (
            '{"lockfileVersion": 3, "packages": {'
            '"": {"dependencies": {"' + key_name + '": "1.0.0"}}, '
            '"node_modules/' + key_name + '": {"version": "' + version + '"}}}'
        )
        return write_lockfile_text(self.tmp, text, "surrogate.json")

    def test_surrogate_in_name_and_version_raise_input_error(self):
        for key_name, version in (("\\ud800", "1.0.0"), ("ok", "\\udc00")):
            path = self._write_surrogate_lockfile(key_name, version)
            # 读取阶段不拒绝：lone surrogate 是合法的 Python 字符串。
            root_deps, packages_map = load_lockfile(path)
            with self.assertRaises(InputError):
                sbom_document(root_deps, packages_map, with_purl=True)
            result = run_cli(["sbom", path, "--with-purl"])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "INPUT_ERROR\n")
            self.assertNotIn("Traceback", result.stderr)
            # 省略 with_purl 时函数行为不变：仍正常返回、不含 purl
            # （标识编码只在 with_purl=True 时发生）。
            plain = sbom_document(root_deps, packages_map)
            self.assertEqual(len(plain["components"]), 1)
            self.assertNotIn("purl", plain["components"][0])


class SbomPurlValidationAndIO(unittest.TestCase):
    """新选项不放宽既有校验；失败输出约定不变；只读、不改入参。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_input_error_cli(self, path, extra):
        result = run_cli(["sbom", path, *extra])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_dangling_dep_and_missing_version_rejected(self):
        dangling = write_lockfile(
            self.tmp,
            lock_data(
                {"a": "1.0.0"},
                {"a": "1.0.0", "orphan": ("1.0.0", {"ghost": "1.0.0"})},
            ),
            "dangling.json",
        )
        with self.assertRaises(InputError):
            load_lockfile(dangling)
        self.assert_input_error_cli(dangling, ["--with-purl"])
        self.assert_input_error_cli(
            dangling, ["--with-purl", "--reachable", "--with-paths"]
        )

        missing = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": "2.0.0"})
        del missing["packages"]["node_modules/orphan"]["version"]
        missing_path = write_lockfile(self.tmp, missing, "missing-version.json")
        with self.assertRaises(InputError):
            load_lockfile(missing_path)
        self.assert_input_error_cli(missing_path, ["--with-purl"])

    def test_unreadable_file_bad_utf8_and_broken_json(self):
        self.assert_input_error_cli(
            os.path.join(self.tmp, "missing.json"), ["--with-purl"]
        )
        self.assert_input_error_cli(BAD_UTF8, ["--with-purl"])
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.assert_input_error_cli(broken, ["--with-purl"])

    def test_input_file_unchanged(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"alpha": "1.0.0"},
                {
                    "alpha": ("1.0.0", {"@scope/leaf": "2.0.0"}),
                    "@scope/leaf": "2.0.0",
                    "orphan": "3.0.0",
                },
            ),
        )
        before = read_bytes(path)
        run_cli(["sbom", path, "--with-purl"])
        run_cli(["sbom", path, "--with-purl", "--reachable"])
        run_cli(["sbom", path, "--with-purl", "--with-paths", "--with-dependencies"])
        self.assertEqual(read_bytes(path), before)

    def test_arguments_not_mutated(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"alpha": "1.0.0"},
                {"alpha": ("1.0.0", {"@scope/leaf": "2.0.0"}),
                 "@scope/leaf": "2.0.0"},
            ),
        )
        root_deps, packages_map = load_lockfile(path)
        deps_before = copy.deepcopy(root_deps)
        map_before = copy.deepcopy(packages_map)
        sbom_document(root_deps, packages_map, with_purl=True)
        sbom_document(
            root_deps,
            packages_map,
            reachable=True,
            with_paths=True,
            with_dependencies=True,
            with_purl=True,
        )
        self.assertEqual(root_deps, deps_before)
        self.assertEqual(packages_map, map_before)

    def test_other_commands_have_no_purl(self):
        # 其他查询入口继续保持既有行为：list 输出不出现 purl。
        result = run_cli(["list", LOADER_EXAMPLE])
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("purl", result.stdout)
        why = run_cli(["why", LOADER_EXAMPLE, "@scope/leaf"])
        self.assertEqual(why.returncode, 0)
        self.assertNotIn("purl", why.stdout)


if __name__ == "__main__":
    unittest.main()
