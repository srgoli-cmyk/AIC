"""SRT parsing. Pure functions, no I/O.

Tolerant on purpose: captions come from a third party, and one bad cue must not lose a lecture.
The file is split on timing lines ("... --> ..."), not on blank lines, so missing separators and
odd whitespace do not merge or lose cues.
"""

import html
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Cue:
    start: float  # seconds from the start of the lecture
    end: float
    text: str


# 00:01:02,345 --> 00:01:05.678. Hours are limited to 5 digits (a longer number is a malformed
# timestamp, not a 400-digit integer to convert); milliseconds use "," or ".".
_T = r"(?<!\d)(\d{1,5}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})(?!\d)"
_TIMING = re.compile(rf"{_T}\s*-->\s*{_T}")
_ARROW = "-->"  # any line with this is a timing line; if it is not valid, its cue is dropped
_CUE_NUMBER = re.compile(r"\s*\d+\s*$")
# Only known caption markup is stripped, so "if x <y and y> z" survives:
# <i> <b> <u> <c> <c.class> <font ...> <v Name> and their closing forms, and {\an8} style marks.
_TAG = re.compile(
    r"</?(?:[ibuc](?:\.[^>\s]*)?|font(?:\s[^>]*)?)>|<v(?:\.[^>\s]*)?\s[^>]*>|</v>|\{\\[^}]*\}",
    re.IGNORECASE,
)
_SPACE = re.compile(r"\s+")


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    # ".5" means 500 ms, not 5 ms, so pad on the right.
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _clean(lines: list[str]) -> str:
    # Unescape after stripping tags, so an escaped "&lt;i&gt;" stays as visible text.
    return _SPACE.sub(" ", html.unescape(_TAG.sub("", " ".join(lines)))).strip()


def parse_srt_with_stats(raw: str) -> tuple[list[Cue], int]:
    """Return (cues sorted by start time, number of dropped cues).

    A cue is dropped when its timing line is malformed, its end is before its start, or nothing
    is left of its text. Never raises on malformed input. Cues with equal starts keep file order.
    """
    lines = raw.lstrip("﻿").splitlines()
    timing_at = [i for i, line in enumerate(lines) if _ARROW in line]
    cues: list[Cue] = []
    dropped = 0
    for n, t in enumerate(timing_at):
        stop = timing_at[n + 1] if n + 1 < len(timing_at) else len(lines)
        body = lines[t + 1 : stop]
        # A number directly above the next timing line is that cue's number, not our text.
        # (Not for the last cue: a number there is not followed by a timing line.)
        if n + 1 < len(timing_at) and body and _CUE_NUMBER.match(body[-1]):
            body = body[:-1]
        m = _TIMING.search(lines[t])
        if not m:
            dropped += 1
            continue
        g = m.groups()
        start, end = _seconds(*g[:4]), _seconds(*g[4:])
        text = _clean(body)
        if end < start or not text:
            dropped += 1
            continue
        cues.append(Cue(start, end, text))
    cues.sort(key=lambda c: c.start)  # stable
    return cues, dropped


def parse_srt(raw: str) -> list[Cue]:
    return parse_srt_with_stats(raw)[0]
