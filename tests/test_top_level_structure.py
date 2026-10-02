"""锁文件顶层结构接收/拒绝边界的回归测试（仅标准库、全程离线）。

固定 README 已公开的约定：
- 合法的最小输入为 {"lockfileVersion":3,"packages":{"":{}}}：
  读取返回空根依赖与空包映射，list 标准输出仅为 "[]\\n"；
- 每次只改变一个结构条件（顶层类型、lockfileVersion、packages、空串根条目），
  其余保持合法：公开入口 depinventory.load_lockfile 必须抛 InputError，
  不返回任何清单；
- 同一批反例经命令行 list 必须为退出码 2、标准错误仅 "INPUT_ERROR\\n"、
  标准输出为空且无堆栈；why 在输入非法时先报输入错误（即使查询名不存在）；
  diff 任一文件非法即整体失败，方向无关；
- 全程使用独立临时文件，不改写仓库样例，调用前后文件字节一致。

不依赖私有函数、异常消息正文或内部处理顺序；不涉及嵌套安装、依赖字段、
编码修复或其他锁文件格式。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from depinventory import InputError, load_lockfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 合法最小输入：无空格的紧凑 JSON，以 UTF-8 原样落盘。
VALID_MINIMAL = '{"lockfileVersion":3,"packages":{"":{}}}'

# 每份反例仅相对合法最小输入改变一个结构条件。
# 原始 JSON 文本直接书写，避免序列化器改写字面量（3.0 必须保持浮点写法）。
INVALID_CASES = [
    ("top-level-array", "[]"),
    ("top-level-null", "null"),
    ("version-missing", '{"packages":{"":{}}}'),
    ("version-string", '{"lockfileVersion":"3","packages":{"":{}}}'),
    ("version-float", '{"lockfileVersion":3.0,"packages":{"":{}}}'),
    ("version-bool", '{"lockfileVersion":true,"packages":{"":{}}}'),
    ("version-null", '{"lockfileVersion":null,"packages":{"":{}}}'),
    ("version-two", '{"lockfileVersion":2,"packages":{"":{}}}'),
    ("packages-missing", '{"lockfileVersion":3}'),
    ("packages-null", '{"lockfileVersion":3,"packages":null}'),
    ("packages-array", '{"lockfileVersion":3,"packages":[]}'),
    ("packages-empty", '{"lockfileVersion":3,"packages":{}}'),
    ("root-null", '{"lockfileVersion":3,"packages":{"":null}}'),
    ("root-array", '{"lockfileVersion":3,"packages":{"":[]}}'),
]


def write_text(directory, content, name):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    return path


class MinimalValidLockfile(unittest.TestCase):
    """合法最小输入的读取结果与 list 输出（接收边界）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.path = write_text(self.tmp, VALID_MINIMAL, "minimal.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_file_is_exact_minimal_utf8_bytes(self):
        with open(self.path, "rb") as handle:
            self.assertEqual(
                handle.read(),
                b'{"lockfileVersion":3,"packages":{"":{}}}',
            )
    def test_load_returns_empty_root_deps_and_empty_map(self):
        root_deps, packages_map = load_lockfile(self.path)
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map, {})
        self.assertIsInstance(root_deps, list)
        self.assertIsInstance(packages_map, dict)

    def test_cli_list_outputs_empty_array_only(self):
        result = subprocess.run(
            [sys.executable, "-m", "depinventory", "list", self.path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "[]\n")
        self.assertEqual(result.stderr, "")

    def test_minimal_file_bytes_unchanged_after_calls(self):
        with open(self.path, "rb") as handle:
            before = handle.read()
        load_lockfile(self.path)
        subprocess.run(
            [sys.executable, "-m", "depinventory", "list", self.path],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), before)


class TopLevelStructureRejectedApi(unittest.TestCase):
    """每个结构反例经公开读取入口都必须抛 InputError，不返回清单。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_case_raises_input_error(self):
        for label, content in INVALID_CASES:
            with self.subTest(case=label):
                path = write_text(self.tmp, content, label + ".json")
                with self.assertRaises(InputError):
                    load_lockfile(path)

    def test_float_literal_is_actually_json_float(self):
        # 守护测试意图：3.0 必须落为 JSON 浮点数（Python float），
        # 而非被任何环节当作整数 3，否则该反例就失去意义。
        path = write_text(
            self.tmp,
            dict(INVALID_CASES)["version-float"],
            "version-float.json",
        )
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)["lockfileVersion"]
        self.assertIsInstance(value, float)
        self.assertNotIsInstance(value, int)
        self.assertEqual(value, 3.0)


class TopLevelStructureRejectedCli(unittest.TestCase):
    """同一批结构反例经 list：退出码 2、stderr 仅 INPUT_ERROR、stdout 空、无堆栈。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_rejects_each_case(self):
        for label, content in INVALID_CASES:
            with self.subTest(case=label):
                path = write_text(self.tmp, content, label + ".json")
                result = subprocess.run(
                    [sys.executable, "-m", "depinventory", "list", path],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "INPUT_ERROR\n")
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("Traceback", result.stdout)


class WhyAndDiffRejectInvalidStructure(unittest.TestCase):
    """why 对非法文件先报输入错误；diff 任一方向放入非法文件都整体失败。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        # 选取一个非法版本文件（lockfileVersion 为 2）作为代表性结构反例。
        self.invalid = write_text(
            self.tmp,
            dict(INVALID_CASES)["version-two"],
            "version-two.json",
        )
        self.valid = write_text(self.tmp, VALID_MINIMAL, "minimal.json")

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_why_missing_ghost_on_invalid_file_is_input_error(self):
        # 文件非法时即使查询的 ghost 不存在，也必须先报 INPUT_ERROR，
        # 而不是 NOT_FOUND。
        result = self.run_cli(["why", self.invalid, "ghost"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "INPUT_ERROR\n")
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_diff_fails_in_both_directions(self):
        for argv in (
            ["diff", self.invalid, self.valid],
            ["diff", self.valid, self.invalid],
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(argv)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "INPUT_ERROR\n")
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)


class InvalidInputsNotModified(unittest.TestCase):
    """代表性有效与无效输入在全部调用前后字节一致，临时文件互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_bytes_identical_before_and_after(self):
        # (标签, 内容, 是否为非法输入)：非法输入先断言抛 InputError，
        # 代表性有效输入则正常读取；两者调用前后字节都必须一致。
        representatives = [
            ("valid-minimal", VALID_MINIMAL, False),
            ("top-level-array", dict(INVALID_CASES)["top-level-array"], True),
            ("version-float", dict(INVALID_CASES)["version-float"], True),
            ("packages-null", dict(INVALID_CASES)["packages-null"], True),
            ("root-array", dict(INVALID_CASES)["root-array"], True),
        ]
        for label, content, invalid in representatives:
            with self.subTest(case=label):
                path = write_text(self.tmp, content, label + ".json")
                with open(path, "rb") as handle:
                    before = handle.read()

                if invalid:
                    with self.assertRaises(InputError):
                        load_lockfile(path)
                else:
                    root_deps, packages_map = load_lockfile(path)
                    self.assertEqual((root_deps, packages_map), ([], {}))

                subprocess.run(
                    [sys.executable, "-m", "depinventory", "list", path],
                    cwd=REPO_ROOT,
                    capture_output=True,
                )
                subprocess.run(
                    [sys.executable, "-m", "depinventory", "why", path, "ghost"],
                    cwd=REPO_ROOT,
                    capture_output=True,
                )

                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), before)


if __name__ == "__main__":
    unittest.main()
