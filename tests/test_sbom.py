"""sbom 子命令与 sbom_document 的回归测试：简化 SBOM 导出的内容与错误处理。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
全程离线、仅用标准库，不修改 demo-lock.json，不安装或执行锁文件中的依赖。
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

DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")

COMPONENT_FIELDS = {"name", "version", "ecosystem", "direct", "license", "securityStatus"}


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


class SbomAcceptance(unittest.TestCase):
    """验收场景：python -m depinventory sbom demo-lock.json 的完整输出。"""

    EXPECTED_COMPONENTS = [
        {
            "name": "alpha",
            "version": "1.0.0",
            "ecosystem": "npm",
            "direct": True,
            "license": "unknown",
            "securityStatus": "unknown",
        },
        {
            "name": "beta",
            "version": "2.0.0",
            "ecosystem": "npm",
            "direct": False,
            "license": "unknown",
            "securityStatus": "unknown",
        },
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assertSbomDocument(self, document, expected_components):
        """顶层仅含三个格式字段，组件仅含约定的六个字段。"""
        self.assertEqual(set(document), {"format", "formatVersion", "components"})
        self.assertEqual(document["format"], "depinventory-sbom")
        # formatVersion 是整数 1，且不是 bool。
        self.assertIs(type(document["formatVersion"]), int)
        self.assertEqual(document["formatVersion"], 1)
        for component in document["components"]:
            self.assertEqual(set(component), COMPONENT_FIELDS)
        self.assertEqual(document["components"], expected_components)

    def test_acceptance_scenario_cli(self):
        result = self.run_cli(["sbom", DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 标准输出是带末尾换行的单个 JSON 文档。
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout[:-1].endswith("\n"))
        document = json.loads(result.stdout)
        self.assertIsInstance(document, dict)
        self.assertSbomDocument(document, self.EXPECTED_COMPONENTS)

    def test_acceptance_scenario_api(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        document = sbom_document(root_deps, packages_map)
        self.assertSbomDocument(document, self.EXPECTED_COMPONENTS)

    def test_cli_output_matches_api_result(self):
        root_deps, packages_map = load_lockfile(DEMO_LOCK)
        expected = sbom_document(root_deps, packages_map)
        result = self.run_cli(["sbom", DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_input_file_bytes_unchanged(self):
        path = write_lockfile(
            self.tmp, lock_data({"a": "1.0.0"}, {"a": "1.0.0", "b": "2.0.0"})
        )
        with open(path, "rb") as handle:
            before = handle.read()
        result = self.run_cli(["sbom", path])
        self.assertEqual(result.returncode, 0)
        with open(path, "rb") as handle:
            after = handle.read()
        self.assertEqual(before, after)


class SbomSemantics(unittest.TestCase):
    """作用域包、不可达包、循环依赖、排序、direct 语义与声明顺序无关性。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def sbom_cli(self, data):
        path = write_lockfile(self.tmp, data)
        result = self.run_cli(["sbom", path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_scoped_unreachable_and_cycle_exported_once_sorted(self):
        # 合法样例：作用域包、根节点不可达的包、a 与 b 之间的循环依赖。
        # 全部已安装包各导出一次，根项目不作为组件，按 Unicode 码点升序
        # （@ 排在小写字母之前）。
        data = lock_data(
            {"a": "1.0.0"},
            {
                "a": ("1.0.0", {"b": "2.0.0"}),
                "b": ("2.0.0", {"a": "1.0.0"}),
                "@scope/pkg": "3.1.0",
                "orphan": "0.0.1",
            },
        )
        document = self.sbom_cli(data)
        names = [c["name"] for c in document["components"]]
        self.assertEqual(names, ["@scope/pkg", "a", "b", "orphan"])
        self.assertEqual(len(set(names)), len(names))
        self.assertNotIn("", names)
        by_name = {c["name"]: c for c in document["components"]}
        # direct 仅取决于根 dependencies，与可达性和循环依赖无关。
        self.assertTrue(by_name["a"]["direct"])
        self.assertFalse(by_name["b"]["direct"])
        self.assertFalse(by_name["@scope/pkg"]["direct"])
        self.assertFalse(by_name["orphan"]["direct"])
        # 版本字符串原样保留。
        self.assertEqual(by_name["@scope/pkg"]["version"], "3.1.0")
        self.assertEqual(by_name["orphan"]["version"], "0.0.1")

    def test_version_strings_kept_verbatim(self):
        data = lock_data(
            {},
            {"a": "1.0.0-beta.1+build.5", "b": " 2.0.0 "},
        )
        document = self.sbom_cli(data)
        by_name = {c["name"]: c for c in document["components"]}
        self.assertEqual(by_name["a"]["version"], "1.0.0-beta.1+build.5")
        self.assertEqual(by_name["b"]["version"], " 2.0.0 ")

    def test_declaration_order_does_not_change_result(self):
        # 交换包条目与根依赖的声明顺序，完整结果相同。
        forward = lock_data(
            {"a": "1.0.0", "b": "2.0.0"},
            {
                "a": ("1.0.0", {"b": "2.0.0"}),
                "b": "2.0.0",
                "@scope/pkg": "3.0.0",
            },
        )
        swapped = lock_data(
            {"b": "2.0.0", "a": "1.0.0"},
            {
                "@scope/pkg": "3.0.0",
                "b": "2.0.0",
                "a": ("1.0.0", {"b": "2.0.0"}),
            },
        )
        self.assertNotEqual(
            list(forward["packages"].keys()), list(swapped["packages"].keys())
        )
        self.assertEqual(self.sbom_cli(forward), self.sbom_cli(swapped))

    def test_root_only_lockfile_exports_empty_components(self):
        document = self.sbom_cli(lock_data({}, {}))
        self.assertEqual(document["components"], [])
        self.assertEqual(document["format"], "depinventory-sbom")
        self.assertEqual(document["formatVersion"], 1)

    def test_api_does_not_mutate_inputs(self):
        path = write_lockfile(
            self.tmp,
            lock_data(
                {"a": "1.0.0"},
                {"a": ("1.0.0", {"b": "2.0.0"}), "b": "2.0.0"},
            ),
        )
        root_deps, packages_map = load_lockfile(path)
        deps_before = copy.deepcopy(root_deps)
        map_before = copy.deepcopy(packages_map)
        sbom_document(root_deps, packages_map)
        self.assertEqual(root_deps, deps_before)
        self.assertEqual(packages_map, map_before)


class SbomInputErrors(unittest.TestCase):
    """缺少包版本的锁文件：API 抛 InputError，CLI 退出码 2 且不输出部分结果。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        data = lock_data({"a": "1.0.0"}, {"a": "1.0.0"})
        del data["packages"]["node_modules/a"]["version"]
        self.bad = write_lockfile(self.tmp, data, "missing-version.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_api_raises_input_error(self):
        with self.assertRaises(InputError):
            load_lockfile(self.bad)

    def test_cli_input_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "depinventory", "sbom", self.bad],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")


if __name__ == "__main__":
    unittest.main()
