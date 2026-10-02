"""非法 UTF-8 锁文件读取边界的回归测试（仅标准库、全程离线）。

覆盖三种损坏形态：
- 孤立的 0xFF 字节（仓库夹具 bad-utf8.json，完整字节内容就是 0xFF）；
- 缺少后续字节的多字节序列；
- 有效 JSON 末尾追加非法字节，以及坏字节位于查询用不到的元数据字段中。

公开函数 depinventory.load_lockfile 必须统一抛 depinventory.InputError，
不得逃逸 UnicodeDecodeError，也不得返回任何根依赖或包映射；命令行
list / why 必须为退出码 2、标准错误仅 "INPUT_ERROR\\n"、标准输出为空，
即使 why 查询的包名不存在也先报告输入错误而非 NOT_FOUND。

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
BAD_UTF8 = os.path.join(REPO_ROOT, "bad-utf8.json")


def write_bytes(directory, content, name="bad.bin"):
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(content)
    return path


def valid_lock_bytes():
    return json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "示例项目", "dependencies": {"alpha": "1.0.0"}},
                "node_modules/alpha": {
                    "version": "1.0.0",
                    "description": "中文元数据",
                    "dependencies": {"beta": "2.0.0"},
                },
                "node_modules/beta": {
                    "version": "2.0.0",
                    "description": "贝塔",
                },
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


class LoadLockfileBadUtf8(unittest.TestCase):
    """公开读取入口对不可解码内容必须抛 InputError（函数级验收）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_input_error_for_decode(self, path):
        with self.assertRaises(InputError) as ctx:
            load_lockfile(path)
        # 必须是解码失败导致的 InputError，而不是别的环节碰巧失败；
        # UnicodeDecodeError 本身不得逃逸到调用方。
        self.assertIsInstance(ctx.exception.__cause__, UnicodeDecodeError)
        self.assertNotIsInstance(ctx.exception, UnicodeDecodeError)

    def test_lone_ff_byte_fixture_raises_input_error(self):
        # 夹具完整字节内容为单个 0xFF。
        with open(BAD_UTF8, "rb") as handle:
            self.assertEqual(handle.read(), b"\xff")
        self.assert_input_error_for_decode(BAD_UTF8)

    def test_truncated_multibyte_sequence_raises_input_error(self):
        # 0xE4 起始一个三字节序列，后续字节缺失。
        path = write_bytes(self.tmp, b'{"lockfileVersion": 3, "packages": {"": {}}}\xe4')
        self.assert_input_error_for_decode(path)

    def test_illegal_byte_appended_after_valid_json_raises_input_error(self):
        # 前缀本身是合法 JSON 与合法锁文件结构，末尾追加孤立 0xFF。
        path = write_bytes(self.tmp, valid_lock_bytes() + b"\xff", "tail.bin")
        self.assert_input_error_for_decode(path)

    def test_bad_byte_in_unused_metadata_field_rejects_whole_file(self):
        # 坏字节落在当前查询不会使用的 description 元数据中：
        # 读取阶段整份文件即作废，不能只解析有用字段后放行。
        content = valid_lock_bytes()
        marker = "中文元数据".encode("utf-8")
        index = content.index(marker)
        # 截掉该多字节字符的最后一个续字节，构成残缺序列。
        corrupted = content[: index + len(marker) - 1] + content[index + len(marker):]
        path = write_bytes(self.tmp, corrupted, "meta-corrupt.bin")
        self.assert_input_error_for_decode(path)

    def test_does_not_mutate_input_file(self):
        before = hashlib.sha256(open(BAD_UTF8, "rb").read()).digest()
        for _ in range(3):
            with self.assertRaises(InputError):
                load_lockfile(BAD_UTF8)
        after = hashlib.sha256(open(BAD_UTF8, "rb").read()).digest()
        self.assertEqual(before, after)
        self.assertEqual(open(BAD_UTF8, "rb").read(), b"\xff")


class CliBadUtf8(unittest.TestCase):
    """命令行对不可解码文件的退出码与输出流（不以本类替代函数验收）。"""

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

    def test_list_bad_utf8(self):
        self.assert_cli_input_error(["list", BAD_UTF8])

    def test_why_bad_utf8_existing_name(self):
        self.assert_cli_input_error(["why", BAD_UTF8, "alpha"])

    def test_why_bad_utf8_missing_name_is_input_error_not_not_found(self):
        # 查询包名不存在也要先报告输入错误，不能返回 NOT_FOUND。
        result = self.run_cli(["why", BAD_UTF8, "ghost"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"INPUT_ERROR\n")
        self.assertEqual(result.stdout, b"")

    def test_cli_does_not_mutate_fixture(self):
        before = open(BAD_UTF8, "rb").read()
        self.assert_cli_input_error(["list", BAD_UTF8])
        self.assert_cli_input_error(["why", BAD_UTF8, "ghost"])
        self.assertEqual(open(BAD_UTF8, "rb").read(), before)
        self.assertEqual(before, b"\xff")


class ValidUtf8StillReads(unittest.TestCase):
    """正常 UTF-8（含中文元数据）读取与既有结构不受本次修复影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_chinese_metadata_loads(self):
        path = write_bytes(self.tmp, valid_lock_bytes(), "cjk.json")
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, ["alpha"])
        self.assertEqual(packages_map["alpha"]["version"], "1.0.0")
        self.assertEqual(packages_map["alpha"]["deps"], ["beta"])
        self.assertEqual(packages_map["beta"]["deps"], [])

    def test_chinese_metadata_listed_via_cli(self):
        path = write_bytes(self.tmp, valid_lock_bytes(), "cjk.json")
        result = subprocess.run(
            [sys.executable, "-m", "depinventory", "list", path],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout.decode("utf-8")),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )
        # 成功输出本身按 UTF-8 解码，中文内容原样保留在输入侧。
        self.assertIn("中文元数据".encode("utf-8"), open(path, "rb").read())


if __name__ == "__main__":
    unittest.main()
