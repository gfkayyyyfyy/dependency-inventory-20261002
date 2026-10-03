"""sbom --reachable 根可达筛选的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖；调用真实的
depinventory.load_lockfile 与 depinventory.sbom_document_reachable，
不以替身代替。覆盖：

- 基本验收：python -m depinventory sbom new-lock.json --reachable
  组件只有 beta（2.1.0、direct 为 true）；省略选项时仍含 beta 与
  gamma（3.0.0、direct 为 false）；
- 筛选语义与 list --reachable 一致：自根 dependencies 沿已安装包的
  dependencies 逐层可达；作用域包、传递依赖、多路径去重、可达自环与
  循环保留，与根断开的包和循环整体排除；根项目不成为组件；结果按完整
  包名 Unicode 码点升序，名称与版本原样保留，direct 仅取决于根
  dependencies；
- 根 dependencies 省略、为空对象或只有根节点时 components 为 []，
  format 与 formatVersion 标记仍保留；
- 条目与依赖声明顺序不影响结果；
- 筛选前校验整份输入：不可达条目的缺失版本、非法 dependencies 或悬空
  依赖同样整体失败——接口抛 InputError，CLI 退出码 2、标准输出为空、
  标准错误仅 "INPUT_ERROR\\n"，不输出部分组件或堆栈；
- CLI 成功约定：退出码 0、标准错误为空、标准输出为带末尾换行的单个
  JSON 文档，解析后与函数结果一致；
- 导出前后输入文件字节不变，函数调用不修改根依赖列表与包映射；
- sbom_document 既有两参数调用与结果保持不变。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import (
    InputError,
    load_lockfile,
    sbom_document,
    sbom_document_reachable,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    return path


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


def component(name, version, direct):
    return {
        "name": name,
        "version": version,
        "ecosystem": "npm",
        "direct": direct,
        "license": "unknown",
        "securityStatus": "unknown",
    }


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


class SbomReachableNewLockAcceptance(unittest.TestCase):
    """基本验收：python -m depinventory sbom new-lock.json --reachable。"""

    def test_cli_reachable_only_beta(self):
        result = run_cli(["sbom", NEW_LOCK, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "format": "depinventory-sbom",
                "formatVersion": 1,
                "components": [component("beta", "2.1.0", True)],
            },
        )

    def test_cli_full_export_still_contains_beta_and_gamma(self):
        result = run_cli(["sbom", NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout)["components"],
            [
                component("beta", "2.1.0", True),
                component("gamma", "3.0.0", False),
            ],
        )

    def test_api_matches_cli(self):
        root_deps, packages_map = load_lockfile(NEW_LOCK)
        doc = sbom_document_reachable(root_deps, packages_map)
        self.assertEqual(doc["format"], "depinventory-sbom")
        self.assertIs(doc["formatVersion"], 1)
        self.assertEqual(list(doc), ["format", "formatVersion", "components"])
        self.assertEqual(doc["components"], [component("beta", "2.1.0", True)])
        cli = run_cli(["sbom", NEW_LOCK, "--reachable"])
        self.assertEqual(json.loads(cli.stdout), doc)


class SbomReachableStructuredSample(unittest.TestCase):
    """作用域包、传递依赖、多路径、自环、循环与断开循环共存的筛选语义。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 根直接声明 @scope/tool 与 alpha；两者都依赖 beta（多路径）；
        # beta 依赖 alpha 与自身（循环 + 自环）；loop-x <-> loop-y 与根
        # 断开；orphan 不被任何节点引用。版本字符串使用非标准形态核对
        # 原样保留。
        self.data = lock_data(
            {"alpha": "*", "@scope/tool": "*"},
            {
                "alpha": ("1.0.0", {"beta": "*"}),
                "@scope/tool": ("=2.0.0-rc.1+build.3", {"beta": "*"}),
                "beta": ("2.0.0", {"alpha": "*", "beta": "*"}),
                "loop-x": ("1.0.0", {"loop-y": "*"}),
                "loop-y": ("1.0.0", {"loop-x": "*"}),
                "orphan": ("9.9.9", {"beta": "*"}),
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "sample.json")

    def tearDown(self):
        self._tmp.cleanup()

    def expected_components(self):
        return [
            # @ (U+0040) 排在字母之前；其余按名称 Unicode 码点升序。
            component("@scope/tool", "=2.0.0-rc.1+build.3", True),
            component("alpha", "1.0.0", True),
            component("beta", "2.0.0", False),
        ]

    def test_reachable_components(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document_reachable(root_deps, packages_map)
        self.assertEqual(doc["components"], self.expected_components())

        names = [item["name"] for item in doc["components"]]
        # 多路径引入的 beta 只输出一次；根项目不作为组件；断开的
        # loop-x/loop-y 循环与 orphan 整体排除。
        self.assertEqual(names, ["@scope/tool", "alpha", "beta"])
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("", names)
        # Unicode 码点升序的独立核对。
        self.assertEqual(names, sorted(names, key=lambda s: [ord(c) for c in s]))
        # 每个组件仍恰好六个固定字段。
        for item in doc["components"]:
            self.assertEqual(
                set(item),
                {"name", "version", "ecosystem", "direct", "license", "securityStatus"},
            )
            self.assertEqual(item["license"], "unknown")
            self.assertEqual(item["securityStatus"], "unknown")

    def test_direct_depends_only_on_root_dependencies(self):
        root_deps, packages_map = load_lockfile(self.path)
        by_name = {
            c["name"]: c
            for c in sbom_document_reachable(root_deps, packages_map)["components"]
        }
        # beta 被 alpha、@scope/tool 与自身引用，仍为传递依赖。
        self.assertTrue(by_name["alpha"]["direct"])
        self.assertTrue(by_name["@scope/tool"]["direct"])
        self.assertFalse(by_name["beta"]["direct"])

    def test_full_export_unchanged_by_option_existence(self):
        # 同一文件省略选项时仍导出全部已安装包（sbom_document 两参数行为）。
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(root_deps, packages_map)
        self.assertEqual(
            [c["name"] for c in doc["components"]],
            ["@scope/tool", "alpha", "beta", "loop-x", "loop-y", "orphan"],
        )

    def test_cli_matches_function_result(self):
        root_deps, packages_map = load_lockfile(self.path)
        result = run_cli(["sbom", self.path, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(
            json.loads(result.stdout),
            sbom_document_reachable(root_deps, packages_map),
        )


class SbomReachableEmptyRoot(unittest.TestCase):
    """根 dependencies 省略、为空对象或只有根节点时 components 为 []。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def check_empty(self, data, name):
        path = write_lockfile(self.tmp, data, name)
        root_deps, packages_map = load_lockfile(path)
        expected = {"format": "depinventory-sbom", "formatVersion": 1, "components": []}
        self.assertEqual(sbom_document_reachable(root_deps, packages_map), expected)
        result = run_cli(["sbom", path, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)

    def test_root_dependencies_omitted(self):
        # 根节点无 dependencies 键；已安装条目间存在合法循环也不可达。
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {},
                "node_modules/loop-x": {
                    "version": "1.0.0",
                    "dependencies": {"loop-y": "*"},
                },
                "node_modules/loop-y": {
                    "version": "1.0.0",
                    "dependencies": {"loop-x": "*"},
                },
            },
        }
        self.check_empty(data, "omitted.json")

    def test_root_dependencies_empty_object(self):
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {}},
                "node_modules/alpha": {"version": "1.0.0"},
            },
        }
        self.check_empty(data, "empty.json")

    def test_root_only(self):
        data = {"lockfileVersion": 3, "packages": {"": {}}}
        self.check_empty(data, "root-only.json")


class SbomReachableOrderIndependence(unittest.TestCase):
    """条目与依赖声明顺序不影响筛选结果。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_reversed_order_identical_document(self):
        data = lock_data(
            {"zeta": "*", "alpha": "*"},
            {
                "zeta": ("1.0.0", {"mid": "*"}),
                "alpha": ("1.0.0", {"mid": "*"}),
                "mid": "1.0.0",
                "orphan": "2.0.0",
            },
        )
        path_a = write_lockfile(self.tmp, data, "a.json")

        reversed_data = copy.deepcopy(data)
        package_keys = [k for k in reversed_data["packages"] if k != ""]
        reordered = {"": reversed_data["packages"][""]}
        for key in reversed(package_keys):
            reordered[key] = reversed_data["packages"][key]
        reversed_data["packages"] = reordered
        for node in reversed_data["packages"].values():
            deps = node.get("dependencies")
            if isinstance(deps, dict):
                node["dependencies"] = dict(reversed(list(deps.items())))
        path_b = write_lockfile(self.tmp, reversed_data, "b.json")

        doc_a = sbom_document_reachable(*load_lockfile(path_a))
        doc_b = sbom_document_reachable(*load_lockfile(path_b))
        self.assertEqual(doc_a, doc_b)
        self.assertEqual(
            [c["name"] for c in doc_a["components"]],
            ["alpha", "mid", "zeta"],
        )

        out_a = run_cli(["sbom", path_a, "--reachable"]).stdout
        out_b = run_cli(["sbom", path_b, "--reachable"]).stdout
        self.assertEqual(out_a, out_b)


class SbomReachableInputError(unittest.TestCase):
    """筛选前校验整份输入：不可达条目的结构问题同样整体失败。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def check_input_error(self, data, name):
        path = write_lockfile(self.tmp, data, name)
        with self.assertRaises(InputError):
            load_lockfile(path)
        result = run_cli(["sbom", path, "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotIn("Traceback", result.stderr)
        # 不输出部分组件。
        self.assertNotIn("components", result.stdout)
        self.assertNotIn("alpha", result.stdout)
        return path

    def test_unreachable_entry_missing_version(self):
        # alpha 可达且结构合法；不可达的 orphan 缺少 version。
        self.check_input_error(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"dependencies": {"alpha": "*"}},
                    "node_modules/alpha": {"version": "1.0.0"},
                    "node_modules/orphan": {"dependencies": {}},
                },
            },
            "missing-version.json",
        )

    def test_unreachable_entry_illegal_dependencies(self):
        # 不可达的 orphan 的 dependencies 为 null（非对象）。
        self.check_input_error(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"dependencies": {"alpha": "*"}},
                    "node_modules/alpha": {"version": "1.0.0"},
                    "node_modules/orphan": {"version": "1.0.0", "dependencies": None},
                },
            },
            "illegal-deps.json",
        )

    def test_unreachable_entry_dangling_dependency(self):
        # 不可达的 orphan 声明指向未安装的 ghost。
        self.check_input_error(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"dependencies": {"alpha": "*"}},
                    "node_modules/alpha": {"version": "1.0.0"},
                    "node_modules/orphan": {
                        "version": "1.0.0",
                        "dependencies": {"ghost": "*"},
                    },
                },
            },
            "dangling.json",
        )

    def test_broken_json_and_unsupported_structure(self):
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write('{"lockfileVersion": 3, "packages": ')
        result = run_cli(["sbom", broken, "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")

        nested = write_lockfile(
            self.tmp,
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"dependencies": {"alpha": "*"}},
                    "node_modules/alpha": {"version": "1.0.0"},
                    "node_modules/alpha/node_modules/beta": {"version": "2.0.0"},
                },
            },
            "nested.json",
        )
        result = run_cli(["sbom", nested, "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")

    def test_missing_file(self):
        result = run_cli(
            ["sbom", os.path.join(self.tmp, "no-such.json"), "--reachable"]
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")


class SbomReachableInputsUnchanged(unittest.TestCase):
    """导出前后输入文件字节不变；函数不修改入参结构。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = lock_data(
            {"@scope/a": "*"},
            {
                "@scope/a": ("1.0.0", {"loop-y": "*"}),
                "loop-x": ("1.0.0", {"loop-y": "*"}),
                "loop-y": ("0.0.1", {"loop-x": "*"}),
                "orphan": "9.9.9",
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "sample.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_file_bytes_identical_before_and_after(self):
        with open(self.path, "rb") as handle:
            before = handle.read()
        for argv in (["sbom", self.path, "--reachable"], ["sbom", self.path]):
            result = run_cli(argv)
            self.assertEqual(result.returncode, 0)
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_arguments_not_mutated(self):
        root_deps, packages_map = load_lockfile(self.path)
        deps_before = copy.deepcopy(root_deps)
        map_before = copy.deepcopy(packages_map)
        doc = sbom_document_reachable(root_deps, packages_map)
        self.assertEqual(root_deps, deps_before)
        self.assertEqual(packages_map, map_before)
        # 组件是独立结构：改写组件不回写包映射。
        doc["components"][0]["version"] = "MUTATED"
        self.assertEqual(packages_map, map_before)
        again = sbom_document_reachable(root_deps, packages_map)
        self.assertEqual(
            again["components"][0]["version"],
            map_before[again["components"][0]["name"]]["version"],
        )


if __name__ == "__main__":
    unittest.main()
