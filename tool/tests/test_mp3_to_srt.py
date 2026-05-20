from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mp3_to_srt import Cue, Word, pack_cues


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
        words = [w(f" w{i}", i * 1.0, i * 1.0 + 0.9) for i in range(10)]
        cues = pack_cues(words, max_chars_per_line=200, max_lines=2, max_duration=3.0)
        for cue in cues:
            self.assertLessEqual(cue.end - cue.start, 3.5)

    def test_indices_are_sequential_from_one(self) -> None:
        words = [w(f" word{i}.", i * 0.5, i * 0.5 + 0.4) for i in range(5)]
        cues = pack_cues(words, max_chars_per_line=8, max_lines=1, max_duration=60.0)
        self.assertEqual([c.index for c in cues], list(range(1, len(cues) + 1)))


if __name__ == "__main__":
    unittest.main()
