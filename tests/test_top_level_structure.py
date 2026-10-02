"""锁文件顶层结构接收/拒绝边界的回归测试（仅标准库、全程离线）。

固定 README“支持范围”与“退出码”章节已公开的行为：
- 合法最小输入 {"lockfileVersion":3,"packages":{"":{}}} 可被
  depinventory.load_lockfile 读取为（空根依赖，空包映射），list 输出
  仅 "[]\\n"，stderr 为空，退出码 0；
- 每次只改变一个顶层结构条件（顶层为数组/null、lockfileVersion 缺失或
  类型/值不合规、packages 缺失/为 null/数组/空对象、空串根条目为
  null/数组），读取入口必须抛 depinventory.InputError，不返回清单；
- 同一批反例经 list 调用必须为退出码 2、stderr 仅 "INPUT_ERROR\\n"、
  stdout 为空且无堆栈；why 查询不存在的包时同样先报输入错误；
  diff 把非法文件放在旧、新任一位置都整体失败并遵守同一输出约定。

所有输入均为临时目录内独立写入的 UTF-8 文件，不接触仓库样例；
代表性有效与无效输入在调用前后字节必须一致。测试只用公开入口
depinventory.load_lockfile 与 python -m depinventory 命令行，
不依赖私有函数、异常消息正文或内部处理顺序。
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

# 合法最小输入：整数值 3、packages 为含空串根对象的对象；根条目为空对象。
MINIMAL = '{"lockfileVersion":3,"packages":{"":{}}}'

# 每份反例相对最小输入只改变一个结构条件，其余保持合法。
INVALID_CASES = [
    ("top_level_array", "[]"),
    ("top_level_null", "null"),
    ("version_missing", '{"packages":{"":{}}}'),
    ("version_string", '{"lockfileVersion":"3","packages":{"":{}}}'),
    # 必须保留小数写法：该用例验证的是 JSON 浮点数被拒绝，而非整数 3。
    ("version_float", '{"lockfileVersion":3.0,"packages":{"":{}}}'),
    ("version_bool", '{"lockfileVersion":true,"packages":{"":{}}}'),
    ("version_null", '{"lockfileVersion":null,"packages":{"":{}}}'),
    ("version_two", '{"lockfileVersion":2,"packages":{"":{}}}'),
    ("packages_missing", '{"lockfileVersion":3}'),
    ("packages_null", '{"lockfileVersion":3,"packages":null}'),
    ("packages_array", '{"lockfileVersion":3,"packages":[]}'),
    ("packages_empty", '{"lockfileVersion":3,"packages":{}}'),
    ("root_entry_null", '{"lockfileVersion":3,"packages":{"":null}}'),
    ("root_entry_array", '{"lockfileVersion":3,"packages":{"":[]}}'),
]


def write_text(directory, text, name="lock.json"):
    """以 UTF-8 写入文本并返回路径；保留调用方给定的字面写法（如 3.0）。"""
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(text.encode("utf-8"))
    return path


class MinimalValidLockfile(unittest.TestCase):
    """合法最小输入：读取为空清单，list 输出仅 [] 加换行。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_text(self.tmp, MINIMAL, "minimal.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_file_is_exact_minimal_utf8(self):
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), MINIMAL.encode("utf-8"))

    def test_load_returns_empty_root_deps_and_empty_map(self):
        root_deps, packages_map = load_lockfile(self.path)
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, {})

    def test_list_stdout_is_empty_array_newline_only(self):
        result = self.run_cli(["list", self.path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"[]\n")
        self.assertEqual(result.stderr, b"")

    @staticmethod
    def run_cli(argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
        )


class InvalidTopLevelStructureApi(unittest.TestCase):
    """每个结构反例经公开读取入口都必须抛 InputError，不返回清单。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_case_raises_input_error(self):
        for label, text in INVALID_CASES:
            with self.subTest(case=label):
                path = write_text(self.tmp, text, label + ".json")
                with self.assertRaises(InputError):
                    load_lockfile(path)

    def test_float_case_on_disk_is_actually_json_float(self):
        # 固定 3.0 的小数写法：磁盘上必须出现 "3.0"，且解析结果为 float
        # （不是 int、也不是 bool），防止该用例悄悄退化成整数用例。
        text = dict(INVALID_CASES)["version_float"]
        self.assertIn("3.0", text)
        path = write_text(self.tmp, text, "float.json")
        with open(path, "rb") as handle:
            on_disk = handle.read()
        self.assertIn(b"3.0", on_disk)
        version = json.loads(on_disk.decode("utf-8"))["lockfileVersion"]
        self.assertIsInstance(version, float)
        self.assertNotIsInstance(version, bool)
        self.assertNotIsInstance(version, int)
        with self.assertRaises(InputError):
            load_lockfile(path)


class InvalidTopLevelStructureCli(unittest.TestCase):
    """同一批反例经命令行验收：退出码 2、stderr 仅 INPUT_ERROR、stdout 为空。"""

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
        )

    def assert_input_error(self, result):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"INPUT_ERROR\n")
        self.assertEqual(result.stdout, b"")
        # 标准错误只允许约定的一行，不得夹带堆栈。
        self.assertNotIn(b"Traceback", result.stderr)

    def test_list_rejects_each_case(self):
        for label, text in INVALID_CASES:
            with self.subTest(case=label):
                path = write_text(self.tmp, text, label + ".json")
                self.assert_input_error(self.run_cli(["list", path]))

    def test_why_missing_ghost_still_input_error_on_bad_version(self):
        # 选取一个非法版本文件；即便查询的 ghost 不存在，
        # 也必须先报告输入错误，而不是 NOT_FOUND。
        path = write_text(
            self.tmp,
            dict(INVALID_CASES)["version_string"],
            "bad-version.json",
        )
        self.assert_input_error(self.run_cli(["why", path, "ghost"]))

    def test_diff_fails_in_both_directions_against_minimal(self):
        minimal = write_text(self.tmp, MINIMAL, "minimal.json")
        # 选取一个非法版本文件分别放在新、旧位置。
        invalid = write_text(
            self.tmp,
            dict(INVALID_CASES)["version_float"],
            "invalid.json",
        )
        self.assert_input_error(self.run_cli(["diff", invalid, minimal]))
        self.assert_input_error(self.run_cli(["diff", minimal, invalid]))


class InputFilesNotModified(unittest.TestCase):
    """代表性有效与无效输入在函数与命令行调用前后字节一致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.valid = write_text(self.tmp, MINIMAL, "minimal.json")
        self.invalid_version = write_text(
            self.tmp,
            dict(INVALID_CASES)["version_float"],
            "bad-version.json",
        )
        self.invalid_packages = write_text(
            self.tmp,
            dict(INVALID_CASES)["packages_array"],
            "bad-packages.json",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
        )

    def digest(self, path):
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).digest()

    def test_bytes_unchanged_around_api_calls(self):
        paths = [self.valid, self.invalid_version, self.invalid_packages]
        before = {path: self.digest(path) for path in paths}
        load_lockfile(self.valid)
        for path in (self.invalid_version, self.invalid_packages):
            with self.assertRaises(InputError):
                load_lockfile(path)
        after = {path: self.digest(path) for path in paths}
        self.assertEqual(before, after)

    def test_bytes_unchanged_around_cli_calls(self):
        paths = [self.valid, self.invalid_version, self.invalid_packages]
        before = {path: self.digest(path) for path in paths}
        self.assertEqual(self.run_cli(["list", self.valid]).returncode, 0)
        self.assertEqual(
            self.run_cli(["list", self.invalid_version]).returncode, 2
        )
        self.assertEqual(
            self.run_cli(["why", self.invalid_packages, "ghost"]).returncode, 2
        )
        self.assertEqual(
            self.run_cli(["diff", self.invalid_version, self.valid]).returncode, 2
        )
        self.assertEqual(
            self.run_cli(["diff", self.valid, self.invalid_packages]).returncode, 2
        )
        after = {path: self.digest(path) for path in paths}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
