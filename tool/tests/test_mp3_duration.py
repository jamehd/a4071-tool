from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mp3_merger import _parse_mp3_first_frame_bitrate_bps, mp3_duration


def _mp3_frame_header_128kbps_44100_mono() -> bytes:
    # MPEG-1 Layer III, bitrate index 9 (128 kbps), sample rate index 0
    # (44100 Hz), no padding, mono.
    return bytes([0xFF, 0xFB, 0x90, 0x40])


class ParseFirstFrameTests(unittest.TestCase):
    def test_returns_bitrate_for_mpeg1_layer3_frame(self) -> None:
        header = _mp3_frame_header_128kbps_44100_mono()
        self.assertEqual(_parse_mp3_first_frame_bitrate_bps(header), 128_000)

    def test_skips_id3v2_tag_before_frame(self) -> None:
        tag_size = 10
        size_bytes = bytes([
            (tag_size >> 21) & 0x7F,
            (tag_size >> 14) & 0x7F,
            (tag_size >> 7) & 0x7F,
            tag_size & 0x7F,
        ])
        data = (
            b"ID3" + b"\x03\x00" + b"\x00" + size_bytes
            + b"\x00" * tag_size
            + _mp3_frame_header_128kbps_44100_mono()
        )
        self.assertEqual(_parse_mp3_first_frame_bitrate_bps(data), 128_000)

    def test_returns_none_for_invalid_bytes(self) -> None:
        self.assertIsNone(_parse_mp3_first_frame_bitrate_bps(b"\x00" * 32))

    def test_returns_none_for_too_short_buffer(self) -> None:
        self.assertIsNone(_parse_mp3_first_frame_bitrate_bps(b"\xFF\xFB"))

    def test_skips_false_sync_byte_before_real_frame(self) -> None:
        # 0xFF 0xE0 is a sync-shaped pattern but version=2.5/layer=reserved
        # which the parser rejects. We expect the scan to keep going and
        # find the real MPEG-1 L3 frame that follows.
        false_sync = bytes([0xFF, 0xE0, 0x00, 0x00])
        data = false_sync + _mp3_frame_header_128kbps_44100_mono()
        self.assertEqual(_parse_mp3_first_frame_bitrate_bps(data), 128_000)


class Mp3DurationTests(unittest.TestCase):
    def test_returns_none_for_missing_file(self) -> None:
        self.assertIsNone(mp3_duration(Path("does/not/exist.mp3"), ffprobe=None))

    def test_estimates_duration_from_cbr_header(self) -> None:
        header = _mp3_frame_header_128kbps_44100_mono()
        payload = header + b"\x00" * (32_000 - len(header))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tone.mp3"
            path.write_bytes(payload)
            self.assertAlmostEqual(mp3_duration(path, ffprobe=None), 2.0, places=2)

    def test_returns_none_for_non_mp3_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "junk.mp3"
            path.write_bytes(b"NOT AN MP3" * 100)
            self.assertIsNone(mp3_duration(path, ffprobe=None))


if __name__ == "__main__":
    unittest.main()
