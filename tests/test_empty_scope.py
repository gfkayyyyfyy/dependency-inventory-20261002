"""空作用域安装路径拒绝与合法作用域对照的回归测试（仅标准库、全程离线）。

固定以下边界：
- node_modules/@/pkg 这类 @ 与斜杠之间没有字符的空作用域路径属于不支持
  的结构：load_lockfile 抛 InputError，不返回清单；即使该条目不被根节点
  dependencies 引用，整份输入同样作废，--reachable 不能跳过；
- 命令行对该输入一律退出码 2、标准输出为空、标准错误仅 "INPUT_ERROR\\n"，
  不输出部分结果或堆栈；why 查询未安装目标时同样先报输入错误；diff 任一
  侧含该条目即整体失败，方向无关；
- 合法对照 node_modules/@scope/pkg 保持原有行为：list 退出码 0、标准错误
  为空、标准输出为仅含该条目的 JSON 数组并以换行结束；作用域名按完整名称
  区分大小写，名称与版本原样保留；
- 测试只读取仓库根目录的固定输入文件，调用前后文件字节一致。
"""

import json
import os
import subprocess
import sys
import unittest

from depinventory import InputError, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMPTY_SCOPE_LOCK = os.path.join(REPO_ROOT, "empty-scope-lock.json")
SCOPED_LOCK = os.path.join(REPO_ROOT, "scoped-lock.json")


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


class EmptyScopeRejected(unittest.TestCase):
    """空作用域条目使整份输入失败，与可达性和查询目标无关。"""

    def assert_input_error(self, result):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_load_lockfile_raises_input_error(self):
        with self.assertRaises(InputError):
            load_lockfile(EMPTY_SCOPE_LOCK)

    def test_list_rejects(self):
        self.assert_input_error(run_cli(["list", EMPTY_SCOPE_LOCK]))

    def test_list_reachable_does_not_skip_unreachable_bad_entry(self):
        # 空作用域条目未被根节点引用，--reachable 也不能跳过整份校验。
        self.assert_input_error(run_cli(["list", "--reachable", EMPTY_SCOPE_LOCK]))

    def test_why_missing_target_still_input_error(self):
        # 查询未安装目标时输入错误优先于 NOT_FOUND。
        self.assert_input_error(run_cli(["why", EMPTY_SCOPE_LOCK, "ghost"]))

    def test_other_commands_reject(self):
        for argv in (
            ["sbom", EMPTY_SCOPE_LOCK],
            ["sbom", "--reachable", EMPTY_SCOPE_LOCK],
            ["parents", EMPTY_SCOPE_LOCK, "ghost"],
            ["ancestors", EMPTY_SCOPE_LOCK, "ghost"],
        ):
            with self.subTest(argv=argv):
                self.assert_input_error(run_cli(argv))

    def test_diff_rejects_on_either_side(self):
        for argv in (
            ["diff", EMPTY_SCOPE_LOCK, SCOPED_LOCK],
            ["diff", SCOPED_LOCK, EMPTY_SCOPE_LOCK],
            ["diff", "--reachable", EMPTY_SCOPE_LOCK, SCOPED_LOCK],
        ):
            with self.subTest(argv=argv):
                self.assert_input_error(run_cli(argv))


class ValidScopeControl(unittest.TestCase):
    """合法作用域包保持原有行为：名称区分大小写、原样保留。"""

    def test_load_lockfile_returns_manifest(self):
        root_deps, packages_map = load_lockfile(SCOPED_LOCK)
        self.assertEqual(root_deps, ["@scope/pkg"])
        self.assertEqual(
            packages_map,
            {"@scope/pkg": {"version": "1.0.0", "deps": []}},
        )

    def test_list_output(self):
        result = run_cli(["list", SCOPED_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(
            json.loads(result.stdout),
            [{"name": "@scope/pkg", "version": "1.0.0", "direct": True}],
        )

    def test_scope_name_case_sensitive(self):
        # 作用域名按完整名称区分大小写：@Scope/pkg 与 @scope/pkg 是两个包。
        result = run_cli(["why", SCOPED_LOCK, "@Scope/pkg"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "NOT_FOUND\n")
        self.assertEqual(result.stdout, "")


class InputFilesUnchanged(unittest.TestCase):
    """命令行调用前后，固定输入文件字节一致。"""

    def test_files_byte_identical_after_commands(self):
        before = {path: read_bytes(path) for path in (EMPTY_SCOPE_LOCK, SCOPED_LOCK)}
        run_cli(["list", EMPTY_SCOPE_LOCK])
        run_cli(["list", SCOPED_LOCK])
        run_cli(["diff", EMPTY_SCOPE_LOCK, SCOPED_LOCK])
        for path, content in before.items():
            with self.subTest(path=path):
                self.assertEqual(read_bytes(path), content)


if __name__ == "__main__":
    unittest.main()
