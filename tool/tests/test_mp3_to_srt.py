from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mp3_to_srt import Cue, Word, pack_cues, write_srt


def w(text: str, start: float, end: float) -> Word:
    return Word(start=start, end=end, text=text)


class PackCuesTests(unittest.TestCase):
    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(pack_cues([]), [])

    def test_single_short_word_becomes_one_cue(self) -> None:
        cues = pack_cues([w("Hello.", 0.0, 0.5)])
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].index, 1)
        self.assertEqual(cues[0].lines, ["Hello."])
        self.assertEqual(cues[0].start, 0.0)
        self.assertEqual(cues[0].end, 0.5)

    def test_line_wraps_at_max_chars(self) -> None:
        words = [w(" ten-chars", i * 0.5, i * 0.5 + 0.5) for i in range(6)]
        cues = pack_cues(words, max_chars_per_line=20, max_lines=2, max_duration=60.0)
        for cue in cues:
            for line in cue.lines:
                self.assertLessEqual(len(line), 20)
            self.assertLessEqual(len(cue.lines), 2)

    def test_sentence_end_closes_cue(self) -> None:
        words = [
            w("Hi", 0.0, 0.2),
            w(" there.", 0.2, 0.5),
            w(" Next", 0.6, 0.9),
            w(" sentence.", 0.9, 1.2),
        ]
        cues = pack_cues(words, max_chars_per_line=80, max_lines=2, max_duration=60.0)
        self.assertEqual(len(cues), 2)
        self.assertTrue(cues[0].lines[0].endswith("."))
        self.assertTrue(cues[1].lines[0].endswith("."))

    def test_max_duration_closes_cue(self) -> None:
        max_duration = 3.0
        word_span = 0.9
        words = [w(f" w{i}", i * 1.0, i * 1.0 + word_span) for i in range(10)]
        cues = pack_cues(words, max_chars_per_line=200, max_lines=2, max_duration=max_duration)
        for cue in cues:
            # A cue may exceed max_duration by up to one word's span because the
            # cut happens after the word that crosses the threshold.
            self.assertLessEqual(cue.end - cue.start, max_duration + word_span)

    def test_sentence_end_finalizes_even_when_token_overflows(self) -> None:
        words = [
            w("short", 0.0, 0.3),
            w(" averylongsentencehere.", 0.3, 0.6),
            w(" Next", 0.7, 1.0),
        ]
        cues = pack_cues(words, max_chars_per_line=10, max_lines=3, max_duration=60.0)
        self.assertTrue(any(c.lines[-1].endswith(".") for c in cues))
        last_cue_with_period = next(
            i for i, c in enumerate(cues) if c.lines[-1].endswith(".")
        )
        # "Next" must land in a later cue than the one closed by the period.
        for c in cues[last_cue_with_period + 1:]:
            self.assertTrue(any("Next" in line for line in c.lines))

    def test_indices_are_sequential_from_one(self) -> None:
        words = [w(f" word{i}.", i * 0.5, i * 0.5 + 0.4) for i in range(5)]
        cues = pack_cues(words, max_chars_per_line=8, max_lines=1, max_duration=60.0)
        self.assertEqual([c.index for c in cues], list(range(1, len(cues) + 1)))


import tempfile


class WriteSrtTests(unittest.TestCase):
    def test_writes_standard_srt(self) -> None:
        cues = [
            Cue(index=1, start=0.0, end=1.5, lines=["Hello world."]),
            Cue(index=2, start=2.0, end=4.25, lines=["Second cue", "second line."]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.srt"
            write_srt(cues, path)
            data = path.read_bytes()
        text = data.decode("utf-8-sig")
        self.assertIn("\r\n", text)
        lines = text.split("\r\n")
        self.assertEqual(lines[0], "1")
        self.assertEqual(lines[1], "00:00:00,000 --> 00:00:01,500")
        self.assertEqual(lines[2], "Hello world.")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], "2")
        self.assertEqual(lines[5], "00:00:02,000 --> 00:00:04,250")
        self.assertEqual(lines[6], "Second cue")
        self.assertEqual(lines[7], "second line.")

    def test_timestamp_formats_hours_and_milliseconds(self) -> None:
        cues = [Cue(index=1, start=3661.789, end=3662.001, lines=["x"])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.srt"
            write_srt(cues, path)
            text = path.read_text(encoding="utf-8-sig")
        self.assertIn("01:01:01,789 --> 01:01:02,001", text)


if __name__ == "__main__":
    unittest.main()
