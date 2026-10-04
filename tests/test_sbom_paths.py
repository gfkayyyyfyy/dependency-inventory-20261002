"""sbom --with-paths 组件来源路径导出的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖；调用真实的
depinventory.load_lockfile 与 depinventory.sbom_document，不以替身
代替。覆盖：

- 基本验收：demo-lock.json --with-paths 下 alpha 的 path 为
  ["$root","alpha"]、beta 为 ["$root","alpha","beta"]；new-lock.json
  下 beta 为 ["$root","beta"]、根不可达的 gamma 为 []，再加 --reachable
  只保留 beta；只有根节点时 components 仍为 []；
- path 与省略 --from 的 find_path 对每个组件逐一一致：最短边数、等长按
  包名序列 Unicode 码点字典序裁决；作用域包作为单个路径元素，自环、
  循环、共享依赖正常结束且每个组件只出现一次，不可达组件为 []；
- 省略选项时组件字段集合仍是既有六个字段；两参数调用、reachable 的
  位置参数与关键字调用、with_paths 默认 False 全部保持兼容；
- 组件排序与路径选择不受条目及 dependencies 声明顺序影响；
- 成功时退出码 0、stderr 为空、stdout 为带末尾换行的单个 JSON 文档；
- 不放宽校验：不可达条目的缺失版本或悬空依赖、文件不可读、非法 UTF-8、
  损坏 JSON 统一退出码 2、stdout 为空、stderr 仅 "INPUT_ERROR\\n"，
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
    find_path,
    load_lockfile,
    sbom_document,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")
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


class SbomPathsDemoAcceptance(unittest.TestCase):
    """基本验收：demo-lock.json / new-lock.json 的 --with-paths 输出。"""

    def test_demo_paths_api_and_cli(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        doc = sbom_document(root_deps, packages_map, with_paths=True)
        self.assertEqual(doc["format"], "depinventory-sbom")
        self.assertIs(doc["formatVersion"], 1)
        self.assertEqual(
            doc["components"],
            [
                {**base_component("alpha", "1.0.0", True),
                 "path": [ROOT, "alpha"]},
                {**base_component("beta", "2.0.0", False),
                 "path": [ROOT, "alpha", "beta"]},
            ],
        )
        result = run_cli(["sbom", DEMO_LOCK, "--with-paths"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout), doc)

    def test_new_lock_unreachable_gamma_empty_path_and_reachable_combo(self):
        root_deps, packages_map = load_lockfile(NEW_LOCK)
        doc = sbom_document(root_deps, packages_map, with_paths=True)
        self.assertEqual(
            doc["components"],
            [
                {**base_component("beta", "2.1.0", True),
                 "path": [ROOT, "beta"]},
                {**base_component("gamma", "3.0.0", False), "path": []},
            ],
        )
        result = run_cli(["sbom", NEW_LOCK, "--with-paths", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "format": "depinventory-sbom",
                "formatVersion": 1,
                "components": [
                    {**base_component("beta", "2.1.0", True),
                     "path": [ROOT, "beta"]}
                ],
            },
        )

    def test_root_only_still_empty_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lockfile(
                tmp,
                {"lockfileVersion": 3, "packages": {"": {}}},
                "root-only.json",
            )
            root_deps, packages_map = load_lockfile(path)
            self.assertEqual(
                sbom_document(root_deps, packages_map, with_paths=True),
                {"format": "depinventory-sbom", "formatVersion": 1, "components": []},
            )
            result = run_cli(["sbom", path, "--with-paths"])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertEqual(
                json.loads(result.stdout),
                {"format": "depinventory-sbom", "formatVersion": 1, "components": []},
            )


class SbomPathsSemantics(unittest.TestCase):
    """路径与 why 默认查询一致：作用域、自环、循环、共享依赖、裁决。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 根声明 @s/z、a、b。shared 在第二层有三条等长路径，按包名序列
        # 字典序应取 "$root"→"@s/z"→shared（@s/z < a < b）。cyc↔a 构成
        # 循环，shared 自环；orphan 自依赖但根不可达。
        self.data = lock_data(
            {"@s/z": "1.0.0", "a": "1.0.0", "b": "1.0.0"},
            {
                "@s/z": ("1.0.0", {"shared": "1.0.0"}),
                "a": ("1.0.0", {"shared": "1.0.0", "cyc": "1.0.0"}),
                "b": ("1.0.0", {"shared": "1.0.0"}),
                "shared": ("1.0.0", {"shared": "1.0.0", "cyc": "1.0.0"}),
                "cyc": ("1.0.0", {"a": "1.0.0"}),
                "orphan": ("1.0.0", {"orphan": "1.0.0"}),
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "sample.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_every_path_matches_default_why_query(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(root_deps, packages_map, with_paths=True)
        for item in doc["components"]:
            self.assertEqual(
                item["path"],
                find_path(root_deps, packages_map, item["name"]),
                item["name"],
            )

    def test_concrete_paths_and_fields(self):
        root_deps, packages_map = load_lockfile(self.path)
        by_name = {
            item["name"]: item
            for item in sbom_document(root_deps, packages_map, with_paths=True)["components"]
        }
        self.assertEqual(by_name["@s/z"]["path"], [ROOT, "@s/z"])
        # 等长路径字典序裁决："@s/z"（U+0040 开头）先于 "a"、"b"。
        self.assertEqual(by_name["shared"]["path"], [ROOT, "@s/z", "shared"])
        self.assertEqual(by_name["a"]["path"], [ROOT, "a"])
        self.assertEqual(by_name["cyc"]["path"], [ROOT, "a", "cyc"])
        self.assertEqual(by_name["orphan"]["path"], [])
        # 作用域包是单个路径元素；direct 含义不变。
        self.assertTrue(by_name["@s/z"]["direct"])
        self.assertFalse(by_name["shared"]["direct"])
        self.assertFalse(by_name["orphan"]["direct"])
        for item in by_name.values():
            self.assertEqual(set(item), BASE_FIELDS | {"path"})
            self.assertEqual(item["license"], "unknown")
            self.assertEqual(item["securityStatus"], "unknown")

    def test_components_unique_sorted_and_unreachable_kept(self):
        root_deps, packages_map = load_lockfile(self.path)
        names = [
            item["name"]
            for item in sbom_document(root_deps, packages_map, with_paths=True)["components"]
        ]
        self.assertEqual(names, ["@s/z", "a", "b", "cyc", "orphan", "shared"])
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("", names)

    def test_reachable_true_combo_drops_unreachable(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(root_deps, packages_map, True, True)
        names = [item["name"] for item in doc["components"]]
        self.assertEqual(names, ["@s/z", "a", "b", "cyc", "shared"])
        self.assertTrue(all(item["path"] for item in doc["components"]))

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
        doc_a = sbom_document(*load_lockfile(self.path), with_paths=True)
        doc_b = sbom_document(*load_lockfile(reversed_path), with_paths=True)
        self.assertEqual(doc_a, doc_b)
        cli_a = run_cli(["sbom", self.path, "--with-paths"]).stdout
        cli_b = run_cli(["sbom", reversed_path, "--with-paths"]).stdout
        self.assertEqual(cli_a, cli_b)


class SbomPathsCompatibility(unittest.TestCase):
    """不带选项时输出不变；两参数与 reachable 位置/关键字调用兼容。"""

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
        ):
            doc = call()
            for item in doc["components"]:
                self.assertEqual(set(item), BASE_FIELDS)
                self.assertNotIn("path", item)
        # CLI 省略选项同样无 path。
        result = run_cli(["sbom", self.path])
        self.assertEqual(result.returncode, 0)
        for item in json.loads(result.stdout)["components"]:
            self.assertNotIn("path", item)

    def test_reachable_positional_and_keyword_calls_accept_with_paths(self):
        root_deps, packages_map = load_lockfile(self.path)
        positional = sbom_document(root_deps, packages_map, True, True)
        keyword = sbom_document(
            root_deps, packages_map, reachable=True, with_paths=True
        )
        self.assertEqual(positional, keyword)
        self.assertEqual(
            [item["name"] for item in positional["components"]],
            ["alpha", "beta"],
        )
        # reachable 单独的旧式位置/关键字调用语义不变。
        self.assertEqual(
            [item["name"] for item in sbom_document(root_deps, packages_map, True)["components"]],
            ["alpha", "beta"],
        )
        self.assertEqual(
            sbom_document(root_deps, packages_map, True),
            sbom_document(root_deps, packages_map, reachable=True),
        )


class SbomPathsValidationAndIO(unittest.TestCase):
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
        # 悬空依赖（目标包不存在），且其声明者根不可达。
        dangling = write_lockfile(self.tmp, base, "dangling.json")
        with self.assertRaises(InputError):
            load_lockfile(dangling)
        self.assert_input_error_cli(dangling, ["--with-paths"])
        self.assert_input_error_cli(dangling, ["--with-paths", "--reachable"])

        missing = lock_data({"a": "1.0.0"}, {"a": "1.0.0", "orphan": "2.0.0"})
        del missing["packages"]["node_modules/orphan"]["version"]
        missing_path = write_lockfile(self.tmp, missing, "missing-version.json")
        with self.assertRaises(InputError):
            load_lockfile(missing_path)
        self.assert_input_error_cli(missing_path, ["--with-paths"])

    def test_unreadable_file_bad_utf8_and_broken_json(self):
        self.assert_input_error_cli(os.path.join(self.tmp, "missing.json"), ["--with-paths"])
        self.assert_input_error_cli(BAD_UTF8, ["--with-paths"])
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.assert_input_error_cli(broken, ["--with-paths"])

    def test_input_file_unchanged(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"a": "1.0.0"},
                {"a": ("1.0.0", {"b": "1.0.0"}), "b": "1.0.0", "o": "9.0.0"},
            ),
        )
        before = read_bytes(path)
        run_cli(["sbom", path, "--with-paths"])
        run_cli(["sbom", path, "--with-paths", "--reachable"])
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
        sbom_document(root_deps, packages_map, with_paths=True)
        sbom_document(root_deps, packages_map, reachable=True, with_paths=True)
        self.assertEqual(root_deps, deps_before)
        self.assertEqual(packages_map, map_before)


if __name__ == "__main__":
    unittest.main()
