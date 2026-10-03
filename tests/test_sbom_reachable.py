"""sbom --reachable 根可达筛选导出的回归测试（仅标准库、全程离线）。

样例均为临时目录内独立创建的合法 package-lock.json v3 平铺结构，
不修改仓库自带样例，不安装或执行锁文件中的依赖；调用真实的
depinventory.load_lockfile 与 depinventory.sbom_document，不以替身
代替。覆盖：

- 基本验收：python -m depinventory sbom new-lock.json --reachable 只含
  beta@2.1.0（direct 为 true）；省略选项时仍含 beta 与 gamma@3.0.0
  （direct 为 false）；
- 可达性语义与 list --reachable 一致：作用域包、共享传递依赖、可达自环
  与循环保留且不重复，与根断开的包和循环整体排除，direct 仍仅取决于根
  dependencies，名称与版本原样保留，按 Unicode 码点升序；
- 根 dependencies 省略、为空对象或只有根节点时 components 为 []，
  format / formatVersion 标记保留；
- 筛选前仍校验整份输入：不可达条目的缺失版本、非法 dependencies、悬空
  依赖均使 load_lockfile 抛 InputError，命令退出码 2、stdout 为空、
  stderr 仅 "INPUT_ERROR\\n"，不输出部分组件；
- 省略选项的两参数调用 sbom_document(root_deps, packages_map) 结果与
  既有完整导出完全一致；
- 成功调用退出码 0、stderr 为空、stdout 为带末尾换行的单个 JSON 文档；
  成功与失败调用前后输入文件字节不变，函数不修改入参。
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
NEW_LOCK = os.path.join(REPO_ROOT, "new-lock.json")


def write_lockfile(directory, data, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
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


def component(name, version, direct):
    return {
        "name": name,
        "version": version,
        "ecosystem": "npm",
        "direct": direct,
        "license": "unknown",
        "securityStatus": "unknown",
    }


def sample_data():
    """构造含作用域包、共享依赖、可达循环与断开循环的样例锁文件。"""
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


EXPECTED_REACHABLE_COMPONENTS = [
    component("@scope/tool", "1.0.0", True),
    component("alpha", "1.0.0", True),
    component("beta", "2.0.0", False),
]

EXPECTED_FULL_COMPONENTS = EXPECTED_REACHABLE_COMPONENTS + [
    component("orphan", "1.0.0", False),
    component("zeta", "1.0.0", False),
]


class SbomReachableNewLockAcceptance(unittest.TestCase):
    """基本验收：new-lock.json 带与不带 --reachable 的导出结果。"""

    def test_reachable_export_contains_only_beta(self):
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

    def test_full_export_still_contains_beta_and_gamma(self):
        result = run_cli(["sbom", NEW_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout)["components"],
            [component("beta", "2.1.0", True), component("gamma", "3.0.0", False)],
        )

    def test_api_two_argument_call_unchanged(self):
        # 现有两参数调用仍导出全部已安装包，结果与省略选项的 CLI 一致。
        root_deps, packages_map = load_lockfile(NEW_LOCK)
        doc = sbom_document(root_deps, packages_map)
        self.assertEqual(
            doc["components"],
            [component("beta", "2.1.0", True), component("gamma", "3.0.0", False)],
        )
        cli = run_cli(["sbom", NEW_LOCK])
        self.assertEqual(json.loads(cli.stdout), doc)


class SbomReachableSample(unittest.TestCase):
    """可达筛选语义与 list --reachable 一致，仅缩小 components 范围。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, sample_data())

    def tearDown(self):
        self._tmp.cleanup()

    def test_api_reachable_true_filters_components(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(root_deps, packages_map, reachable=True)
        self.assertEqual(doc["format"], "depinventory-sbom")
        self.assertIs(doc["formatVersion"], 1)
        self.assertEqual(list(doc), ["format", "formatVersion", "components"])
        self.assertEqual(doc["components"], EXPECTED_REACHABLE_COMPONENTS)
        # 共享依赖 beta、自环 beta→beta、循环 beta↔alpha 均只输出一次。
        names = [item["name"] for item in doc["components"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names, sorted(names))

    def test_api_default_and_explicit_false_match_full_export(self):
        root_deps, packages_map = load_lockfile(self.path)
        self.assertEqual(
            sbom_document(root_deps, packages_map),
            sbom_document(root_deps, packages_map, reachable=False),
        )
        self.assertEqual(
            sbom_document(root_deps, packages_map)["components"],
            EXPECTED_FULL_COMPONENTS,
        )

    def test_cli_reachable_matches_api_result(self):
        root_deps, packages_map = load_lockfile(self.path)
        before = read_bytes(self.path)
        result = run_cli(["sbom", self.path, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            json.dumps(
                {
                    "format": "depinventory-sbom",
                    "formatVersion": 1,
                    "components": EXPECTED_REACHABLE_COMPONENTS,
                },
                ensure_ascii=False,
            )
            + "\n",
        )
        self.assertEqual(
            json.loads(result.stdout),
            sbom_document(root_deps, packages_map, reachable=True),
        )
        self.assertEqual(read_bytes(self.path), before)

    def test_reachable_components_keep_exact_field_set(self):
        root_deps, packages_map = load_lockfile(self.path)
        doc = sbom_document(root_deps, packages_map, reachable=True)
        for item in doc["components"]:
            self.assertEqual(
                set(item),
                {"name", "version", "ecosystem", "direct", "license", "securityStatus"},
            )
            self.assertEqual(item["ecosystem"], "npm")
            self.assertEqual(item["license"], "unknown")
            self.assertEqual(item["securityStatus"], "unknown")

    def test_entry_and_declaration_order_do_not_change_result(self):
        data = sample_data()
        packages = {}
        for key in reversed(list(data["packages"].keys())):
            node = {}
            for field in reversed(list(data["packages"][key].keys())):
                value = data["packages"][key][field]
                if field == "dependencies":
                    value = {dep: value[dep] for dep in reversed(list(value.keys()))}
                node[field] = value
            packages[key] = node
        reversed_path = write_lockfile(
            self.tmp,
            {"lockfileVersion": 3, "packages": packages},
            "reversed.json",
        )
        root_deps, packages_map = load_lockfile(reversed_path)
        self.assertEqual(
            sbom_document(root_deps, packages_map, reachable=True)["components"],
            EXPECTED_REACHABLE_COMPONENTS,
        )
        result = run_cli(["sbom", reversed_path, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout)["components"], EXPECTED_REACHABLE_COMPONENTS
        )

    def test_arguments_not_mutated_by_reachable_export(self):
        root_deps, packages_map = load_lockfile(self.path)
        deps_before = copy.deepcopy(root_deps)
        map_before = copy.deepcopy(packages_map)
        sbom_document(root_deps, packages_map, reachable=True)
        self.assertEqual(root_deps, deps_before)
        self.assertEqual(packages_map, map_before)


class SbomReachableEmptyResult(unittest.TestCase):
    """根 dependencies 省略/为空或只有根节点时 components 为 []，标记保留。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def cycle_data(self, root_node):
        # orphan↔zeta 是合法的已安装循环，但与根节点完全断开。
        return {
            "lockfileVersion": 3,
            "packages": {
                "": root_node,
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

    def assert_empty_components(self, path):
        expected = {"format": "depinventory-sbom", "formatVersion": 1, "components": []}
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(sbom_document(root_deps, packages_map, reachable=True), expected)
        result = run_cli(["sbom", path, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertTrue(result.stdout.endswith("\n"))

    def test_root_dependencies_omitted_with_installed_cycle(self):
        self.assert_empty_components(
            write_lockfile(self.tmp, self.cycle_data({}), "omitted.json")
        )

    def test_root_dependencies_empty_object_with_installed_cycle(self):
        self.assert_empty_components(
            write_lockfile(
                self.tmp, self.cycle_data({"dependencies": {}}), "empty.json"
            )
        )

    def test_root_entry_only(self):
        self.assert_empty_components(
            write_lockfile(
                self.tmp,
                {"lockfileVersion": 3, "packages": {"": {}}},
                "root-only.json",
            )
        )


class SbomReachableInvalidUnreachableEntry(unittest.TestCase):
    """不可达条目的结构问题仍在筛选前拒绝整份输入。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_rejected_before_filtering(self, path):
        before = read_bytes(path)

        with self.assertRaises(InputError):
            root_deps, packages_map = load_lockfile(path)
            sbom_document(root_deps, packages_map, reachable=True)

        result = run_cli(["sbom", path, "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertNotIn("Traceback", result.stderr)

        self.assertEqual(read_bytes(path), before)

    def test_missing_version_on_unreachable_orphan_rejected(self):
        data = sample_data()
        del data["packages"]["node_modules/orphan"]["version"]
        path = write_lockfile(self.tmp, data, "orphan-no-version.json")
        self.assert_rejected_before_filtering(path)

    def test_null_dependencies_on_unreachable_orphan_rejected(self):
        data = sample_data()
        data["packages"]["node_modules/orphan"]["dependencies"] = None
        path = write_lockfile(self.tmp, data, "orphan-null.json")
        self.assert_rejected_before_filtering(path)

    def test_dependency_on_uninstalled_ghost_rejected(self):
        data = sample_data()
        data["packages"]["node_modules/orphan"]["dependencies"] = {"ghost": "*"}
        path = write_lockfile(self.tmp, data, "orphan-ghost.json")
        self.assert_rejected_before_filtering(path)


if __name__ == "__main__":
    unittest.main()
