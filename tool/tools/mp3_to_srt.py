from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SENTENCE_END = {".", "?", "!"}


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    lines: list[str]


def _strip_leading(text: str) -> str:
    return text.lstrip()


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in SENTENCE_END


def pack_cues(
    words: list[Word],
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    max_duration: float = 7.0,
) -> list[Cue]:
    if not words:
        return []

    cues: list[Cue] = []
    cue_lines: list[str] = [""]
    cue_start: float = words[0].start
    cue_end: float = words[0].start

    def finalize() -> None:
        nonlocal cue_lines, cue_start, cue_end
        cleaned = [line for line in cue_lines if line]
        if cleaned:
            cues.append(
                Cue(
                    index=len(cues) + 1,
                    start=cue_start,
                    end=cue_end,
                    lines=cleaned,
                )
            )
        cue_lines = [""]

    for word in words:
        token = _strip_leading(word.text)
        if not token:
            continue
        if not cue_lines[-1] and len(cue_lines) == 1 and not cues and cue_start == words[0].start and cue_end == words[0].start:
            cue_start = word.start

        current_line = cue_lines[-1]
        candidate = (current_line + " " + token).strip() if current_line else token

        too_long = len(candidate) > max_chars_per_line
        too_far = (word.end - cue_start) > max_duration

        if too_far and (current_line or len(cue_lines) > 1):
            finalize()
            cue_start = word.start
            cue_end = word.end
            cue_lines = [token]
            continue

        if too_long:
            if len(cue_lines) < max_lines:
                cue_lines.append(token)
                cue_end = word.end
            else:
                finalize()
                cue_start = word.start
                cue_end = word.end
                cue_lines = [token]
            if _ends_sentence(token):
                finalize()
                cue_start = word.end
                cue_end = word.end
            continue

        cue_lines[-1] = candidate
        cue_end = word.end

        if _ends_sentence(token):
            finalize()
            cue_start = word.end
            cue_end = word.end

    finalize()
    return cues


def _format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3600 * 1000)
    minutes, rem = divmod(rem, 60 * 1000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(cues: list[Cue], srt_path: Path) -> None:
    parts: list[str] = []
    for cue in cues:
        parts.append(str(cue.index))
        parts.append(f"{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}")
        parts.extend(cue.lines)
        parts.append("")
    body = "\r\n".join(parts)
    if not body.endswith("\r\n"):
        body += "\r\n"
    srt_path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
