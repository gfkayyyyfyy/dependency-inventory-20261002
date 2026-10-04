"""sbom --with-dependencies 组件直接依赖数组导出的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖；调用真实的
depinventory.load_lockfile 与 depinventory.sbom_document，不以替身
代替。覆盖：

- 基本验收：sample-lock.json --with-dependencies 下 alpha 为 ["beta"]、
  beta 为 ["leaf"]、leaf 为 ["beta"]、orphan 为 ["leaf"]、isolated
  为 []，组件排序不变；加 --reachable 只保留 alpha、beta、leaf 且保留
  组件的依赖数组不变；与 --with-paths 组合时两个附加字段同时出现；
- dependencies 只取直接声明：按 Unicode 码点升序去重、区分大小写、
  作用域包名整体保留；声明省略或为空时为 []；自环保留自身，循环双方
  分别保留声明；不展开传递依赖、不带版本范围；
- 根 dependencies 省略或为空时完整导出保留组件及其关系，--reachable
  下组件数组为空；只有根节点时文档标记保留、components 为 []；
- 省略选项时组件字段集合仍是既有六个字段；两参数调用、reachable 与
  with_paths 的位置/关键字调用、with_dependencies 默认 False 全部兼容；
- 成功时退出码 0、stderr 为空、stdout 为带末尾换行的单个 JSON 文档；
- 不放宽校验：悬空依赖、缺失版本、文件不可读、非法 UTF-8、损坏 JSON
  统一退出码 2、stdout 为空、stderr 仅 "INPUT_ERROR\\n"，
  读取接口抛 InputError；
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
    """基本验收：sample-lock.json 的 --with-dependencies 输出与组合。"""

    EXPECTED = {
        "alpha": ["beta"],
        "beta": ["leaf"],
        "isolated": [],
        "leaf": ["beta"],
        "orphan": ["leaf"],
    }

    def test_sample_dependencies_api_and_cli(self):
        root_deps, packages_map = load_lockfile(SAMPLE_LOCK)
        doc = sbom_document(root_deps, packages_map, with_dependencies=True)
        self.assertEqual(doc["format"], "depinventory-sbom")
        self.assertIs(doc["formatVersion"], 1)
        names = [item["name"] for item in doc["components"]]
        # 组件排序不变：仍按完整包名 Unicode 码点升序，每包一次。
        self.assertEqual(names, ["alpha", "beta", "isolated", "leaf", "orphan"])
        for item in doc["components"]:
            self.assertEqual(set(item), BASE_FIELDS | {"dependencies"})
            self.assertEqual(item["dependencies"], self.EXPECTED[item["name"]])
            self.assertEqual(item["license"], "unknown")
            self.assertEqual(item["securityStatus"], "unknown")
        result = run_cli(["sbom", SAMPLE_LOCK, "--with-dependencies"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), doc)

    def test_reachable_combo_keeps_arrays_unchanged(self):
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

    def test_with_paths_combo_both_fields_present(self):
        root_deps, packages_map = load_lockfile(SAMPLE_LOCK)
        doc = sbom_document(
            root_deps, packages_map, with_paths=True, with_dependencies=True
        )
        by_name = {item["name"]: item for item in doc["components"]}
        for name, item in by_name.items():
            self.assertEqual(
                set(item), BASE_FIELDS | {"dependencies", "path"}
            )
            self.assertEqual(item["dependencies"], self.EXPECTED[name])
        self.assertEqual(by_name["alpha"]["path"], [ROOT, "alpha"])
        self.assertEqual(by_name["orphan"]["path"], [])
        result = run_cli(
            ["sbom", SAMPLE_LOCK, "--with-dependencies", "--with-paths"]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), doc)


class SbomDependenciesSemantics(unittest.TestCase):
    """数组语义：排序去重、大小写、作用域、自环、循环、空声明。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_sorted_dedup_case_sensitive_and_scoped(self):
        # 声明顺序故意打乱；JSON 对象键天然不重复，去重语义由
        # sorted(set(...)) 保证，此处验证排序、大小写与作用域整体保留。
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"hub": "1.0.0"},
                {
                    "hub": ("1.0.0", {
                        "zeta": "1.0.0",
                        "@s/pkg": "1.0.0",
                        "Alpha": "1.0.0",
                        "alpha": "1.0.0",
                    }),
                    "zeta": "1.0.0",
                    "@s/pkg": "1.0.0",
                    "Alpha": "1.0.0",
                    "alpha": "1.0.0",
                },
            ),
        )
        root_deps, packages_map = load_lockfile(path)
        by_name = {
            item["name"]: item
            for item in sbom_document(
                root_deps, packages_map, with_dependencies=True
            )["components"]
        }
        # Unicode 码点升序："@"<"A"<"a"<"z"；作用域包名为单个元素。
        self.assertEqual(
            by_name["hub"]["dependencies"],
            ["@s/pkg", "Alpha", "alpha", "zeta"],
        )
        self.assertEqual(by_name["@s/pkg"]["dependencies"], [])

    def test_self_loop_and_cycle_keep_declarations(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"a": "1.0.0"},
                {
                    "a": ("1.0.0", {"b": "1.0.0", "a": "1.0.0"}),
                    "b": ("1.0.0", {"a": "1.0.0"}),
                },
            ),
        )
        root_deps, packages_map = load_lockfile(path)
        by_name = {
            item["name"]: item
            for item in sbom_document(
                root_deps, packages_map, with_dependencies=True
            )["components"]
        }
        # 自环保留自身；循环双方分别保留各自的声明。
        self.assertEqual(by_name["a"]["dependencies"], ["a", "b"])
        self.assertEqual(by_name["b"]["dependencies"], ["a"])
        names = [item["name"] for item in sbom_document(
            root_deps, packages_map, with_dependencies=True
        )["components"]]
        self.assertEqual(names, ["a", "b"])

    def test_empty_and_missing_declaration_yield_empty_array(self):
        data = lock_data(
            {"a": "1.0.0"},
            {"a": ("1.0.0", {}), "b": "1.0.0"},
        )
        path = write_lockfile(self.tmp, data)
        root_deps, packages_map = load_lockfile(path)
        by_name = {
            item["name"]: item
            for item in sbom_document(
                root_deps, packages_map, with_dependencies=True
            )["components"]
        }
        self.assertEqual(by_name["a"]["dependencies"], [])
        self.assertEqual(by_name["b"]["dependencies"], [])

    def test_no_transitive_expansion_and_no_version_ranges(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"a": "1.0.0"},
                {
                    "a": ("1.0.0", {"b": "^2.1.0"}),
                    "b": ("2.1.0", {"c": "~3.0.0"}),
                    "c": "3.0.0",
                },
            ),
        )
        root_deps, packages_map = load_lockfile(path)
        by_name = {
            item["name"]: item
            for item in sbom_document(
                root_deps, packages_map, with_dependencies=True
            )["components"]
        }
        # 只列直接声明的包名：c 不出现在 a 的数组中，也不带版本范围。
        self.assertEqual(by_name["a"]["dependencies"], ["b"])
        self.assertEqual(by_name["b"]["dependencies"], ["c"])
        self.assertEqual(by_name["c"]["dependencies"], [])

    def test_root_deps_empty_or_missing(self):
        for root_node in ({}, {"dependencies": {}}):
            data = {"lockfileVersion": 3, "packages": {"": root_node}}
            data["packages"]["node_modules/a"] = {
                "version": "1.0.0",
                "dependencies": {"b": "1.0.0"},
            }
            data["packages"]["node_modules/b"] = {"version": "1.0.0"}
            path = write_lockfile(self.tmp, data, "root-empty.json")
            root_deps, packages_map = load_lockfile(path)
            # 完整导出保留已安装组件及其关系。
            doc = sbom_document(
                root_deps, packages_map, with_dependencies=True
            )
            self.assertEqual(
                doc["components"],
                [
                    {**base_component("a", "1.0.0", False),
                     "dependencies": ["b"]},
                    {**base_component("b", "1.0.0", False),
                     "dependencies": []},
                ],
            )
            # 带可达筛选时组件数组为空，文档标记保留。
            reachable_doc = sbom_document(
                root_deps, packages_map, reachable=True,
                with_dependencies=True,
            )
            self.assertEqual(
                reachable_doc,
                {"format": "depinventory-sbom", "formatVersion": 1,
                 "components": []},
            )

    def test_root_only_keeps_markers(self):
        path = write_lockfile(
            self.tmp,
            {"lockfileVersion": 3, "packages": {"": {}}},
            "root-only.json",
        )
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(
            sbom_document(root_deps, packages_map, with_dependencies=True),
            {"format": "depinventory-sbom", "formatVersion": 1,
             "components": []},
        )
        result = run_cli(["sbom", path, "--with-dependencies"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"format": "depinventory-sbom", "formatVersion": 1,
             "components": []},
        )


class SbomDependenciesCompatibility(unittest.TestCase):
    """省略选项时输出逐字段不变；既有调用方式全部保持兼容。"""

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
        baseline = sbom_document(root_deps, packages_map)
        for call in (
            lambda: sbom_document(root_deps, packages_map),
            lambda: sbom_document(root_deps, packages_map, False),
            lambda: sbom_document(root_deps, packages_map, False, False),
            lambda: sbom_document(
                root_deps, packages_map, False, False, False
            ),
            lambda: sbom_document(
                root_deps, packages_map, with_dependencies=False
            ),
            lambda: sbom_document(root_deps, packages_map, with_paths=True),
        ):
            doc = call()
            for item in doc["components"]:
                self.assertNotIn("dependencies", item)
            if doc["components"] and "path" not in doc["components"][0]:
                self.assertEqual(doc, baseline)
        result = run_cli(["sbom", self.path])
        self.assertEqual(result.returncode, 0)
        for item in json.loads(result.stdout)["components"]:
            self.assertNotIn("dependencies", item)

    def test_positional_and_keyword_calls(self):
        root_deps, packages_map = load_lockfile(self.path)
        positional = sbom_document(root_deps, packages_map, True, True, True)
        keyword = sbom_document(
            root_deps, packages_map,
            reachable=True, with_paths=True, with_dependencies=True,
        )
        self.assertEqual(positional, keyword)
        for item in positional["components"]:
            self.assertEqual(
                set(item), BASE_FIELDS | {"dependencies", "path"}
            )
        # with_dependencies 单独的关键字调用与默认 False 一致。
        self.assertEqual(
            sbom_document(root_deps, packages_map),
            sbom_document(root_deps, packages_map, with_dependencies=False),
        )


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
