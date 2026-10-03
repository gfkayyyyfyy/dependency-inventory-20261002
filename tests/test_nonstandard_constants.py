"""非标准 JSON 数值常量（NaN、Infinity、-Infinity）整份拒绝的回归测试。

仅使用本地标准库。这些常量不是合法 JSON，无论出现在根条目、已安装
包条目还是额外元数据的任意层级（含根节点不可达包内的对象或数组），
load_lockfile 都必须抛 InputError；CLI 各子命令统一退出码 2、标准
输出为空、标准错误仅为 INPUT_ERROR 加换行，且文件内容保持原样。
字符串值 "NaN"/"Infinity"/"-Infinity"、同名对象键与合法数字文本
（含 1e999）不受影响。
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

EMPTY_LOCK_TEXT = '{"lockfileVersion": 3, "packages": {"": {}}}'


def write_text(directory, text, name="lock.json"):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class NonStandardConstantsRejected(unittest.TestCase):
    """NaN、Infinity、-Infinity 在字符串以外作为值出现即整份拒绝。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def bad_texts(self):
        cases = []
        for const in ("NaN", "Infinity", "-Infinity"):
            cases.append((
                "metadata-%s" % const,
                '{"lockfileVersion": 3, "packages": {"": {}}, "metadata": %s}'
                % const,
            ))
            cases.append((
                "root-entry-%s" % const,
                '{"lockfileVersion": 3, "packages": {"": {"extra": %s}}}' % const,
            ))
            cases.append((
                "package-entry-%s" % const,
                '{"lockfileVersion": 3, "packages": {"": {},'
                ' "node_modules/alpha": {"version": "1.0.0", "extra": %s}}}'
                % const,
            ))
            cases.append((
                "nested-object-%s" % const,
                '{"lockfileVersion": 3, "packages": {"": {},'
                ' "node_modules/alpha": {"version": "1.0.0",'
                ' "extra": {"deep": {"deeper": [%s]}}}}}' % const,
            ))
            cases.append((
                "unreachable-package-%s" % const,
                '{"lockfileVersion": 3, "packages": {"": {},'
                ' "node_modules/orphan": {"version": "9.9.9",'
                ' "metadata": [%s]}}}' % const,
            ))
            cases.append((
                "top-level-array-%s" % const,
                '{"lockfileVersion": 3, "packages": {"": {}},'
                ' "extra": [1, [2, %s]]}' % const,
            ))
        return cases

    def test_load_lockfile_raises_input_error(self):
        for label, text in self.bad_texts():
            path = write_text(self.tmp, text, "%s.json" % label)
            with self.subTest(label=label):
                with self.assertRaises(InputError):
                    load_lockfile(path)

    def test_load_lockfile_returns_nothing_on_failure(self):
        path = write_text(
            self.tmp,
            '{"lockfileVersion": 3, "packages": {"": {}}, "metadata": NaN}',
        )
        try:
            result = load_lockfile(path)
        except InputError:
            result = None
        self.assertIsNone(result)

    def test_file_content_unchanged_after_failure(self):
        text = '{"lockfileVersion": 3, "packages": {"": {}}, "metadata": Infinity}'
        path = write_text(self.tmp, text)
        with self.assertRaises(InputError):
            load_lockfile(path)
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), text)

    def run_cli(self, argv):
        return subprocess.run(
            [sys.executable, "-m", "depinventory", *argv],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def assert_input_error(self, result):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INPUT_ERROR\n")

    def test_cli_list_why_sbom_reject(self):
        path = write_text(
            self.tmp,
            '{"lockfileVersion": 3, "packages": {"": {}}, "metadata": NaN}',
        )
        for argv in (
            ["list", path],
            ["why", path, "alpha"],
            ["why", path, "nonexistent"],
            ["why", path, "alpha", "--from", "beta"],
            ["sbom", path],
        ):
            with self.subTest(argv=argv):
                self.assert_input_error(self.run_cli(argv))

    def test_cli_diff_rejects_either_side_and_swap(self):
        bad = write_text(
            self.tmp,
            '{"lockfileVersion": 3, "packages": {"": {}}, "metadata": -Infinity}',
            "bad.json",
        )
        good = write_text(self.tmp, EMPTY_LOCK_TEXT, "good.json")
        self.assert_input_error(self.run_cli(["diff", bad, good]))
        self.assert_input_error(self.run_cli(["diff", good, bad]))
        self.assert_input_error(self.run_cli(["diff", bad, bad]))

    def test_cli_file_content_unchanged_after_failure(self):
        text = '{"lockfileVersion": 3, "packages": {"": {}}, "metadata": NaN}'
        path = write_text(self.tmp, text)
        self.assert_input_error(self.run_cli(["list", path]))
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), text)


class LegalTextsUnaffected(unittest.TestCase):
    """字符串形式的同名文本、同名对象键与合法数字文本保持原语义。"""

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
            text=True,
        )

    def test_string_nan_metadata_list_succeeds(self):
        path = write_text(
            self.tmp,
            '{"lockfileVersion": 3, "packages": {"": {}}, "metadata": "NaN"}',
        )
        result = self.run_cli(["list", path])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "[]\n")

    def test_string_constants_in_fields_load(self):
        text = (
            '{"lockfileVersion": 3, "packages": {"": {},'
            ' "node_modules/alpha": {"version": "1.0.0",'
            ' "description": "use NaN, Infinity and -Infinity safely",'
            ' "extra": {"NaN": "Infinity", "list": ["-Infinity"]}}}}'
        )
        path = write_text(self.tmp, text)
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map["alpha"]["version"], "1.0.0")

    def test_object_keys_named_like_constants_load(self):
        text = (
            '{"lockfileVersion": 3, "packages": {"": {},'
            ' "node_modules/alpha": {"version": "1.0.0"}},'
            ' "metadata": {"NaN": 1, "Infinity": 2, "-Infinity": 3}}'
        )
        path = write_text(self.tmp, text)
        _, packages_map = load_lockfile(path)
        self.assertIn("alpha", packages_map)

    def test_legal_number_forms_load(self):
        text = (
            '{"lockfileVersion": 3, "packages": {"": {},'
            ' "node_modules/alpha": {"version": "1.0.0"}},'
            ' "metadata": {"int": 42, "neg": -7, "frac": 0.5,'
            ' "exp": 1.5e-10, "huge": 1e999, "arr": [0, -0.25e3]}}'
        )
        path = write_text(self.tmp, text)
        root_deps, packages_map = load_lockfile(path)
        self.assertEqual(root_deps, [])
        self.assertEqual(packages_map["alpha"]["version"], "1.0.0")

    def test_demo_lock_outputs_unchanged(self):
        result = self.run_cli(["list", DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"name": "alpha", "version": "1.0.0", "direct": True},
                {"name": "beta", "version": "2.0.0", "direct": False},
            ],
        )
        result = self.run_cli(["sbom", DEMO_LOCK])
        self.assertEqual(result.returncode, 0)
        document = json.loads(result.stdout)
        self.assertEqual(document["format"], "depinventory-sbom")
        for component in document["components"]:
            self.assertEqual(component["license"], "unknown")
            self.assertEqual(component["securityStatus"], "unknown")


if __name__ == "__main__":
    unittest.main()
