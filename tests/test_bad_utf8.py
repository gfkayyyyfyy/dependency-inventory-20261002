"""非法 UTF-8 输入的读取边界回归测试（仅标准库、全程离线）。

公开入口 load_lockfile 对存在且可读、但无法按 UTF-8 解码的文件必须
统一抛 InputError（不泄漏 UnicodeDecodeError），不返回根依赖或包映射；
命令行 list/why 对同一文件返回退出码 2，标准错误仅 "INPUT_ERROR\n"，
标准输出为空，即使 why 查询的包名不存在也先报输入错误。

合法 UTF-8（含中文元数据）与 demo-lock.json 的既有结果保持不变；
处理前后输入文件字节不变。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_LOCK = os.path.join(REPO_ROOT, "demo-lock.json")
BAD_UTF8_LOCK = os.path.join(REPO_ROOT, "bad-utf8.json")

# 合法的最小锁文件 JSON，用于拼接各类损坏字节。
VALID_JSON = (
    '{"lockfileVersion": 3, "packages": {"": {}, '
    '"node_modules/alpha": {"version": "1.0.0"}}}'
).encode("utf-8")


def write_bytes(directory, content, name):
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(content)
    return path


class BadUtf8RejectedByApi(unittest.TestCase):
    """load_lockfile 对非法 UTF-8 统一抛 InputError，且不改动输入文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_bad_utf8_raises_input_error(self, content, name):
        path = write_bytes(self.tmp, content, name)
        before = open(path, "rb").read()
        with self.assertRaises(InputError):
            load_lockfile(path)
        # 异常类型必须恰为公开的 InputError，不得泄漏 UnicodeDecodeError。
        try:
            load_lockfile(path)
        except InputError as exc:
            self.assertNotIsInstance(exc, UnicodeDecodeError)
        else:
            self.fail("load_lockfile did not reject invalid UTF-8")
        # 读取失败不得改动输入文件的任何字节。
        after = open(path, "rb").read()
        self.assertEqual(before, after)

    def test_fixture_file_is_single_ff_byte(self):
        with open(BAD_UTF8_LOCK, "rb") as handle:
            self.assertEqual(handle.read(), b"\xff")

    def test_fixture_file_raises_input_error(self):
        with self.assertRaises(InputError):
            load_lockfile(BAD_UTF8_LOCK)

    def test_isolated_ff_byte(self):
        self.assert_bad_utf8_raises_input_error(b"\xff", "ff.json")

    def test_truncated_multibyte_sequence(self):
        # 中文字符的三字节序列缺少后续字节。
        self.assert_bad_utf8_raises_input_error(
            VALID_JSON[:-1] + b"\xe4\xb8", "truncated.json"
        )

    def test_invalid_byte_appended_after_valid_json(self):
        self.assert_bad_utf8_raises_input_error(
            VALID_JSON + b"\xff", "trailing.json"
        )

    def test_corruption_in_field_unused_by_queries(self):
        # 损坏字节位于查询用不到的元数据字段中，整份文件仍不得产生结果。
        data = (
            b'{"lockfileVersion": 3, "packages": {"": {}, '
            b'"node_modules/alpha": {"version": "1.0.0", '
            b'"description": "\xff"}}}'
        )
        self.assert_bad_utf8_raises_input_error(data, "unused-field.json")

    def test_valid_utf8_chinese_metadata_still_loads(self):
        data = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "根项目"},
                "node_modules/alpha": {
                    "version": "1.0.0",
                    "description": "中文元数据：甲乙丙丁",
                },
            },
        }
        path = os.path.join(self.tmp, "chinese.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, {"alpha": {"version": "1.0.0", "deps": []}})


class BadUtf8RejectedByCli(unittest.TestCase):
    """list/why 对非法 UTF-8 返回退出码 2，stderr 仅 INPUT_ERROR，stdout 为空。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assert_input_error(self, argv):
        result = self.run_cli(argv)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")

    def test_list_fixture_file(self):
        self.assert_input_error(["list", BAD_UTF8_LOCK])

    def test_why_fixture_file_existing_name(self):
        self.assert_input_error(["why", BAD_UTF8_LOCK, "alpha"])

    def test_why_fixture_file_missing_name_reports_input_error_first(self):
        # 包名不存在也先报输入错误，不得返回 NOT_FOUND。
        self.assert_input_error(["why", BAD_UTF8_LOCK, "nonexistent"])

    def test_input_file_bytes_unchanged_after_cli(self):
        before = open(BAD_UTF8_LOCK, "rb").read()
        self.run_cli(["list", BAD_UTF8_LOCK])
        self.run_cli(["why", BAD_UTF8_LOCK, "alpha"])
        after = open(BAD_UTF8_LOCK, "rb").read()
        self.assertEqual(before, after)
        self.assertEqual(after, b"\xff")


class ExistingResultsUnchanged(unittest.TestCase):
    """demo-lock.json 的两包清单与 beta 来源路径保持原样。"""

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_demo_list_unchanged(self):
        result = self.run_cli(["list", DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )

    def test_demo_why_beta_path_unchanged(self):
        result = self.run_cli(["why", DEMO_LOCK, "beta"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {"name": "beta", "path": ["$root", "alpha", "beta"]},
        )


if __name__ == "__main__":
    unittest.main()
