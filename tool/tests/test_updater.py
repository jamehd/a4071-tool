from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.updater import compare_versions, parse_version
from tools.updater import _render_updater_bat


class ParseVersionTests(unittest.TestCase):
    def test_three_parts(self) -> None:
        self.assertEqual(parse_version("0.1.0"), (0, 1, 0))

    def test_two_parts_pads_with_zero(self) -> None:
        self.assertEqual(parse_version("1.2"), (1, 2, 0))

    def test_one_part_pads_with_zeros(self) -> None:
        self.assertEqual(parse_version("3"), (3, 0, 0))

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(parse_version("abc"))
        self.assertIsNone(parse_version("1.x"))
        self.assertIsNone(parse_version(""))


class CompareVersionsTests(unittest.TestCase):
    def test_newer_minor(self) -> None:
        self.assertGreater(compare_versions("0.2.0", "0.1.0"), 0)

    def test_older_patch(self) -> None:
        self.assertLess(compare_versions("0.1.0", "0.1.1"), 0)

    def test_equal(self) -> None:
        self.assertEqual(compare_versions("0.1.0", "0.1.0"), 0)

    def test_unparseable_returns_zero(self) -> None:
        self.assertEqual(compare_versions("nope", "0.1.0"), 0)
        self.assertEqual(compare_versions("0.1.0", "nope"), 0)


class RenderUpdaterBatTests(unittest.TestCase):
    def test_substitutes_pid_and_paths(self) -> None:
        script = _render_updater_bat(
            pid=4242,
            new_exe=r"C:\Users\foo\AppData\Local\Temp\A4071-Tool-update.exe",
            current_exe=r"C:\Apps\A4071-Tool\A4071-Tool.exe",
        )
        self.assertIn('PID eq 4242', script)
        self.assertIn(r'"C:\Users\foo\AppData\Local\Temp\A4071-Tool-update.exe"', script)
        self.assertIn(r'"C:\Apps\A4071-Tool\A4071-Tool.exe"', script)
        self.assertIn("move /Y", script)
        self.assertIn("del \"%~f0\"", script)
        self.assertNotIn("start \"\"", script)

    def test_no_extra_quotes_or_braces(self) -> None:
        script = _render_updater_bat(
            pid=1,
            new_exe=r"C:\a.exe",
            current_exe=r"C:\b.exe",
        )
        self.assertNotIn("{PID}", script)
        self.assertNotIn("{NEW_EXE}", script)
        self.assertNotIn("{CURRENT_EXE}", script)


if __name__ == "__main__":
    unittest.main()
