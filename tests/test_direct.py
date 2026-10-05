"""list --direct 根直接声明依赖筛选的回归测试（仅标准库、全程离线）。

样例为 UTF-8 的 npm v3 平铺锁文件，依赖声明值统一为 "*"：
- 根节点 dependencies 声明 alpha 与 @scope/tool，两包都依赖 beta；
- beta 版本 2.0.0，依赖 alpha 与自身；其余包版本 1.0.0；
- orphan 与 zeta 互相依赖，但不被根项目引用。

固定行为：
- load_lockfile 后 direct_items 的结果与
  ``python -m depinventory list <样例> --direct`` 的真实输出一致：
  包名依次 @scope/tool、alpha，direct 均为 true；每项仅有 name、version、
  direct；版本取自安装条目的原始字符串而非根声明中的版本范围；beta 等
  传递依赖与未被引用的 orphan、zeta 不进入结果；根项目不输出；
- 同一包同时被其他包声明时只出现一次；
- 结果按完整包名 Unicode 码点升序、区分大小写排序，作用域包作为完整
  名称保留，反转安装条目与声明顺序不改变结果；
- 根 dependencies 省略或为空对象、文件只有根节点时结果为 []；
- --direct 与 --reachable 组合不改变直接依赖结果，与 --unreachable 组合
  为 []，与选项书写顺序无关；--reachable 与 --unreachable 同时出现即使
  也带 --direct，仍在读取文件前按输入错误拒绝；
- 不可达 orphan 的 dependencies 单独改成 null，或改成指向未安装 ghost 的
  声明，均在筛选前拒绝整份输入；
- 实际输出含无法编码为 UTF-8 的孤立代理码点时按输入错误处理；该字符只
  存在于被 --direct 排除的版本（beta、orphan）中时不影响成功；
- 省略 --direct 时 list 既有行为不变，其他命令不受影响；
- 成功与失败调用前后输入文件字节保持一致。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, direct_items, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SURROGATE_LOCK = os.path.join(REPO_ROOT, "surrogate-lock.json")

EXPECTED_DIRECT = [
    {"name": "@scope/tool", "version": "1.0.0", "direct": True},
    {"name": "alpha", "version": "1.0.0", "direct": True},
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


def write_text(directory, text, name="lock.json"):
    # 模板中的 \\ud83f 等是写给文件的 ASCII JSON 转义，文件字节合法 UTF-8。
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    return path


def run_cli(argv):
    return subprocess.run(
        [sys.executable, "-m", "depinventory", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
    )


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def assert_input_error(test, result):
    test.assertEqual(result.returncode, 2)
    test.assertEqual(result.stdout, b"")
    test.assertEqual(result.stderr, b"INPUT_ERROR\n")
    test.assertNotIn(b"Traceback", result.stderr)


class DirectSample(unittest.TestCase):
    """样例关系下 direct_items 与 list --direct 的精确结果。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, sample_data())

    def tearDown(self):
        self._tmp.cleanup()

    def test_direct_items_matches_sample_relations(self):
        root_deps, packages_map = load_lockfile(self.path)
        items = direct_items(root_deps, packages_map)
        self.assertEqual(items, EXPECTED_DIRECT)
        # 顺序、版本与键集合逐项核对预期值。
        self.assertEqual([item["name"] for item in items], ["@scope/tool", "alpha"])
        self.assertEqual([item["version"] for item in items], ["1.0.0", "1.0.0"])
        self.assertTrue(all(item["direct"] is True for item in items))
        for item in items:
            self.assertEqual(set(item.keys()), {"name", "version", "direct"})
        # beta（传递依赖）、orphan/zeta（未被引用）不出现，根项目不输出。
        self.assertEqual(
            {item["name"] for item in items}, {"@scope/tool", "alpha"}
        )

    def test_cli_direct_output_matches_api_result(self):
        root_deps, packages_map = load_lockfile(self.path)
        api_items = direct_items(root_deps, packages_map)

        before = read_bytes(self.path)
        result = run_cli(["list", self.path, "--direct"])
        after = read_bytes(self.path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            result.stdout,
            (json.dumps(EXPECTED_DIRECT, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), api_items)
        # 单个 JSON 文档且恰一个末尾换行；@scope/tool 原样保留不转义。
        self.assertEqual(result.stdout.count(b"\n"), 1)
        self.assertIn("@scope/tool".encode("utf-8"), result.stdout)
        # 成功调用不得改动输入文件字节。
        self.assertEqual(after, before)

    def test_version_taken_from_installed_entry_not_root_range(self):
        # 根声明值是版本范围文本，安装条目版本是另一具体字符串：
        # 输出必须采用安装条目的原始字符串。
        data = sample_data()
        data["packages"][""]["dependencies"] = {
            "alpha": "^9.9.9",
            "@scope/tool": ">=2.0.0 || <1.0.0",
        }
        path = write_lockfile(self.tmp, data, "ranges.json")
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(
            direct_items(root_deps, packages_map), EXPECTED_DIRECT
        )
        result = run_cli(["list", path, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), EXPECTED_DIRECT)

    def test_shared_reference_appears_once(self):
        # beta 在自身 dependencies 中也声明 alpha：alpha 同时被根与其他包
        # 引用，结果中仍只出现一次。
        root_deps, packages_map = load_lockfile(self.path)
        items = direct_items(root_deps, packages_map)
        self.assertEqual(
            [item["name"] for item in items if item["name"] == "alpha"], ["alpha"]
        )


class DirectSortingAndOrder(unittest.TestCase):
    """码点排序、大小写与安装/声明顺序无关。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_unicode_codepoint_case_sensitive_order(self):
        # 码点：'@'=64 < 'A'=65 < 'a'=97；作用域包作为完整名称参与排序。
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"alpha": "*", "Alpha": "*", "@z/pkg": "*"}},
                "node_modules/alpha": {"version": "1.0.0"},
                "node_modules/Alpha": {"version": "2.0.0"},
                "node_modules/@z/pkg": {"version": "3.0.0"},
            },
        }
        path = write_lockfile(self.tmp, data, "case.json")
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(
            [item["name"] for item in direct_items(root_deps, packages_map)],
            ["@z/pkg", "Alpha", "alpha"],
        )
        result = run_cli(["list", path, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            [item["name"] for item in json.loads(result.stdout.decode("utf-8"))],
            ["@z/pkg", "Alpha", "alpha"],
        )

    def test_reversed_entry_and_declaration_order(self):
        path = write_lockfile(self.tmp, reversed_order(sample_data()), "reversed.json")
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(direct_items(root_deps, packages_map), EXPECTED_DIRECT)
        result = run_cli(["list", path, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")), EXPECTED_DIRECT
        )


class DirectWithNoRootEdges(unittest.TestCase):
    """根 dependencies 省略/为空或只有根节点时，直接结果为 []。"""

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

    def assert_empty_direct(self, path):
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(direct_items(root_deps, packages_map), [])
        result = run_cli(["list", path, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, b"[]\n")

    def test_root_dependencies_omitted_with_installed_cycle(self):
        path = write_lockfile(self.tmp, self.cycle_data({}), "omitted.json")
        self.assert_empty_direct(path)

    def test_root_dependencies_empty_object_with_installed_cycle(self):
        path = write_lockfile(
            self.tmp, self.cycle_data({"dependencies": {}}), "empty.json"
        )
        self.assert_empty_direct(path)

    def test_root_entry_only(self):
        path = write_lockfile(
            self.tmp,
            {"lockfileVersion": 3, "packages": {"": {}}},
            "root-only.json",
        )
        self.assert_empty_direct(path)


class DirectCombinedWithReachability(unittest.TestCase):
    """--direct 与 --reachable/--unreachable 取交集。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, sample_data())

    def tearDown(self):
        self._tmp.cleanup()

    def test_direct_with_reachable_unchanged(self):
        # 根直接声明的包本身就是可达遍历的播种节点，交集不变。
        for argv in (
            ["list", self.path, "--direct", "--reachable"],
            ["list", self.path, "--reachable", "--direct"],
        ):
            result = run_cli(argv)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(
                json.loads(result.stdout.decode("utf-8")), EXPECTED_DIRECT
            )

    def test_direct_with_unreachable_is_empty(self):
        # 根声明的包不可能不可达，交集恒为 []。
        for argv in (
            ["list", self.path, "--direct", "--unreachable"],
            ["list", self.path, "--unreachable", "--direct"],
        ):
            result = run_cli(argv)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(result.stdout, b"[]\n")

    def test_reachable_and_unreachable_conflict_rejected_with_direct(self):
        # 即使同时带 --direct，互斥仍在读取文件前拒绝：路径不存在时
        # 结果与文件无关。
        missing = os.path.join(self.tmp, "missing.json")
        for target in (self.path, missing):
            for argv in (
                ["list", target, "--direct", "--reachable", "--unreachable"],
                ["list", target, "--reachable", "--unreachable", "--direct"],
            ):
                assert_input_error(self, run_cli(argv))


class InvalidEntryRejectedBeforeDirectFilter(unittest.TestCase):
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
            direct_items(root_deps, packages_map)

        result = run_cli(["list", path, "--direct"])
        assert_input_error(self, result)

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

    def test_missing_file_is_input_error(self):
        missing = os.path.join(self.tmp, "missing.json")
        assert_input_error(self, run_cli(["list", missing, "--direct"]))


class DirectSurrogateOutputBoundary(unittest.TestCase):
    """孤立代理码点只按实际输出文本判定成败。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_surrogate_in_excluded_beta_version_does_not_fail(self):
        # 仓库夹具 surrogate-lock.json：根只声明 alpha，beta 的版本含孤立
        # 高代理；--direct 只输出 alpha，被排除的 beta 版本不影响成功。
        before = read_bytes(SURROGATE_LOCK)
        result = run_cli(["list", SURROGATE_LOCK, "--direct"])
        after = read_bytes(SURROGATE_LOCK)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [{"name": "alpha", "version": "1.0.0", "direct": True}],
        )
        result = run_cli(["list", SURROGATE_LOCK, "--direct", "--reachable"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [{"name": "alpha", "version": "1.0.0", "direct": True}],
        )
        self.assertEqual(after, before)

    DIRECT_SURROGATE_LOCK = (
        '{"lockfileVersion": 3, "packages": {'
        '"": {"dependencies": {"alpha": "1.0.0"}},'
        '"node_modules/alpha": {"version": "1.0-\\ud83f"},'
        '"node_modules/beta": {"version": "2.0.0"}'
        "}}"
    )

    def test_surrogate_in_included_direct_version_fails(self):
        # alpha 是根直接声明且其版本含孤立代理：实际输出无法编码，失败。
        path = write_text(self.tmp, self.DIRECT_SURROGATE_LOCK, "direct-bad.json")
        before = read_bytes(path)
        result = run_cli(["list", path, "--direct"])
        assert_input_error(self, result)
        # 与 --reachable 组合仍包含 alpha，同样失败。
        assert_input_error(self, run_cli(["list", path, "--direct", "--reachable"]))
        # 与 --unreachable 组合交集为空，没有任何问题文本进入输出：成功。
        empty = run_cli(["list", path, "--direct", "--unreachable"])
        self.assertEqual(empty.returncode, 0)
        self.assertEqual(empty.stderr, b"")
        self.assertEqual(empty.stdout, b"[]\n")
        self.assertEqual(read_bytes(path), before)

    EXCLUDED_ORPHAN_SURROGATE_LOCK = (
        '{"lockfileVersion": 3, "packages": {'
        '"": {"dependencies": {"alpha": "1.0.0"}},'
        '"node_modules/alpha": {"version": "1.0.0",'
        ' "dependencies": {"beta": "2.0.0"}},'
        '"node_modules/beta": {"version": "2.0.0"},'
        '"node_modules/orphan": {"version": "9.0-\\ud83f"}'
        "}}"
    )

    def test_surrogate_in_unreferenced_orphan_version_does_not_fail(self):
        # 未被引用的 orphan 既不被根声明也不可达：--direct 输出干净。
        path = write_text(self.tmp, self.EXCLUDED_ORPHAN_SURROGATE_LOCK, "orphan.json")
        result = run_cli(["list", path, "--direct"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [{"name": "alpha", "version": "1.0.0", "direct": True}],
        )


class DefaultListBehaviorUnchanged(unittest.TestCase):
    """省略 --direct 时 list 三种既有形态的结果不受新选项影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_lockfile(self.tmp, sample_data())

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_reachable_unreachable_outputs_unchanged(self):
        full = run_cli(["list", self.path])
        self.assertEqual(full.returncode, 0)
        self.assertEqual(
            [item["name"] for item in json.loads(full.stdout.decode("utf-8"))],
            ["@scope/tool", "alpha", "beta", "orphan", "zeta"],
        )
        reachable = run_cli(["list", self.path, "--reachable"])
        self.assertEqual(
            [item["name"] for item in json.loads(reachable.stdout.decode("utf-8"))],
            ["@scope/tool", "alpha", "beta"],
        )
        unreachable = run_cli(["list", self.path, "--unreachable"])
        self.assertEqual(
            [item["name"] for item in json.loads(unreachable.stdout.decode("utf-8"))],
            ["orphan", "zeta"],
        )


if __name__ == "__main__":
    unittest.main()
