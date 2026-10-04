"""sbom --with-paths 来源路径导出的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖；调用真实的
depinventory.load_lockfile 与 depinventory.sbom_document，不以替身
代替。覆盖：

- 基本验收：demo-lock.json --with-paths 下 alpha 的 path 为
  ["$root","alpha"]、beta 为 ["$root","alpha","beta"]；new-lock.json
  下 beta 为 ["$root","beta"]、gamma 为 []，再加 --reachable 只保留
  beta；只有根节点时 components 仍为 []；
- path 与省略 --from 的 why 默认查询（find_path）逐组件一致：最短边数、
  等长按包名序列 Unicode 码点字典序取第一条，作用域包作为单个路径元素，
  自环、循环、共享依赖正常结束，路径中各组件只出现一次；
- 省略 --with-paths 时组件字段集与完整导出逐字节保持原样，with_paths
  默认 False；reachable 的位置参数与关键字调用继续有效；
- 组件排序、direct、name/version 原文、license/securityStatus 均不变；
- 成功输出单个 JSON 文档加末尾换行，退出 0、标准错误为空；
- 校验不因新选项放宽：不可达包缺失版本或悬空依赖仍拒绝整份输入；
  文件不可读、非法 UTF-8、损坏 JSON、不支持结构统一退出 2、stdout 为空、
  stderr 仅 "INPUT_ERROR\\n"，读取接口抛 InputError；
- 导出前后输入文件字节不变，sbom_document 不修改根依赖列表与包映射。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, find_path, load_lockfile, sbom_document

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")
BAD_UTF8 = os.path.join(REPO_ROOT, "bad-utf8.json")

BASE_FIELDS = {"name", "version", "ecosystem", "direct", "license", "securityStatus"}
PATH_FIELDS = BASE_FIELDS | {"path"}


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


def write_raw(directory, raw, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(raw)
    return path


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


def component(name, version, direct, path=None):
    item = {
        "name": name,
        "version": version,
        "ecosystem": "npm",
        "direct": direct,
        "license": "unknown",
        "securityStatus": "unknown",
    }
    if path is not None:
        item["path"] = path
    return item


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


class SbomWithPathsAcceptance(unittest.TestCase):
    """基本验收：demo/new 两份样例锁文件上的 --with-paths 与组合选项。"""

    def test_demo_paths_api_and_cli(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        doc = sbom_document(root_deps, packages_map, with_paths=True)
        self.assertEqual(doc["format"], "depinventory-sbom")
        self.assertIs(doc["formatVersion"], 1)
        self.assertEqual(
            doc["components"],
            [
                component("alpha", "1.0.0", True, ["$root", "alpha"]),
                component("beta", "2.0.0", False, ["$root", "alpha", "beta"]),
            ],
        )

        result = run_cli(["sbom", DEMO_LOCK, "--with-paths"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed, doc)
        self.assertEqual(parsed["components"][0]["path"], ["$root", "alpha"])
        self.assertEqual(parsed["components"][1]["path"], ["$root", "alpha", "beta"])

    def test_new_lock_unreachable_gamma_path_empty(self):
        root_deps, packages_map = load_lockfile(NEW_LOCK)
        doc = sbom_document(root_deps, packages_map, with_paths=True)
        self.assertEqual(
            doc["components"],
            [
                component("beta", "2.1.0", True, ["$root", "beta"]),
                component("gamma", "3.0.0", False, []),
            ],
        )
        result = run_cli(["sbom", NEW_LOCK, "--with-paths"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), doc)

    def test_new_lock_with_paths_and_reachable_keeps_only_beta(self):
        root_deps, packages_map = load_lockfile(NEW_LOCK)
        # 关键字组合调用。
        doc = sbom_document(
            root_deps, packages_map, reachable=True, with_paths=True
        )
        self.assertEqual(
            doc["components"],
            [component("beta", "2.1.0", True, ["$root", "beta"])],
        )
        # reachable 位置参数仍有效。
        self.assertEqual(
            sbom_document(root_deps, packages_map, True, True), doc
        )
        for argv in (
            ["sbom", NEW_LOCK, "--reachable", "--with-paths"],
            ["sbom", NEW_LOCK, "--with-paths", "--reachable"],
        ):
            with self.subTest(argv=argv):
                result = run_cli(argv)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout), doc)

    def test_root_only_still_empty_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lockfile(
                tmp, {"lockfileVersion": 3, "packages": {"": {}}}, "root-only.json"
            )
            root_deps, packages_map = load_lockfile(path)
            doc = sbom_document(root_deps, packages_map, with_paths=True)
            self.assertEqual(
                doc,
                {"format": "depinventory-sbom", "formatVersion": 1, "components": []},
            )
            result = run_cli(["sbom", path, "--with-paths"])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), doc)


class SbomWithPathsSemantics(unittest.TestCase):
    """路径语义：why 一致性、字典序裁决、作用域、循环/自环/共享依赖。"""

    def sample_data(self):
        # 根声明 alpha 与 @scope/tool，二者共享传递依赖 beta；
        # beta 自环且回指 alpha（可达循环）；orphan↔zeta 与根断开。
        return {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "*", "@scope/tool": "*"}},
                "node_modules/alpha": {
                    "version": "1.0.0",
                    "dependencies": {"beta": "*"},
                },
                "node_modules/@scope/tool": {
                    "version": "1.0.0",
                    "dependencies": {"beta": "*"},
                },
                "node_modules/beta": {
                    "version": "2.0.0",
                    "dependencies": {"alpha": "*", "beta": "*"},
                },
                "node_modules/orphan": {
                    "version": "1.0.0",
                    "dependencies": {"zeta": "*"},
                },
                "node_modules/zeta": {
                    "version": "1.0.0",
                    "dependencies": {"orphan": "*"},
                },
            },
        }

    def test_paths_match_default_why_for_every_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lockfile(tmp, self.sample_data())
            root_deps, packages_map = load_lockfile(path)
            doc = sbom_document(root_deps, packages_map, with_paths=True)
            by_name = {item["name"]: item for item in doc["components"]}
            for name in sorted(packages_map):
                self.assertEqual(
                    by_name[name]["path"],
                    find_path(root_deps, packages_map, name),
                    name,
                )

    def test_explicit_paths_on_shared_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lockfile(tmp, self.sample_data())
            root_deps, packages_map = load_lockfile(path)
            by_name = {
                item["name"]: item
                for item in sbom_document(root_deps, packages_map, with_paths=True)[
                    "components"
                ]
            }
            # 两条等长路径 $root/@scope/tool/beta 与 $root/alpha/beta：
            # '@' (U+0040) < 'a' (U+0061)，取作用域一侧。
            self.assertEqual(by_name["beta"]["path"], ["$root", "@scope/tool", "beta"])
            self.assertEqual(by_name["alpha"]["path"], ["$root", "alpha"])
            self.assertEqual(by_name["@scope/tool"]["path"], ["$root", "@scope/tool"])
            # 与根断开的循环两包都保留在完整导出中，路径为空。
            self.assertEqual(by_name["orphan"]["path"], [])
            self.assertEqual(by_name["zeta"]["path"], [])

    def test_scoped_package_is_single_path_element(self):
        data = lock_data(
            {"@scope/a": "1.0.0"},
            {"@scope/a": ("1.0.0", {"@scope/b": "1.0.0"}), "@scope/b": "2.0.0"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lockfile(tmp, data)
            root_deps, packages_map = load_lockfile(path)
            by_name = {
                item["name"]: item
                for item in sbom_document(root_deps, packages_map, with_paths=True)[
                    "components"
                ]
            }
            self.assertEqual(by_name["@scope/a"]["path"], ["$root", "@scope/a"])
            self.assertEqual(
                by_name["@scope/b"]["path"], ["$root", "@scope/a", "@scope/b"]
            )

    def test_every_path_has_no_repeated_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lockfile(tmp, self.sample_data())
            root_deps, packages_map = load_lockfile(path)
            for item in sbom_document(root_deps, packages_map, with_paths=True)[
                "components"
            ]:
                elements = item["path"]
                self.assertEqual(len(elements), len(set(elements)), item["name"])
                if elements:
                    self.assertEqual(elements[0], "$root")
                    self.assertEqual(elements[-1], item["name"])

    def test_declaration_order_does_not_change_paths(self):
        data = self.sample_data()
        # 递归反转所有对象键与依赖声明顺序，图关系保持不变。
        def reverse_order(value):
            if isinstance(value, dict):
                return {
                    key: reverse_order(item)
                    for key, item in reversed(list(value.items()))
                }
            return value

        with tempfile.TemporaryDirectory() as tmp:
            path_a = write_lockfile(tmp, data, "a.json")
            path_b = write_lockfile(tmp, reverse_order(copy.deepcopy(data)), "b.json")
            doc_a = sbom_document(*load_lockfile(path_a), with_paths=True)
            doc_b = sbom_document(*load_lockfile(path_b), with_paths=True)
            self.assertEqual(doc_a, doc_b)
            out_a = run_cli(["sbom", path_a, "--with-paths"]).stdout
            out_b = run_cli(["sbom", path_b, "--with-paths"]).stdout
            self.assertEqual(out_a, out_b)

    def test_paths_reflect_graph_not_direct_flag(self):
        # direct 只看根 dependencies；gamma 直接声明却……此处构造一个
        # direct=False 但路径非空的传递依赖，核对两字段相互独立。
        data = lock_data(
            {"alpha": "1.0.0"},
            {"alpha": ("1.0.0", {"beta": "1.0.0"}), "beta": "2.0.0"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lockfile(tmp, data)
            root_deps, packages_map = load_lockfile(path)
            by_name = {
                item["name"]: item
                for item in sbom_document(root_deps, packages_map, with_paths=True)[
                    "components"
                ]
            }
            beta = by_name["beta"]
            self.assertFalse(beta["direct"])
            self.assertEqual(beta["path"], ["$root", "alpha", "beta"])


class SbomWithPathsFieldContract(unittest.TestCase):
    """字段集、默认值、排序、固定元数据、向后兼容。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = lock_data(
            {"@scope/a": "1.0.0", "alpha": "1.0.0"},
            {
                "@scope/a": ("1.0.0", {"beta": "2.0.0"}),
                "alpha": ("1.0.0", {"beta": "2.0.0"}),
                "beta": "2.0.0",
                "orphan": "9.9.9",
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "sample.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_field_sets_with_and_without_option(self):
        root_deps, packages_map = load_lockfile(self.path)
        for item in sbom_document(root_deps, packages_map, with_paths=True)[
            "components"
        ]:
            self.assertEqual(set(item), PATH_FIELDS)
            self.assertIsInstance(item["path"], list)
            self.assertEqual(item["license"], "unknown")
            self.assertEqual(item["securityStatus"], "unknown")
            self.assertEqual(item["ecosystem"], "npm")
        for item in sbom_document(root_deps, packages_map)["components"]:
            self.assertEqual(set(item), BASE_FIELDS)
            self.assertNotIn("path", item)

    def test_default_false_matches_omitted_export(self):
        root_deps, packages_map = load_lockfile(self.path)
        default_doc = sbom_document(root_deps, packages_map)
        self.assertEqual(
            default_doc, sbom_document(root_deps, packages_map, with_paths=False)
        )
        self.assertEqual(
            default_doc, sbom_document(root_deps, packages_map, False, False)
        )
        # 与命令行省略选项的输出一致。
        self.assertEqual(
            json.loads(run_cli(["sbom", self.path]).stdout), default_doc
        )

    def test_component_order_and_other_fields_unchanged(self):
        root_deps, packages_map = load_lockfile(self.path)
        plain = sbom_document(root_deps, packages_map)["components"]
        with_paths = sbom_document(root_deps, packages_map, with_paths=True)[
            "components"
        ]
        self.assertEqual(
            [item["name"] for item in plain],
            [item["name"] for item in with_paths],
        )
        for plain_item, path_item in zip(plain, with_paths):
            for field in BASE_FIELDS:
                self.assertEqual(plain_item[field], path_item[field])


class SbomWithPathsInputStillValidated(unittest.TestCase):
    """新选项不放宽整份校验，读取/解析/结构失败行为不变。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_cli_input_error(self, path):
        result = run_cli(["sbom", path, "--with-paths"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_version_on_unreachable_package_rejected(self):
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "1.0.0"}},
                "node_modules/alpha": {"version": "1.0.0"},
                "node_modules/orphan": {"dependencies": {}},
            },
        }
        path = write_lockfile(self.tmp, data, "orphan-no-version.json")
        with self.assertRaises(InputError):
            load_lockfile(path)
        self.assert_cli_input_error(path)

    def test_dangling_dependency_rejected(self):
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "1.0.0"}},
                "node_modules/alpha": {"version": "1.0.0"},
                "node_modules/orphan": {
                    "version": "1.0.0",
                    "dependencies": {"ghost": "*"},
                },
            },
        }
        path = write_lockfile(self.tmp, data, "ghost.json")
        with self.assertRaises(InputError):
            load_lockfile(path)
        self.assert_cli_input_error(path)

    def test_unreadable_bad_utf8_and_broken_json_rejected(self):
        # 不存在的文件：打开即失败。
        self.assert_cli_input_error(os.path.join(self.tmp, "missing.json"))
        # 仓库自带的非法 UTF-8 样例。
        self.assert_cli_input_error(BAD_UTF8)
        # 损坏 JSON。
        broken = write_raw(self.tmp, b'{"lockfileVersion": 3, ', "broken.json")
        self.assert_cli_input_error(broken)

    def test_unsupported_structure_rejected(self):
        # 嵌套安装路径属于不支持的结构。
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {},
                "node_modules/a/node_modules/nested": {"version": "1.0.0"},
            },
        }
        path = write_lockfile(self.tmp, data, "nested.json")
        with self.assertRaises(InputError):
            load_lockfile(path)
        self.assert_cli_input_error(path)

    def test_reachable_with_paths_also_validates_whole_input(self):
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "1.0.0"}},
                "node_modules/alpha": {"version": "1.0.0"},
                "node_modules/orphan": {"dependencies": {"ghost": "*"}},
            },
        }
        path = write_lockfile(self.tmp, data, "ghost.json")
        result = run_cli(["sbom", path, "--reachable", "--with-paths"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")


class SbomWithPathsInputsUnchanged(unittest.TestCase):
    """成功导出前后文件字节不变，函数不修改入参，输出为单个 JSON 文档。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = lock_data(
            {"alpha": "1.0.0", "@scope/tool": "1.0.0"},
            {
                "alpha": ("1.0.0", {"beta": "2.0.0", "alpha": "1.0.0"}),
                "@scope/tool": ("1.0.0", {"beta": "2.0.0"}),
                "beta": "2.0.0",
                "orphan": ("9.9.9", {"orphan": "9.9.9"}),
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "sample.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_file_bytes_identical_before_and_after(self):
        before = read_bytes(self.path)
        for argv in (
            ["sbom", self.path, "--with-paths"],
            ["sbom", self.path, "--reachable", "--with-paths"],
        ):
            result = run_cli(argv)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(read_bytes(self.path), before)

    def test_arguments_not_mutated(self):
        root_deps, packages_map = load_lockfile(self.path)
        deps_before = copy.deepcopy(root_deps)
        map_before = copy.deepcopy(packages_map)
        doc = sbom_document(root_deps, packages_map, with_paths=True)
        self.assertEqual(root_deps, deps_before)
        self.assertEqual(packages_map, map_before)
        # 改写导出的 path 不回写入参；再次导出结果不变。
        doc["components"][0]["path"].append("MUTATED")
        self.assertEqual(packages_map, map_before)
        again = sbom_document(root_deps, packages_map, with_paths=True)
        self.assertEqual(
            again["components"][0]["path"], ["$root", "@scope/tool"]
        )

    def test_cli_output_single_json_document(self):
        result = run_cli(["sbom", self.path, "--with-paths"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        root_deps, packages_map = load_lockfile(self.path)
        self.assertEqual(
            json.loads(result.stdout),
            sbom_document(root_deps, packages_map, with_paths=True),
        )


if __name__ == "__main__":
    unittest.main()
