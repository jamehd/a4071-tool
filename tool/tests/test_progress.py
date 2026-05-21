from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.progress import format_eta


class FormatEtaTests(unittest.TestCase):
    def test_returns_none_when_pct_is_zero(self) -> None:
        self.assertIsNone(format_eta(10.0, 0.0))

    def test_returns_none_when_pct_below_one(self) -> None:
        self.assertIsNone(format_eta(10.0, 0.5))

    def test_returns_none_when_elapsed_is_zero(self) -> None:
        self.assertIsNone(format_eta(0.0, 50.0))

    def test_seconds_bucket(self) -> None:
        # 10s elapsed at 25% -> 30s remaining
        self.assertEqual(format_eta(10.0, 25.0), "còn ~30 giây")

    def test_minutes_and_seconds_under_5_minutes(self) -> None:
        # 60s elapsed at 25% -> 180s remaining = 3 phút 0 giây
        self.assertEqual(format_eta(60.0, 25.0), "còn ~3 phút 0 giây")

    def test_minutes_only_at_or_above_5(self) -> None:
        # 60s elapsed at 10% -> 540s remaining = 9 phút
        self.assertEqual(format_eta(60.0, 10.0), "còn ~9 phút")

    def test_hours_format(self) -> None:
        # 3600s elapsed at 25% -> 10800s remaining = 3h 0m
        self.assertEqual(format_eta(3600.0, 25.0), "còn ~3h 0m")

    def test_rounding_carry_seconds_to_one_minute(self) -> None:
        # 59.5s elapsed at 50% -> 59.5s remaining, rounds to 60s -> carry to 1 phút 0 giây
        self.assertEqual(format_eta(59.5, 50.0), "còn ~1 phút 0 giây")

    def test_rounding_carry_seconds_to_next_minute(self) -> None:
        # 239.5s elapsed at 50% -> 239.5s remaining, rounds to 240s = 4 phút 0 giây
        self.assertEqual(format_eta(239.5, 50.0), "còn ~4 phút 0 giây")

    def test_rounding_carry_into_minutes_only_bucket(self) -> None:
        # 299.5s elapsed at 50% -> 299.5s remaining, rounds to 300s = 5 phút (minutes-only)
        self.assertEqual(format_eta(299.5, 50.0), "còn ~5 phút")


import tkinter as tk

from tools.progress import ProgressPanel


class ProgressPanelSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk not available: {exc}")
        self.root.withdraw()

    def tearDown(self) -> None:
        self.root.destroy()

    def test_full_lifecycle_does_not_raise(self) -> None:
        panel = ProgressPanel(self.root)
        panel.pack()
        panel.start("Bắt đầu")
        panel.set_progress(50.0, "Đang chạy")
        panel.set_indeterminate("Tạm dừng")
        panel.set_progress(75.0, "Tiếp tục")
        panel.finish("Hoàn tất")
        panel.reset()
        self.root.update()


if __name__ == "__main__":
    unittest.main()
