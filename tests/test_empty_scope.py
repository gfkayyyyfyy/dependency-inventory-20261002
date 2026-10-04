"""空作用域安装路径拒绝与合法作用域对照的回归测试（仅标准库、全程离线）。

npm v3 平铺锁文件中，node_modules/@/pkg 的 @ 与斜杠之间没有任何字符，
作用域为空，属于不支持的结构：load_lockfile 必须抛 InputError，命令行
list / why / parents / ancestors / sbom / diff（任一侧）必须整份失败——
退出码 2、标准错误仅 "INPUT_ERROR\\n"、标准输出为空；条目不被根节点
引用也不能被 --reachable 跳过，why 查询未安装目标同样先报输入错误。

合法对照 scoped-lock.json（node_modules/@scope/pkg，根 dependencies
声明 "@scope/pkg":"*"）保持原有行为：list 退出码 0、标准错误为空、
标准输出为仅含 {"name":"@scope/pkg","version":"1.0.0","direct":true}
的 JSON 数组并以换行结束；完整名称区分大小写，名称与版本原样保留。

不以命令行结果代替函数验收：异常类型用直接调用核对，CLI 单独核对
退出码与输出流。处理前后输入文件字节保持一致。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMPTY_SCOPE_LOCK = os.path.join(REPO_ROOT, "empty-scope-lock.json")
SCOPED_LOCK = os.path.join(REPO_ROOT, "scoped-lock.json")

EMPTY_SCOPE_BYTES = (
    b'{"lockfileVersion":3,"packages":{"":{},'
    b'"node_modules/@/pkg":{"version":"1.0.0"}}}'
)
SCOPED_BYTES = (
    b'{"lockfileVersion":3,"packages":{"":{"dependencies":{"@scope/pkg":"*"}},'
    b'"node_modules/@scope/pkg":{"version":"1.0.0"}}}'
)


def write_bytes(directory, content, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(content)
    return path


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).digest()


class FixtureBytes(unittest.TestCase):
    """两个夹具的磁盘字节与题目给定内容逐字节一致。"""

    def test_empty_scope_fixture_bytes(self):
        with open(EMPTY_SCOPE_LOCK, "rb") as handle:
            self.assertEqual(handle.read(), EMPTY_SCOPE_BYTES)

    def test_scoped_fixture_bytes(self):
        with open(SCOPED_LOCK, "rb") as handle:
            self.assertEqual(handle.read(), SCOPED_BYTES)


class EmptyScopeRejected(unittest.TestCase):
    """空作用域条目在函数级必须抛 InputError（函数级验收）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_empty_scope_fixture_raises_input_error(self):
        with self.assertRaises(InputError):
            load_lockfile(EMPTY_SCOPE_LOCK)

    def test_load_empty_scope_copy_raises_input_error(self):
        path = write_bytes(self.tmp, EMPTY_SCOPE_BYTES)
        with self.assertRaises(InputError):
            load_lockfile(path)

    def test_unreachable_empty_scope_entry_rejects_whole_file(self):
        # 条目不被根节点引用：整份校验在加载期完成，不可达也不能放过。
        content = (
            b'{"lockfileVersion":3,"packages":{"":{},'
            b'"node_modules/alpha":{"version":"1.0.0"},'
            b'"node_modules/@/pkg":{"version":"1.0.0"}}}'
        )
        path = write_bytes(self.tmp, content, "unreachable.json")
        with self.assertRaises(InputError):
            load_lockfile(path)

    def test_empty_scope_with_other_valid_fields_still_rejected(self):
        # 其余字段（version、dependencies）全部合法也不能放行空作用域。
        content = (
            b'{"lockfileVersion":3,"packages":{"":{},'
            b'"node_modules/@/pkg":{"version":"1.0.0","dependencies":{}}}}'
        )
        path = write_bytes(self.tmp, content, "valid-fields.json")
        with self.assertRaises(InputError):
            load_lockfile(path)

    def test_does_not_mutate_input_file(self):
        before = sha256(EMPTY_SCOPE_LOCK)
        for _ in range(3):
            with self.assertRaises(InputError):
                load_lockfile(EMPTY_SCOPE_LOCK)
        self.assertEqual(sha256(EMPTY_SCOPE_LOCK), before)


class EmptyScopeCli(unittest.TestCase):
    """命令行各入口对空作用域条目的退出码与输出流。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
        )

    def assert_cli_input_error(self, argv):
        result = self.run_cli(argv)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"INPUT_ERROR\n")
        self.assertEqual(result.stdout, b"")

    def test_list(self):
        self.assert_cli_input_error(["list", EMPTY_SCOPE_LOCK])

    def test_list_reachable_cannot_skip_unreachable_entry(self):
        # @/pkg 不被根节点引用，--reachable 也不能跳过整份校验。
        self.assert_cli_input_error(["list", EMPTY_SCOPE_LOCK, "--reachable"])

    def test_why_missing_target_is_input_error_not_not_found(self):
        # 查询未安装目标也先报告输入错误，不能返回 NOT_FOUND。
        self.assert_cli_input_error(["why", EMPTY_SCOPE_LOCK, "ghost"])

    def test_parents_ancestors_sbom(self):
        self.assert_cli_input_error(["parents", EMPTY_SCOPE_LOCK, "pkg"])
        self.assert_cli_input_error(["ancestors", EMPTY_SCOPE_LOCK, "pkg"])
        self.assert_cli_input_error(["sbom", EMPTY_SCOPE_LOCK])
        self.assert_cli_input_error(["sbom", EMPTY_SCOPE_LOCK, "--reachable"])

    def test_diff_either_side(self):
        # 空作用域条目出现在 diff 任一侧都整份失败，不输出部分结果。
        self.assert_cli_input_error(["diff", EMPTY_SCOPE_LOCK, SCOPED_LOCK])
        self.assert_cli_input_error(["diff", SCOPED_LOCK, EMPTY_SCOPE_LOCK])
        self.assert_cli_input_error(
            ["diff", EMPTY_SCOPE_LOCK, SCOPED_LOCK, "--reachable"]
        )

    def test_cli_does_not_mutate_fixture(self):
        before = sha256(EMPTY_SCOPE_LOCK)
        self.assert_cli_input_error(["list", EMPTY_SCOPE_LOCK])
        self.assert_cli_input_error(["why", EMPTY_SCOPE_LOCK, "ghost"])
        self.assertEqual(sha256(EMPTY_SCOPE_LOCK), before)


class ValidScopeUnchanged(unittest.TestCase):
    """合法作用域对照：读取、大小写与输出格式保持原有行为。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
        )

    def test_load_scoped_fixture(self):
        root_deps, packages_map = load_lockfile(SCOPED_LOCK)
        self.assertEqual(root_deps, ["@scope/pkg"])
        self.assertEqual(
            packages_map, {"@scope/pkg": {"version": "1.0.0", "deps": []}}
        )

    def test_cli_list_scoped_fixture(self):
        result = self.run_cli(["list", SCOPED_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [{"name": "@scope/pkg", "version": "1.0.0", "direct": True}],
        )
        # 输出为单行 JSON 数组并以换行结束。
        self.assertTrue(result.stdout.endswith(b"\n"))
        self.assertEqual(result.stdout.count(b"\n"), 1)

    def test_scoped_name_is_case_sensitive(self):
        # 完整名称区分大小写：@Scope/pkg 未安装，报 NOT_FOUND 而非命中。
        result = self.run_cli(["why", SCOPED_LOCK, "@Scope/pkg"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"NOT_FOUND\n")
        self.assertEqual(result.stdout, b"")

    def test_name_and_version_preserved_verbatim(self):
        # 作用域与版本字符串原样保留，大小写与字符不规范化。
        content = (
            b'{"lockfileVersion":3,"packages":{"":{"dependencies":'
            b'{"@Scope/Pkg":"*"}},"node_modules/@Scope/Pkg":'
            b'{"version":"1.0.0-beta.1"}}}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_bytes(tmp, content, "mixed-case.json")
            root_deps, packages_map = load_lockfile(path)
            self.assertEqual(root_deps, ["@Scope/Pkg"])
            self.assertEqual(
                packages_map["@Scope/Pkg"]["version"], "1.0.0-beta.1"
            )

    def test_scoped_fixture_not_mutated(self):
        before = sha256(SCOPED_LOCK)
        result = self.run_cli(["list", SCOPED_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(sha256(SCOPED_LOCK), before)


if __name__ == "__main__":
    unittest.main()
