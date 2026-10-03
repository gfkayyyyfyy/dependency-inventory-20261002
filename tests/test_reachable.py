"""list --reachable 既有行为的回归测试（仅标准库、全程离线）。

样例为 UTF-8 的 npm v3 平铺锁文件：
- 根节点 dependencies 声明 alpha 与 @scope/tool，声明值统一为 "*"；
- alpha 与 @scope/tool 都依赖 beta；beta 依赖 alpha 与自身（自环）；
- orphan 与 zeta 互相依赖构成循环，但不被根项目引用；
- beta 版本 2.0.0，其余包 1.0.0。

覆盖：
- load_lockfile 后 reachable_items 的结果与真实命令行
  `python -m depinventory list <path> --reachable` 输出一致，
  顺序为 @scope/tool、alpha、beta，direct 依次为 True、True、False，
  每项仅含 name、version、direct，版本原样保留，根项目不输出，
  共享依赖与循环不产生重复项；省略 --reachable 时另含 orphan、zeta；
- 反转安装条目顺序与依赖声明顺序后，两种清单结果不变；
- 根 dependencies 省略或为空对象时，即使存在合法的已安装循环包，
  筛选结果仍为 []；只有根节点时同样为 []；
- 成功调用退出码 0、标准错误为空、标准输出为单个 JSON 数组且以换行结束；
- 不可达 orphan 的 dependencies 单独改为 null 或指向未安装 ghost 时，
  在筛选前整份拒绝：load_lockfile 抛 InputError，带选项的命令退出码 2、
  标准输出为空、标准错误仅 "INPUT_ERROR\n"；
- 成功与失败调用前后输入文件字节一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, load_lockfile, reachable_items

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED_REACHABLE = [
    {"name": "@scope/tool", "version": "1.0.0", "direct": True},
    {"name": "alpha", "version": "1.0.0", "direct": True},
    {"name": "beta", "version": "2.0.0", "direct": False},
]

EXPECTED_ALL = EXPECTED_REACHABLE + [
    {"name": "orphan", "version": "1.0.0", "direct": False},
    {"name": "zeta", "version": "1.0.0", "direct": False},
]

ITEM_KEYS = {"name", "version", "direct"}


def reverse_mapping(mapping):
    """按相反的键插入顺序重建 dict（依赖声明值保持不变）。"""
    return {key: mapping[key] for key in reversed(list(mapping))}


def sample_data():
    """构造任务描述的样例锁文件（dict 形式，写出时为 UTF-8 JSON）。"""
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


def reversed_entries_data():
    """仅反转 packages 中安装条目（node_modules/*）的顺序。"""
    src = sample_data()
    packages = {}
    items = list(src["packages"].items())
    packages[""] = dict(items[0][1])
    for key, node in reversed(items[1:]):
        packages[key] = {
            "version": node["version"],
            "dependencies": dict(node["dependencies"]),
        }
    return {"lockfileVersion": 3, "packages": packages}


def reversed_dependencies_data():
    """仅反转每个节点 dependencies 声明的键顺序。"""
    src = sample_data()
    packages = {}
    for key, node in src["packages"].items():
        if key == "":
            # 根节点没有 version 字段。
            packages[key] = {"dependencies": reverse_mapping(node["dependencies"])}
        else:
            packages[key] = {
                "version": node["version"],
                "dependencies": reverse_mapping(node["dependencies"]),
            }
    return {"lockfileVersion": 3, "packages": packages}


class ReachableFixture(unittest.TestCase):
    """共享临时目录与锁文件写入/快照工具。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write_lockfile(self, data, name="lock.json"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        return path

    @staticmethod
    def snapshot(path):
        with open(path, "rb") as handle:
            return handle.read()

    @staticmethod
    def run_cli(argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def standard_path(self):
        return self.write_lockfile(sample_data(), "sample.json")


class ReachableApiResult(ReachableFixture):
    """load_lockfile + reachable_items 的直接函数级核对。"""

    def test_reachable_items_matches_expected_relations(self):
        path = self.standard_path()
        before = self.snapshot(path)
        root_deps, packages_map = load_lockfile(path)
        items = reachable_items(root_deps, packages_map)
        after = self.snapshot(path)

        self.assertEqual(items, EXPECTED_REACHABLE)
        self.assertEqual([item["name"] for item in items],
                         ["@scope/tool", "alpha", "beta"])
        self.assertEqual([item["direct"] for item in items], [True, True, False])
        # 每项仅有 name、version、direct；beta 版本原样保留。
        self.assertTrue(all(set(item) == ITEM_KEYS for item in items))
        self.assertEqual(items[2]["version"], "2.0.0")
        # 共享依赖（beta）与循环不产生重复项，根项目不输出。
        names = [item["name"] for item in items]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("", names)
        # 读取不改变输入文件字节。
        self.assertEqual(before, after)

    def test_ordering_independent_of_entry_and_declaration_order(self):
        for label, data in (
            ("reversed-entries", reversed_entries_data()),
            ("reversed-dependencies", reversed_dependencies_data()),
        ):
            with self.subTest(variant=label):
                path = self.write_lockfile(data, label + ".json")
                root_deps, packages_map = load_lockfile(path)
                self.assertEqual(
                    reachable_items(root_deps, packages_map),
                    EXPECTED_REACHABLE,
                )


class ReachableCommandOutput(ReachableFixture):
    """真实命令行 list --reachable 及省略选项时的输出核对。"""

    def test_reachable_command_output(self):
        path = self.standard_path()
        before = self.snapshot(path)
        result = self.run_cli(["list", path, "--reachable"])
        after = self.snapshot(path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 单个 JSON 数组并以换行结束，无尾随的第二个文档。
        self.assertTrue(result.stdout.endswith("\n"))
        parsed = json.loads(result.stdout)
        self.assertIsInstance(parsed, list)
        self.assertEqual(
            result.stdout,
            json.dumps(EXPECTED_REACHABLE, ensure_ascii=False) + "\n",
        )
        self.assertTrue(all(set(item) == ITEM_KEYS for item in parsed))
        self.assertEqual(before, after)

    def test_without_option_includes_disconnected_cycle(self):
        path = self.standard_path()
        result = self.run_cli(["list", path])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        items = json.loads(result.stdout)
        self.assertEqual(items, EXPECTED_ALL)
        # orphan、zeta 存在且 direct 均为 False。
        by_name = {item["name"]: item for item in items}
        self.assertFalse(by_name["orphan"]["direct"])
        self.assertFalse(by_name["zeta"]["direct"])

    def test_reversed_order_variants_keep_both_listings(self):
        # 反转安装条目顺序、反转依赖声明顺序后，可达清单与完整清单各自不变。
        variants = {
            "reversed-entries": reversed_entries_data(),
            "reversed-dependencies": reversed_dependencies_data(),
        }
        for label, data in variants.items():
            path = self.write_lockfile(data, label + ".json")
            with self.subTest(variant=label, option="--reachable"):
                result = self.run_cli(["list", path, "--reachable"])
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), EXPECTED_REACHABLE)
            with self.subTest(variant=label, option="none"):
                result = self.run_cli(["list", path])
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), EXPECTED_ALL)


class EmptyRootSelections(ReachableFixture):
    """根 dependencies 省略/为空/无包条目时，可达结果均为 []。"""

    def disconnected_cycle_lock(self, root_node):
        return {
            "lockfileVersion": 3,
            "packages": {
                "": root_node,
                # 合法的已安装循环包，但与根节点断开。
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

    def assert_reachable_empty(self, data, filename):
        path = self.write_lockfile(data, filename)

        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(reachable_items(root_deps, packages_map), [])
        # 循环包确实已安装，省略选项时仍出现在完整清单中。
        self.assertEqual(
            sorted(packages_map), ["orphan", "zeta"],
        )

        result = self.run_cli(["list", path, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    def test_root_dependencies_omitted_with_installed_cycle(self):
        self.assert_reachable_empty(
            self.disconnected_cycle_lock({}), "root-omitted.json"
        )

    def test_root_dependencies_empty_object_with_installed_cycle(self):
        self.assert_reachable_empty(
            self.disconnected_cycle_lock({"dependencies": {}}),
            "root-empty.json",
        )

    def test_root_only_entry_outputs_empty_array(self):
        path = self.write_lockfile(
            {"lockfileVersion": 3, "packages": {"": {}}},
            "root-only.json",
        )
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(reachable_items(root_deps, packages_map), [])

        result = self.run_cli(["list", path, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")


class InvalidUnreachableEntryRejected(ReachableFixture):
    """不可达包的非法 dependencies 仍须在筛选前整份拒绝。"""

    def invalid_locks(self):
        null_case = sample_data()
        null_case["packages"]["node_modules/orphan"]["dependencies"] = None
        ghost_case = sample_data()
        ghost_case["packages"]["node_modules/orphan"]["dependencies"] = {
            "ghost": "*"
        }
        return [
            ("orphan-null", null_case),
            ("orphan-ghost", ghost_case),
        ]

    def test_load_lockfile_raises_input_error(self):
        for label, data in self.invalid_locks():
            with self.subTest(variant=label):
                path = self.write_lockfile(data, label + ".json")
                before = self.snapshot(path)
                with self.assertRaises(InputError):
                    load_lockfile(path)
                # 失败调用不改变输入文件字节。
                self.assertEqual(before, self.snapshot(path))

    def test_reachable_command_rejects_whole_input(self):
        for label, data in self.invalid_locks():
            with self.subTest(variant=label):
                path = self.write_lockfile(data, label + ".json")
                before = self.snapshot(path)
                result = self.run_cli(["list", path, "--reachable"])
                # 即使 orphan 不可达，也先拒绝整份输入而非输出筛选结果。
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "INPUT_ERROR\n")
                self.assertEqual(before, self.snapshot(path))


if __name__ == "__main__":
    unittest.main()
