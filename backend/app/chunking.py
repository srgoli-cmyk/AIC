"""Merge caption cues into search chunks. Pure and deterministic.

Cues are taken in start-time order (a stable sort). Rules, in seconds unless noted:
- A chunk is a run of whole cues, 45-60 s long, counting its overlap. Length is measured to the
  latest cue end seen so far, so cues that overlap each other cannot stretch a chunk.
- It is cut at the first cue that ends a sentence, or that is followed by a pause over 1.5 s,
  once it is at least 45 s long. If no such point appears, it is cut at the last cue that keeps
  it within 60 s, or before its text would pass MAX_CHARS.
- The next chunk starts with whole cues from the end of the previous one, so a sentence is not
  lost at a boundary: the overlap closest to 10 s, at most about 13 s, or a single last cue that
  is longer. Never the whole previous chunk. No overlap across a pause of over 10 s, and none
  into or out of a cue longer than 60 s, which is a chunk by itself.
- The final chunk may be shorter than 45 s.
"""

import re
from dataclasses import dataclass

from .srt import Cue

CHUNKER_VERSION = "1"  # bump when chunking output changes, so stored lectures are re-chunked

MIN_WINDOW = 45.0
MAX_WINDOW = 60.0
PAUSE = 1.5
OVERLAP = 10.0  # the target
OVERLAP_CEILING = 13.0  # largest overlap made of several cues
OVERLAP_MAX_GAP = 10.0  # no overlap if the next cue starts this long after the chunk ends
# 2,000 characters is about 400 words: more than a 60 s window of speech (about 150 words) and
# near what the embedding model reads (512 tokens). It stops runs of cues with no real duration
# from becoming one huge chunk. A single cue longer than this stays whole.
MAX_CHARS = 2000
OVERLAP_MAX_CHARS = MAX_CHARS // 4  # keeps overlap small, so progress stays linear

# . ? ! optionally followed by closing quotes or brackets.
_SENTENCE_END = re.compile(r"[.?!][\"'\)\]”’»]*$")


@dataclass(frozen=True)
class Chunk:
    start: float  # start of the first cue
    end: float  # latest end among its cues
    text: str


def _window_end(cues: list[Cue], first: int) -> tuple[int, float]:
    """(index of the last cue, latest end time) of a chunk that begins at cues[first]."""
    k = first
    latest = cues[first].end
    chars = len(cues[first].text)
    while k < len(cues) - 1:
        nxt = cues[k + 1]
        if latest - cues[first].start >= MIN_WINDOW and (
            _SENTENCE_END.search(cues[k].text) or nxt.start - latest > PAUSE
        ):
            break
        if max(latest, nxt.end) - cues[first].start > MAX_WINDOW:
            break  # adding the next cue would exceed the window
        if chars + 1 + len(nxt.text) > MAX_CHARS:
            break
        k += 1
        latest = max(latest, nxt.end)
        chars += 1 + len(nxt.text)
    return k, latest


def _overlap_start(cues: list[Cue], prev_first: int, last: int, prev_end: float) -> int:
    """Index of the first cue of the next chunk. Candidates are whole cues from the end of the
    previous chunk (cues[prev_first..last], never reaching prev_first): up to OVERLAP_CEILING
    seconds, or the last cue alone if it is longer (but not a long cue). The one closest to
    OVERLAP wins; with a tie, the longer. None fits: no overlap (last + 1)."""
    best, best_key = last + 1, None
    chars = 0
    s = last
    while s > prev_first:
        chars += 1 + len(cues[s].text)
        length = prev_end - cues[s].start
        fits = length <= OVERLAP_CEILING or (s == last and length <= MAX_WINDOW)
        if chars > OVERLAP_MAX_CHARS or (not fits and s != last):
            break
        if fits:
            key = (abs(length - OVERLAP), -length)
            if best_key is None or key < best_key:
                best, best_key = s, key
        s -= 1
    return best


def chunk_cues(cues: list[Cue]) -> list[Chunk]:
    cues = sorted(cues, key=lambda c: c.start)
    chunks: list[Chunk] = []
    first = 0  # first cue of the chunk being built (may be an overlap cue)
    new_from = 0  # first cue not yet in any chunk
    while new_from < len(cues):
        last, end = _window_end(cues, first)
        if last < new_from:
            # Only overlap cues fit (the next cue is too long to join them): start fresh.
            first = new_from
            last, end = _window_end(cues, first)
        chunks.append(
            Chunk(cues[first].start, end, " ".join(c.text for c in cues[first : last + 1]))
        )
        new_from = last + 1
        if new_from < len(cues) and cues[new_from].start - end > OVERLAP_MAX_GAP:
            first = new_from  # long pause: overlap would start the chunk far before the speech
        else:
            first = _overlap_start(cues, first, last, end)
    return chunks
