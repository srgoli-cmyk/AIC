"""Synthetic cases run through the backend's Python code, so the extension's JavaScript ports can be
checked against it (extension/tests/parity.test.mjs).

    python scripts/parity_fixtures.py            # print the fixture JSON
    python scripts/parity_fixtures.py --check    # exit 1 if the committed fixture is out of date

Everything here is generated: no real captions.
"""

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.answer import SYSTEM_PROMPT, citation_label, cited_hits, dedupe, source_url  # noqa: E402
from app.chunking import chunk_cues  # noqa: E402
from app.db import ChunkHit  # noqa: E402
from app.srt import parse_srt_with_stats  # noqa: E402
from app.titles import parse_title  # noqa: E402

FIXTURE = ROOT / "extension/tests/fixtures/parity.json"
WORDS = (
    "the matrix vector span basis we see that so now if x <y and y> z eigen value "
    "row column linear map kernel image proof &amp; &lt;i&gt; café λ okay right"
).split()


def ts(sec: float, sep: str = ",") -> str:
    ms = round(sec * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def make_srt(rng: random.Random) -> str:
    t = rng.uniform(0, 5)
    out = []
    n = rng.randint(0, 120)
    for i in range(n):
        dur = rng.choice([0.0, rng.uniform(0.5, 4), rng.uniform(4, 9), rng.uniform(55, 80)])
        start = t
        end = t + dur
        t = end + rng.choice([0, 0, 0.2, 1.0, 2.0, 12.0])
        words = [rng.choice(WORDS) for _ in range(rng.randint(1, 18))]
        text = " ".join(words)
        if rng.random() < 0.3:
            text += rng.choice([".", "?", "!", '."', ".)", ""])
        if rng.random() < 0.1:
            text = f"<i>{text}</i>"
        if rng.random() < 0.05:
            text = "{\\an8}" + text
        lines = text.split(" ", 1) if rng.random() < 0.2 and " " in text else [text]
        timing = f"{ts(start, rng.choice([',', '.']))} --> {ts(end)}"
        if rng.random() < 0.03:
            timing = "00:00:xx,000 --> 00:00:01,000"
        if rng.random() < 0.02:
            timing = f"{ts(end)} --> {ts(start)}"
        block = [str(i + 1), timing, *lines]
        out.append("\n".join(block))
    sep = rng.choice(["\n\n", "\n", "\r\n\r\n", "\n\n\n"])
    body = sep.join(out)
    if rng.random() < 0.2:
        body = "﻿" + body
    return body + rng.choice(["", "\n", "\n\n"])


def srt_cases() -> list[dict]:
    rng = random.Random(20261107)
    cases = []
    extra = [
        "",
        "1\n00:00:01,000 --> 00:00:02,000\n\n",
        "1\n00:00:01.5 --> 00:00:02,25\nhalf &amp; quarter &#955; &#x3bb; &bogus;\n",
        "x --> y\nnot a cue\n\n2\n00:00:03,000 --> 00:00:04,000\n<b>bold</b> <v Bob>hi</v>\n",
    ]
    for raw in extra + [make_srt(rng) for _ in range(30)]:
        cues, dropped = parse_srt_with_stats(raw)
        chunks = chunk_cues(cues)
        cases.append(
            {
                "srt": raw,
                "cues": [[c.start, c.end, c.text] for c in cues],
                "dropped": dropped,
                "chunks": [[c.start, c.end, c.text] for c in chunks],
            }
        )
    return cases


def title_cases() -> list[dict]:
    titles = [
        "Lecture 17 - Topic - LE [002-000-LE]",
        "lecture 3",
        "Week 2 [A00]",
        "Lecture 99999999999 [ 004 ]",
        "No number here",
        "Lecture 5, 11/3/2026 - MATH 18 [a] [002]",
        "Lecture4",
    ]
    return [{"title": t, "parsed": list(parse_title(t))} for t in titles]


def hit(entry, no, title, start, end, text, url="https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/77"):
    return ChunkHit(entry, title, no, url, start, end, text, 0.0)


def answer_cases() -> list[dict]:
    hits = [
        hit("1_aaaaaaaa", 8, "Lecture 8 - Eigen", 4.0, 60.0, "eigen values"),
        hit("1_aaaaaaaa", 8, "Lecture 8 - Eigen", 50.0, 110.0, "overlap"),
        hit("1_bbbbbbbb", None, "Review [Final], part 2", 3725.0, 3780.0, "review text"),
        hit("1_cccccccc", 9, "Lecture 9", 62.0, 120.0, "kernels", "https://podcast.ucsd.edu/watch/fa26/math018_004-000-le/9"),
        hit("1_dddddddd", 8, "Lecture 8 - Eigen (sec 2)", 30.0, 90.0, "same lecture other section"),
    ]
    replies = [
        "Eigen [Lecture 8, 0:04] and kernels [Lecture 9, 1:02].",
        "Mixed [Lecture 8, 0:04; Lecture 9, 1:02] and bad [Lecture 3, 0:01].",
        "Range [Lecture 8, 0:04-0:10] and same label [Lecture 8, 0:04, 0:50].",
        "Title cite [Review Final, part 2, 1:02:05] ok.",
        "Round (Lecture 9, 1:02) and round text (see above).",
        "Nothing valid [Lecture 7, 9:99] here [0:04].",
        "Inside range [Lecture 9, 1:30] and and-form [Lecture 8, 0:04 and Lecture 9, 1:02].",
    ]
    out = []
    for r in replies:
        cited, cleaned = cited_hits(r, hits)
        out.append({"reply": r, "cited": [hits.index(c) for c in cited], "cleaned": cleaned})
    return [
        {
            "hits": [
                [h.entry_id, h.lecture_no, h.lecture_title, h.start_sec, h.end_sec, h.text, h.media_url]
                for h in hits
            ],
            "labels": [citation_label(h) for h in hits],
            "dedupe": [hits.index(h) for h in dedupe(hits, 3)],
            "urls": [source_url(h.media_url, h.start_sec) for h in hits],
            "replies": out,
        }
    ]


def build() -> dict:
    return {
        "srt": srt_cases(),
        "titles": title_cases(),
        "answer": answer_cases(),
        "system_prompt": SYSTEM_PROMPT,
    }


def main() -> int:
    text = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
    if "--check" in sys.argv:
        if not FIXTURE.exists() or FIXTURE.read_text() != text:
            print("parity fixture is out of date: run python scripts/parity_fixtures.py > " + str(FIXTURE.relative_to(ROOT)))
            return 1
        print("parity fixture: ok")
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
