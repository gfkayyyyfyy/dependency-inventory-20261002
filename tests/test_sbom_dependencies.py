"""sbom --with-dependencies 组件直接依赖数组导出的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖；调用真实的
depinventory.load_lockfile 与 depinventory.sbom_document，不以替身
代替。覆盖：

- 基本验收：sample-lock.json --with-dependencies 下 alpha 为 ["beta"]、
  beta 为 ["leaf"]、leaf 为 ["beta"]（循环双方各自保留声明）、orphan
  为 ["leaf"]、isolated 为 []，组件排序不变；
- dependencies 只取该包条目直接声明的完整包名：不展开传递依赖、不附带
  版本范围、不从其他元数据补边；按 Unicode 码点升序去重、区分大小写，
  作用域包名作为整体保留；声明省略或为空时为 []；自环保留自身；
- 与 --reachable 组合只筛选组件集合，保留组件的 dependencies 不变；
  与 --with-paths 组合两数组各自独立附加；
- 根 dependencies 省略或为空：完整导出保留全部组件及其关系，带可达
  筛选时 components 为 []；只有根节点时 components 为 [] 且格式标记保留；
- 省略选项时组件字段集合仍是既有六个字段；两参数调用、reachable 与
  with_paths 的位置/关键字调用、with_dependencies 默认 False 全部兼容；
- 成功时退出码 0、stderr 为空、stdout 为带末尾换行的单个 JSON 文档；
- 不放宽校验：文件不可读、非法 UTF-8、损坏 JSON、不可达条目的缺失版本
  或悬空依赖统一退出码 2、stdout 为空、stderr 仅 "INPUT_ERROR\\n"；
- 导出前后输入文件字节不变，函数不修改入参。
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
    ROOT,
    load_lockfile,
    sbom_document,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_LOCK = os.path.join(REPO_ROOT, "sample-lock.json")
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


class SbomDependenciesSampleAcceptance(unittest.TestCase):
    """基本验收：sample-lock.json 的 --with-dependencies 输出。"""

    def test_sample_dependencies_api_and_cli(self):
        root_deps, packages_map = load_lockfile(SAMPLE_LOCK)
        doc = sbom_document(root_deps, packages_map, with_dependencies=True)
        self.assertEqual(doc["format"], "depinventory-sbom")
        self.assertIs(doc["formatVersion"], 1)
        self.assertEqual(
            doc["components"],
            [
                {**base_component("alpha", "1.0.0", True),
                 "dependencies": ["beta"]},
                {**base_component("beta", "1.0.0", False),
                 "dependencies": ["leaf"]},
                {**base_component("isolated", "1.0.0", False),
                 "dependencies": []},
                {**base_component("leaf", "1.0.0", False),
                 "dependencies": ["beta"]},
                {**base_component("orphan", "1.0.0", False),
                 "dependencies": ["leaf"]},
            ],
        )
        result = run_cli(["sbom", SAMPLE_LOCK, "--with-dependencies"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout), doc)

    def test_sample_reachable_combo_keeps_arrays(self):
        root_deps, packages_map = load_lockfile(SAMPLE_LOCK)
        doc = sbom_document(
            root_deps, packages_map, reachable=True, with_dependencies=True
        )
        self.assertEqual(
            doc["components"],
            [
                {**base_component("alpha", "1.0.0", True),
                 "dependencies": ["beta"]},
                {**base_component("beta", "1.0.0", False),
                 "dependencies": ["leaf"]},
                {**base_component("leaf", "1.0.0", False),
                 "dependencies": ["beta"]},
            ],
        )
        result = run_cli(
            ["sbom", SAMPLE_LOCK, "--with-dependencies", "--reachable"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), doc)

    def test_sample_with_paths_combo(self):
        root_deps, packages_map = load_lockfile(SAMPLE_LOCK)
        doc = sbom_document(
            root_deps, packages_map, with_paths=True, with_dependencies=True
        )
        by_name = {item["name"]: item for item in doc["components"]}
        self.assertEqual(by_name["alpha"]["path"], [ROOT, "alpha"])
        self.assertEqual(by_name["alpha"]["dependencies"], ["beta"])
        self.assertEqual(by_name["orphan"]["path"], [])
        self.assertEqual(by_name["orphan"]["dependencies"], ["leaf"])
        self.assertEqual(by_name["isolated"]["dependencies"], [])
        for item in by_name.values():
            self.assertEqual(set(item), BASE_FIELDS | {"path", "dependencies"})
        result = run_cli(
            ["sbom", SAMPLE_LOCK, "--with-dependencies", "--with-paths"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), doc)


class SbomDependenciesSemantics(unittest.TestCase):
    """数组语义：直接声明、排序去重、作用域、自环、循环、空声明。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 根声明 a。a 的依赖故意乱序书写且含作用域包与大小写混合；
        # selfy 自环；x↔y 循环；plain 无 dependencies 键；empty 为空对象；
        # deep 只被 a 传递引入，其自身声明不并入 a 的数组。
        self.data = lock_data(
            {"a": "1.0.0"},
            {
                "a": ("1.0.0", {
                    "zeta": "1.0.0",
                    "@s/pkg": "2.0.0",
                    "Alpha": "3.0.0",
                    "deep": "4.0.0",
                }),
                "deep": ("4.0.0", {"zeta": "1.0.0"}),
                "zeta": "1.0.0",
                "@s/pkg": "2.0.0",
                "Alpha": "3.0.0",
                "selfy": ("1.0.0", {"selfy": "1.0.0"}),
                "x": ("1.0.0", {"y": "1.0.0"}),
                "y": ("1.0.0", {"x": "1.0.0"}),
                "plain": "1.0.0",
                "empty": ("1.0.0", {}),
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "sample.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_direct_only_sorted_deduped_scoped_and_case_sensitive(self):
        root_deps, packages_map = load_lockfile(self.path)
        by_name = {
            item["name"]: item
            for item in sbom_document(
                root_deps, packages_map, with_dependencies=True
            )["components"]
        }
        # 只含直接声明：deep 是 a 的传递依赖 zeta 的声明者关系不并入；
        # 按 Unicode 码点升序："@s/pkg"(U+0040) < "Alpha"(U+0041) < "deep" < "zeta"。
        self.assertEqual(
            by_name["a"]["dependencies"],
            ["@s/pkg", "Alpha", "deep", "zeta"],
        )
        # 版本范围不附带，作用域包名整体保留。
        self.assertEqual(by_name["deep"]["dependencies"], ["zeta"])
        self.assertEqual(by_name["@s/pkg"]["dependencies"], [])
        self.assertEqual(by_name["Alpha"]["dependencies"], [])

    def test_self_loop_cycle_and_empty_declarations(self):
        root_deps, packages_map = load_lockfile(self.path)
        by_name = {
            item["name"]: item
            for item in sbom_document(
                root_deps, packages_map, with_dependencies=True
            )["components"]
        }
        # 自环保留自身；循环双方分别保留各自声明。
        self.assertEqual(by_name["selfy"]["dependencies"], ["selfy"])
        self.assertEqual(by_name["x"]["dependencies"], ["y"])
        self.assertEqual(by_name["y"]["dependencies"], ["x"])
        # 声明省略与空对象都输出 []。
        self.assertEqual(by_name["plain"]["dependencies"], [])
        self.assertEqual(by_name["empty"]["dependencies"], [])
        # 根项目不成为组件；组件不重复。
        names = [item["name"] for item in sbom_document(
            root_deps, packages_map, with_dependencies=True
        )["components"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("", names)
        self.assertEqual(names, sorted(names))

    def test_order_independence(self):
        packages = {}
        for key in reversed(list(self.data["packages"].keys())):
            node = {}
            for field in reversed(list(self.data["packages"][key].keys())):
                value = self.data["packages"][key][field]
                if field == "dependencies":
                    value = {dep: value[dep] for dep in reversed(list(value.keys()))}
                node[field] = value
            packages[key] = node
        reversed_path = write_lockfile(
            self.tmp,
            {"lockfileVersion": 3, "packages": packages},
            "reversed.json",
        )
        doc_a = sbom_document(*load_lockfile(self.path), with_dependencies=True)
        doc_b = sbom_document(*load_lockfile(reversed_path), with_dependencies=True)
        self.assertEqual(doc_a, doc_b)
        cli_a = run_cli(["sbom", self.path, "--with-dependencies"]).stdout
        cli_b = run_cli(["sbom", reversed_path, "--with-dependencies"]).stdout
        self.assertEqual(cli_a, cli_b)


class SbomDependenciesRootEdgeCases(unittest.TestCase):
    """根依赖省略或为空、只有根节点时的行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_root_deps_omitted_or_empty(self):
        for root_node in ({}, {"dependencies": {}}):
            data = {
                "lockfileVersion": 3,
                "packages": {
                    "": root_node,
                    "node_modules/a": {
                        "version": "1.0.0",
                        "dependencies": {"b": "1.0.0"},
                    },
                    "node_modules/b": {"version": "1.0.0"},
                },
            }
            path = write_lockfile(self.tmp, data, "no-root-deps.json")
            root_deps, packages_map = load_lockfile(path)
            # 完整导出保留已安装组件及其关系。
            doc = sbom_document(root_deps, packages_map, with_dependencies=True)
            self.assertEqual(
                doc["components"],
                [
                    {**base_component("a", "1.0.0", False),
                     "dependencies": ["b"]},
                    {**base_component("b", "1.0.0", False),
                     "dependencies": []},
                ],
            )
            # 带可达筛选时组件数组为空，格式标记保留。
            doc = sbom_document(
                root_deps, packages_map, reachable=True, with_dependencies=True
            )
            self.assertEqual(
                doc,
                {"format": "depinventory-sbom", "formatVersion": 1,
                 "components": []},
            )
            result = run_cli(
                ["sbom", path, "--with-dependencies", "--reachable"]
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout), doc)

    def test_root_only_keeps_format_markers(self):
        path = write_lockfile(
            self.tmp,
            {"lockfileVersion": 3, "packages": {"": {}}},
            "root-only.json",
        )
        root_deps, packages_map = load_lockfile(path)
        expected = {
            "format": "depinventory-sbom",
            "formatVersion": 1,
            "components": [],
        }
        self.assertEqual(
            sbom_document(root_deps, packages_map, with_dependencies=True),
            expected,
        )
        result = run_cli(["sbom", path, "--with-dependencies"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)


class SbomDependenciesCompatibility(unittest.TestCase):
    """不带选项时输出不变；既有位置/关键字调用保持兼容。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(
            self.tmp,
            lock_data(
                {"alpha": "1.0.0"},
                {"alpha": ("1.0.0", {"beta": "2.0.0"}), "beta": "2.0.0",
                 "orphan": "3.0.0"},
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
            lambda: sbom_document(root_deps, packages_map, with_paths=False),
            lambda: sbom_document(
                root_deps, packages_map, with_dependencies=False
            ),
            lambda: sbom_document(
                root_deps, packages_map, False, False, False
            ),
        ):
            doc = call()
            for item in doc["components"]:
                self.assertEqual(set(item), BASE_FIELDS)
                self.assertNotIn("dependencies", item)
                self.assertNotIn("path", item)
        # CLI 省略选项同样无 dependencies。
        result = run_cli(["sbom", self.path])
        self.assertEqual(result.returncode, 0)
        for item in json.loads(result.stdout)["components"]:
            self.assertNotIn("dependencies", item)

    def test_positional_and_keyword_calls(self):
        root_deps, packages_map = load_lockfile(self.path)
        positional = sbom_document(root_deps, packages_map, True, True, True)
        keyword = sbom_document(
            root_deps,
            packages_map,
            reachable=True,
            with_paths=True,
            with_dependencies=True,
        )
        self.assertEqual(positional, keyword)
        self.assertEqual(
            [item["name"] for item in positional["components"]],
            ["alpha", "beta"],
        )
        # with_dependencies 默认 False；旧式调用语义不变。
        self.assertEqual(
            sbom_document(root_deps, packages_map, True, True),
            sbom_document(
                root_deps, packages_map, reachable=True, with_paths=True
            ),
        )
        for item in sbom_document(root_deps, packages_map, True, True)["components"]:
            self.assertNotIn("dependencies", item)


class SbomDependenciesValidationAndIO(unittest.TestCase):
    """新选项不放宽校验；读接口抛 InputError，CLI 退出 2 且输出约定不变。"""

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

    def test_unreachable_missing_version_and_dangling_dep_rejected(self):
        base = lock_data(
            {"a": "1.0.0"},
            {"a": "1.0.0", "orphan": ("1.0.0", {"ghost": "1.0.0"})},
        )
        dangling = write_lockfile(self.tmp, base, "dangling.json")
        with self.assertRaises(InputError):
            load_lockfile(dangling)
        self.assert_input_error_cli(dangling, ["--with-dependencies"])
        self.assert_input_error_cli(
            dangling, ["--with-dependencies", "--reachable"]
        )

        missing = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": "2.0.0"})
        del missing["packages"]["node_modules/orphan"]["version"]
        missing_path = write_lockfile(self.tmp, missing, "missing-version.json")
        with self.assertRaises(InputError):
            load_lockfile(missing_path)
        self.assert_input_error_cli(missing_path, ["--with-dependencies"])

    def test_unreadable_file_bad_utf8_and_broken_json(self):
        self.assert_input_error_cli(
            os.path.join(self.tmp, "missing.json"), ["--with-dependencies"]
        )
        self.assert_input_error_cli(BAD_UTF8, ["--with-dependencies"])
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.assert_input_error_cli(broken, ["--with-dependencies"])

    def test_input_file_unchanged(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"a": "1.0.0"},
                {"a": ("1.0.0", {"b": "1.0.0"}), "b": "1.0.0", "o": "9.0.0"},
            ),
        )
        before = read_bytes(path)
        run_cli(["sbom", path, "--with-dependencies"])
        run_cli(["sbom", path, "--with-dependencies", "--reachable"])
        run_cli(["sbom", path, "--with-dependencies", "--with-paths"])
        self.assertEqual(read_bytes(path), before)

    def test_arguments_not_mutated(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"a": "1.0.0"},
                {"a": ("1.0.0", {"b": "1.0.0", "a": "1.0.0"}), "b": "1.0.0"},
            ),
        )
        root_deps, packages_map = load_lockfile(path)
        deps_before = copy.deepcopy(root_deps)
        map_before = copy.deepcopy(packages_map)
        sbom_document(root_deps, packages_map, with_dependencies=True)
        sbom_document(
            root_deps, packages_map,
            reachable=True, with_paths=True, with_dependencies=True,
        )
        self.assertEqual(root_deps, deps_before)
        self.assertEqual(packages_map, map_before)


if __name__ == "__main__":
    unittest.main()
