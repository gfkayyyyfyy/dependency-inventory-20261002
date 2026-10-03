"""list --reachable 根可达筛选既有行为的回归测试（仅标准库、全程离线）。

样例为 UTF-8 的 npm v3 平铺锁文件，依赖声明值统一为 "*"：
- 根节点 dependencies 声明 alpha 与 @scope/tool，两包都依赖 beta；
- beta 版本 2.0.0，依赖 alpha 与自身；其余包版本 1.0.0；
- orphan 与 zeta 互相依赖，但不被根项目引用。

固定的既有行为：
- load_lockfile 后 reachable_items 的结果与
  ``python -m depinventory list <样例> --reachable`` 的真实输出一致：
  包名依次 @scope/tool、alpha、beta，direct 依次 true、true、false；
  每项仅有 name、version、direct，版本原样保留，根项目不输出；
  共享依赖（beta）与循环（beta 自环、beta↔alpha）不产生重复项；
- 省略 --reachable 时完整清单还包含 orphan、zeta，direct 均为 false；
- 反转安装条目顺序与 dependencies 声明顺序，两种清单各自保持原结果；
- 根 dependencies 省略或为空对象时，即使已安装条目间存在合法循环，
  可达结果仍为 []；只有根节点时同样为 []；
- 成功调用退出码 0、stderr 为空、stdout 为单个 JSON 数组并以换行结束；
- 不可达 orphan 的 dependencies 单独改成 null，或单独改成指向未安装
  ghost 的声明，均在筛选前拒绝整份输入：load_lockfile 抛 InputError，
  带 --reachable 的命令退出码 2、stdout 为空、stderr 仅 "INPUT_ERROR\\n"；
- 成功与失败调用前后输入文件字节保持一致。
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

EXPECTED_FULL = EXPECTED_REACHABLE + [
    {"name": "orphan", "version": "1.0.0", "direct": False},
    {"name": "zeta", "version": "1.0.0", "direct": False},
]


def sample_data():
    """构造任务描述的样例锁文件（插入顺序即下方书写顺序）。"""
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


def reversed_order(data):
    """反转 packages 安装条目顺序及每个 dependencies 的声明顺序。"""
    packages = {}
    for key in reversed(list(data["packages"].keys())):
        node = {}
        for field in reversed(list(data["packages"][key].keys())):
            value = data["packages"][key][field]
            if field == "dependencies":
                value = {dep: value[dep] for dep in reversed(list(value.keys()))}
            node[field] = value
        packages[key] = node
    return {"lockfileVersion": 3, "packages": packages}


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


class ReachableSample(unittest.TestCase):
    """样例关系下 reachable_items 与 list --reachable 的精确结果。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, sample_data())

    def tearDown(self):
        self._tmp.cleanup()

    def test_reachable_items_matches_sample_relations(self):
        root_deps, packages_map = load_lockfile(self.path)
        items = reachable_items(root_deps, packages_map)
        self.assertEqual(items, EXPECTED_REACHABLE)
        # 顺序、direct、版本与键集合逐项核对预期值。
        self.assertEqual([item["name"] for item in items],
                         ["@scope/tool", "alpha", "beta"])
        self.assertEqual([item["version"] for item in items],
                         ["1.0.0", "1.0.0", "2.0.0"])
        self.assertEqual([item["direct"] for item in items],
                         [True, True, False])
        for item in items:
            self.assertEqual(set(item.keys()), {"name", "version", "direct"})

    def test_cli_reachable_output_matches_api_result(self):
        root_deps, packages_map = load_lockfile(self.path)
        api_items = reachable_items(root_deps, packages_map)

        before = read_bytes(self.path)
        result = run_cli(["list", self.path, "--reachable"])
        after = read_bytes(self.path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 单个 JSON 数组，且以换行结束；@scope/tool 原样保留不转义。
        self.assertEqual(
            result.stdout,
            json.dumps(EXPECTED_REACHABLE, ensure_ascii=False) + "\n",
        )
        self.assertEqual(json.loads(result.stdout), api_items)
        # 成功调用不得改动输入文件字节。
        self.assertEqual(after, before)

    def test_full_list_without_option_includes_unreachable_cycle(self):
        result = run_cli(["list", self.path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        items = json.loads(result.stdout)
        self.assertEqual(items, EXPECTED_FULL)
        # orphan、zeta 构成与根断开的循环：完整清单保留，direct 均为 false。
        self.assertEqual(
            [item["direct"] for item in items if item["name"] in ("orphan", "zeta")],
            [False, False],
        )
        self.assertTrue(result.stdout.endswith("\n"))


class ReachableOrderIndependence(unittest.TestCase):
    """安装条目与依赖声明顺序反转不改变两种清单。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_reversed_entry_and_declaration_order(self):
        path = write_lockfile(self.tmp, reversed_order(sample_data()), "reversed.json")

        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(reachable_items(root_deps, packages_map), EXPECTED_REACHABLE)

        reachable = run_cli(["list", path, "--reachable"])
        self.assertEqual(reachable.returncode, 0)
        self.assertEqual(reachable.stderr, "")
        self.assertEqual(json.loads(reachable.stdout), EXPECTED_REACHABLE)

        full = run_cli(["list", path])
        self.assertEqual(full.returncode, 0)
        self.assertEqual(full.stderr, "")
        self.assertEqual(json.loads(full.stdout), EXPECTED_FULL)


class ReachableWithNoRootEdges(unittest.TestCase):
    """根 dependencies 省略/为空或只有根节点时，可达结果为 []。"""

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

    def assert_empty_reachable(self, path):
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(reachable_items(root_deps, packages_map), [])
        result = run_cli(["list", path, "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    def test_root_dependencies_omitted_with_installed_cycle(self):
        path = write_lockfile(self.tmp, self.cycle_data({}), "omitted.json")
        self.assert_empty_reachable(path)

    def test_root_dependencies_empty_object_with_installed_cycle(self):
        path = write_lockfile(
            self.tmp, self.cycle_data({"dependencies": {}}), "empty.json"
        )
        self.assert_empty_reachable(path)

    def test_root_entry_only(self):
        path = write_lockfile(
            self.tmp,
            {"lockfileVersion": 3, "packages": {"": {}}},
            "root-only.json",
        )
        self.assert_empty_reachable(path)


class InvalidUnreachableEntryRejected(unittest.TestCase):
    """不可达 orphan 的非法 dependencies 仍在筛选前拒绝整份输入。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_rejected_before_filtering(self, path):
        before = read_bytes(path)

        with self.assertRaises(InputError):
            root_deps, packages_map = load_lockfile(path)
            reachable_items(root_deps, packages_map)

        result = run_cli(["list", path, "--reachable"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")

        # 失败调用同样不得改动输入文件字节。
        self.assertEqual(read_bytes(path), before)

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
