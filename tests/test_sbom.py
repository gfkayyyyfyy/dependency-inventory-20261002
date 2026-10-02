"""sbom 子命令（简化 SBOM 导出）的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改 demo-lock.json，不安装或执行锁文件中的依赖；调用真实的
depinventory.load_lockfile 与 depinventory.sbom_document，不以替身
代替。覆盖：

- 基本验收：demo-lock.json 导出 depinventory-sbom / formatVersion
  整数 1，alpha 直接、beta 传递，组件仅含六个固定字段；
- 小型合法样例：作用域包、根节点不可达包、两包循环依赖同时出现，
  全部已安装包各导出一次、根项目不作为组件、按 Unicode 码点排序、
  版本字符串原样保留、direct 仅取决于根 dependencies；
- 条目声明顺序不影响结果；只有根节点时 components 为 [] 而格式标记不变；
- CLI 成功：退出码 0、标准错误空、标准输出为带末尾换行的单个 JSON 文档，
  解析后与函数结果一致；
- 缺少包版本的锁文件：读取接口抛 InputError，sbom 退出码 2、标准输出空、
  标准错误仅 "INPUT_ERROR\n"，不返回部分组件；
- 导出前后输入文件字节不变，函数调用不修改根依赖列表与包映射。

list / why / why --from / diff 的 README 公开行为只做最小守卫，完整覆盖
仍由各自既有测试文件负责。
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


class SbomDemoAcceptance(unittest.TestCase):
    """基本验收：python -m depinventory sbom demo-lock.json。"""

    def setUp(self):
        self.root_deps, self.packages_map = load_lockfile(DEMO_LOCK)

    def test_document_markers_and_components(self):
        doc = sbom_document(self.root_deps, self.packages_map)
        self.assertEqual(doc["format"], "depinventory-sbom")
        # formatVersion 必须是整数 1，而不是 1.0、True 或字符串。
        self.assertIs(doc["formatVersion"], 1)
        self.assertIsInstance(doc["formatVersion"], int)
        self.assertNotIsInstance(doc["formatVersion"], bool)
        self.assertEqual(list(doc), ["format", "formatVersion", "components"])
        self.assertEqual(
            doc["components"],
            [
                component("alpha", "1.0.0", True),
                component("beta", "2.0.0", False),
            ],
        )

    def test_component_field_set_is_exact(self):
        doc = sbom_document(self.root_deps, self.packages_map)
        for item in doc["components"]:
            self.assertEqual(
                set(item),
                {"name", "version", "ecosystem", "direct", "license", "securityStatus"},
            )
            self.assertEqual(item["ecosystem"], "npm")
            self.assertEqual(item["license"], "unknown")
            self.assertEqual(item["securityStatus"], "unknown")
            self.assertIsInstance(item["direct"], bool)

    def test_cli_single_json_document_with_trailing_newline(self):
        result = run_cli(["sbom", DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        # 单个 JSON 文档：去掉唯一的末尾换行后整体解析，文档内不得再夹带换行
        # 或第二个 JSON 文档。
        body = result.stdout
        self.assertTrue(body.endswith("\n"))
        self.assertEqual(body.count("\n"), 1)
        parsed = json.loads(body)
        self.assertEqual(parsed, sbom_document(self.root_deps, self.packages_map))


class SbomStructuredSample(unittest.TestCase):
    """作用域包、根不可达包、循环依赖同处一份锁文件时的导出语义。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 根直接声明：@scope/a 与 loop-x（direct 仅取决于此）。
        # loop-x <-> loop-y 构成两包循环依赖；orphan 已安装但任何节点都
        # 不引用它（根节点不可达）；版本字符串使用非标准形态核对原样保留。
        self.data = lock_data(
            {"@scope/a": "1.0.0", "loop-x": "1.0.0"},
            {
                "@scope/a": ("1.0.0", {"beta": "2.0.0"}),
                "beta": "2.0.0",
                "loop-x": ("=1.2.3-rc.1+build.7", {"loop-y": "0.0.1"}),
                "loop-y": ("0.0.1", {"loop-x": "=1.2.3-rc.1+build.7"}),
                "orphan": ("9.9.9", {"beta": "2.0.0"}),
            },
        )

    def tearDown(self):
        self._tmp.cleanup()

    def expected_components(self):
        return [
            # @ (U+0040) 排在字母之前；其余按名称 Unicode 码点升序。
            component("@scope/a", "1.0.0", True),
            component("beta", "2.0.0", False),
            component("loop-x", "=1.2.3-rc.1+build.7", True),
            component("loop-y", "0.0.1", False),
            component("orphan", "9.9.9", False),
        ]

    def test_api_export_via_real_load_and_document(self):
        path = write_lockfile(self.tmp, self.data, "sample.json")
        root_deps, packages_map = load_lockfile(path)
        doc = sbom_document(root_deps, packages_map)
        self.assertEqual(doc["format"], "depinventory-sbom")
        self.assertEqual(doc["formatVersion"], 1)
        self.assertEqual(doc["components"], self.expected_components())

        names = [item["name"] for item in doc["components"]]
        # 全部已安装包各导出一次，根项目（""）不作为组件。
        self.assertEqual(names, ["@scope/a", "beta", "loop-x", "loop-y", "orphan"])
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("", names)
        # 与读取结果中的已安装包集合完全一致。
        self.assertEqual(set(names), set(packages_map))
        # Unicode 码点升序的独立核对。
        self.assertEqual(names, sorted(names, key=lambda s: [ord(c) for c in s]))
        self.assertEqual(names, sorted(names))

    def test_direct_depends_only_on_root_dependencies(self):
        path = write_lockfile(self.tmp, self.data, "sample.json")
        root_deps, packages_map = load_lockfile(path)
        by_name = {c["name"]: c for c in sbom_document(root_deps, packages_map)["components"]}
        # orphan 依赖 beta、loop-y 被 loop-x 引用，均不改变 direct；
        # 根声明的两个包即使处于循环中仍为 true。
        self.assertTrue(by_name["@scope/a"]["direct"])
        self.assertTrue(by_name["loop-x"]["direct"])
        self.assertFalse(by_name["beta"]["direct"])
        self.assertFalse(by_name["loop-y"]["direct"])
        self.assertFalse(by_name["orphan"]["direct"])

    def test_version_strings_preserved_verbatim(self):
        path = write_lockfile(self.tmp, self.data, "sample.json")
        root_deps, packages_map = load_lockfile(path)
        by_name = {c["name"]: c for c in sbom_document(root_deps, packages_map)["components"]}
        self.assertEqual(by_name["loop-x"]["version"], "=1.2.3-rc.1+build.7")
        for name, item in by_name.items():
            self.assertEqual(item["version"], packages_map[name]["version"])

    def test_cli_matches_function_result(self):
        path = write_lockfile(self.tmp, self.data, "sample.json")
        root_deps, packages_map = load_lockfile(path)
        result = run_cli(["sbom", path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout), sbom_document(root_deps, packages_map))


class SbomOrderIndependence(unittest.TestCase):
    """交换 packages 条目声明顺序后，完整导出结果相同。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_swapped_entry_order_identical_document(self):
        data = lock_data(
            {"zeta": "1.0.0", "alpha": "1.0.0"},
            {
                "zeta": ("1.0.0", {"mid": "1.0.0"}),
                "alpha": ("1.0.0", {"mid": "1.0.0"}),
                "mid": "1.0.0",
            },
        )
        path_a = write_lockfile(self.tmp, data, "a.json")

        # 以相反的条目顺序重写同一份逻辑锁文件（根条目除外，其键固定为 ""）。
        reversed_data = copy.deepcopy(data)
        package_keys = [k for k in reversed_data["packages"] if k != ""]
        reordered = {"": reversed_data["packages"][""]}
        for key in reversed(package_keys):
            reordered[key] = reversed_data["packages"][key]
        reversed_data["packages"] = reordered
        path_b = write_lockfile(self.tmp, reversed_data, "b.json")

        doc_a = sbom_document(*load_lockfile(path_a))
        doc_b = sbom_document(*load_lockfile(path_b))
        self.assertEqual(doc_a, doc_b)
        self.assertEqual(
            [c["name"] for c in doc_a["components"]],
            ["alpha", "mid", "zeta"],
        )

        # CLI 文本输出同样逐字节一致。
        out_a = run_cli(["sbom", path_a]).stdout
        out_b = run_cli(["sbom", path_b]).stdout
        self.assertEqual(out_a, out_b)


class SbomRootOnly(unittest.TestCase):
    """只有根节点时 components 为 []，格式标记不变。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(
            self.tmp, {"lockfileVersion": 3, "packages": {"": {}}}, "root-only.json"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_api_empty_components_markers_kept(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(root_deps, packages_map)
        self.assertEqual(doc, {"format": "depinventory-sbom", "formatVersion": 1, "components": []})

    def test_cli_empty_components_markers_kept(self):
        result = run_cli(["sbom", self.path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout[-1], "\n")
        self.assertEqual(
            json.loads(result.stdout),
            {"format": "depinventory-sbom", "formatVersion": 1, "components": []},
        )


class SbomInputError(unittest.TestCase):
    """缺少包版本的锁文件：接口抛 InputError，CLI 退出码 2 且无部分组件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # alpha 条目缺少 version：合法 JSON、引用关系完整，仅版本字段缺失。
        self.data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "1.0.0", "beta": "2.0.0"}},
                "node_modules/alpha": {"dependencies": {"beta": "2.0.0"}},
                "node_modules/beta": {"version": "2.0.0"},
            },
        }
        self.path = write_lockfile(self.tmp, self.data, "missing-version.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_lockfile_raises_input_error(self):
        with self.assertRaises(InputError):
            load_lockfile(self.path)

    def test_cli_exit_2_stderr_marker_stdout_empty(self):
        result = run_cli(["sbom", self.path])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_no_partial_components_on_failure(self):
        # 失败发生在读取阶段：拿不到任何映射，sbom_document 不会被执行，
        # 标准输出中不得出现组件片段。
        result = run_cli(["sbom", self.path])
        self.assertNotIn("components", result.stdout)
        self.assertNotIn("alpha", result.stdout)
        self.assertNotIn("beta", result.stdout)

    def test_file_bytes_identical_after_failed_calls(self):
        with open(self.path, "rb") as handle:
            before = handle.read()
        with self.assertRaises(InputError):
            load_lockfile(self.path)
        failed = run_cli(["sbom", self.path])
        self.assertEqual(failed.returncode, 2)
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), before)


class SbomInputsUnchanged(unittest.TestCase):
    """新增临时样例在导出前后文件字节不变；函数不修改入参结构。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = lock_data(
            {"@scope/a": "1.0.0"},
            {
                "@scope/a": ("1.0.0", {"loop-y": "0.0.1"}),
                "loop-x": ("1.0.0", {"loop-y": "0.0.1"}),
                "loop-y": ("0.0.1", {"loop-x": "1.0.0"}),
                "orphan": "9.9.9",
            },
        )
        self.path = write_lockfile(self.tmp, self.data, "sample.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_file_bytes_identical_before_and_after(self):
        with open(self.path, "rb") as handle:
            before = handle.read()
        for _ in range(2):
            result = run_cli(["sbom", self.path])
            self.assertEqual(result.returncode, 0)
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_arguments_not_mutated_by_sbom_document(self):
        root_deps, packages_map = load_lockfile(self.path)
        deps_before = copy.deepcopy(root_deps)
        map_before = copy.deepcopy(packages_map)
        doc = sbom_document(root_deps, packages_map)
        # 调用前后根依赖与包映射（含版本与 deps 列表）保持不变。
        self.assertEqual(root_deps, deps_before)
        self.assertEqual(packages_map, map_before)
        # 组件是独立结构：改写组件不回写包映射。
        doc["components"][0]["version"] = "MUTATED"
        doc["components"][0]["direct"] = not doc["components"][0]["direct"]
        self.assertEqual(packages_map, map_before)
        self.assertEqual(root_deps, deps_before)
        # 再导出一次仍得到原值。
        again = sbom_document(root_deps, packages_map)
        self.assertEqual(again["components"][0]["version"], map_before[again["components"][0]["name"]]["version"])


class ExistingCommandsUnchanged(unittest.TestCase):
    """README 已公开的 list / why / why --from / diff 行为守卫。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_demo_list_why_and_diff_still_work(self):
        self.assertEqual(run_cli(["list", DEMO_LOCK]).returncode, 0)
        why = run_cli(["why", DEMO_LOCK, "beta"])
        self.assertEqual(why.returncode, 0)
        self.assertEqual(
            json.loads(why.stdout), {"name": "beta", "path": ["$root", "alpha", "beta"]}
        )
        why_from = run_cli(["why", DEMO_LOCK, "beta", "--from", "alpha"])
        self.assertEqual(why_from.returncode, 0)
        self.assertEqual(
            json.loads(why_from.stdout), {"name": "beta", "path": ["alpha", "beta"]}
        )
        diff = run_cli(["diff", DEMO_LOCK, os.path.join(REPO_ROOT, "new-lock.json")])
        self.assertEqual(diff.returncode, 0)
        self.assertEqual(
            json.loads(diff.stdout),
            [
                {"name": "alpha", "change": "removed", "before": "1.0.0", "after": None},
                {"name": "beta", "change": "changed", "before": "2.0.0", "after": "2.1.0"},
                {"name": "gamma", "change": "added", "before": None, "after": "3.0.0"},
            ],
        )

    def test_lockfile_support_scope_unreachable_and_cycle_still_loads(self):
        # 当前锁文件支持范围：作用域路径、悬空（根不可达）包、循环依赖
        # 均可读取，sbom 与 why 行为与 list 语义一致。
        data = lock_data(
            {"@scope/a": "1.0.0"},
            {
                "@scope/a": ("1.0.0", {"cx": "1.0.0"}),
                "cx": ("1.0.0", {"cy": "1.0.0"}),
                "cy": ("1.0.0", {"cx": "1.0.0"}),
                "orphan": "2.0.0",
            },
        )
        path = write_lockfile(self.tmp, data, "support.json")
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, ["@scope/a"])
        self.assertEqual(set(packages_map), {"@scope/a", "cx", "cy", "orphan"})

        listed = run_cli(["list", path])
        self.assertEqual(listed.returncode, 0)
        self.assertEqual(
            [item["name"] for item in json.loads(listed.stdout)],
            ["@scope/a", "cx", "cy", "orphan"],
        )
        orphan_why = run_cli(["why", path, "orphan"])
        self.assertEqual(
            json.loads(orphan_why.stdout), {"name": "orphan", "path": []}
        )
        # 循环依赖不阻止最短路径查询。
        cy_why = run_cli(["why", path, "cy"])
        self.assertEqual(
            json.loads(cy_why.stdout), {"name": "cy", "path": ["$root", "@scope/a", "cx", "cy"]}
        )


if __name__ == "__main__":
    unittest.main()
