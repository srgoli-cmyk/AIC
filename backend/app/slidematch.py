"""Which slide pages go with a lecture, for the study tools.

A deck's name or date rarely lines up with a recording (sections meet on different days, and a
chapter deck spans many lectures), and the caption embeddings barely tell one linear algebra deck
from another. Words do: "inverse", "determinant", "eigenvector". So each stretch of the lecture
votes, by TF-IDF, for the slide page closest to what was said, pages are grouped into parts (a
"Section 2.2" heading and the pages after it, or a whole deck that has no headings), and the
parts most of the lecture voted for are returned whole.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

from . import db

WINDOW = 3  # caption chunks per vote
STEP = 2
MIN_SIMILARITY = 0.12  # a stretch closer to no page than this does not vote
MIN_VOTES = 3
SHARE_OF_TOP = 0.5  # a part needs at least half the votes of the best part
MAX_PARTS = 3
MAX_CHARS = 24_000

# Exams, solutions and the syllabus are not the lecture's own slides.
NOT_SLIDES = re.compile(
    r"exam|final|midterm|soln|solution|answer|key\b|quiz|syllabus|practice|review|card",
    re.IGNORECASE,
)
# `Oct19` and `Oct19_filled` are one set of notes; the filled copy has the worked answers.
FILLED = re.compile(r"[ _-]*(?:partly[ _-]?)?filled$", re.IGNORECASE)
HEADING = re.compile(r"\bSection\s+(\d{1,2}\.\d{1,2})\b")
# "[004-000-LE]" in a lecture title is its section.
LECTURE_SECTION = re.compile(r"\[(\d{3})-\d{3}-[A-Z]{2}\]")
WORD = re.compile(r"[a-z]{3,}")
STOP = frozenset(
    "the and for are you that this with have what from they will then there their when which "
    "would about these into just like going want okay right because said were here does make "
    "know think really also some something thing things your let's well other more over only "
    "same than them very where been being each such could should can now get got one two see "
    "way yeah its it's how why who all any not but our out use".split()
)


@dataclass(frozen=True)
class Page:
    doc_id: str
    name: str
    url: str
    page: int
    text: str
    part: tuple[str, str]  # (doc_id, heading); heading "" for a deck with none


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP]


def _own_section(text: str, section: str) -> bool:
    """`Math 18, Lecture 004` names section 004; `Section 4.1` (a textbook section) does not."""
    return bool(re.search(rf"\b(?:lecture|section|sec\.?)\s+{section}\b", text, re.IGNORECASE))


def candidate_pages(rows: list[tuple[str, str, str, int, str]], lecture_title: str) -> list[Page]:
    """The course's slide pages that could go with the lecture: not exams or solutions, the
    filled copy of a set of notes only, and when some decks name the lecture's own section
    ("Math 18, Lecture 004"), only those."""
    by_doc: dict[str, list[tuple[str, str, str, int, str]]] = {}
    for row in rows:
        if not NOT_SLIDES.search(row[1]):
            by_doc.setdefault(row[0], []).append(row)
    names = {doc_id: pages[0][1] for doc_id, pages in by_doc.items()}
    keys: dict[str, list[str]] = {}
    for doc_id, name in names.items():
        keys.setdefault(FILLED.sub("", name).lower(), []).append(doc_id)
    keep = set()
    for docs in keys.values():
        filled = [d for d in docs if re.search(r"(?<!partly)filled$", names[d], re.IGNORECASE)]
        keep.add((filled or sorted(docs))[0])
    m = LECTURE_SECTION.search(lecture_title)
    if m:
        own = {d for d in keep if any(_own_section(r[4], m.group(1)) for r in by_doc[d])}
        keep = own or keep
    pages = []
    for doc_id in sorted(keep):
        heading = ""
        for d, name, url, number, text in sorted(by_doc[doc_id], key=lambda r: r[3]):
            found = HEADING.search(text)
            heading = found.group(1) if found else heading  # a page continues the last heading
            pages.append(Page(d, name, url, number, text, (d, heading)))
    return pages


def _vectors(texts: list[str]) -> tuple[list[dict[str, float]], dict[str, float]]:
    counts = [Counter(_words(t)) for t in texts]
    df: Counter = Counter()
    for c in counts:
        df.update(c.keys())
    n = len(counts)
    # Words on more than a third of the pages ("matrix" in a linear algebra course) do not help.
    idf = {w: math.log(n / k) for w, k in df.items() if k < n / 3}
    return [_unit(c, idf) for c in counts], idf


def _unit(counts: Counter, idf: dict[str, float]) -> dict[str, float]:
    v = {w: (1 + math.log(k)) * idf[w] for w, k in counts.items() if w in idf}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {w: x / norm for w, x in v.items()}


def _similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def choose(pages: list[Page], captions: list[str]) -> list[Page]:
    """The pages of the parts the lecture talks about most, best part first."""
    if not pages or not captions:
        return []
    vectors, idf = _vectors([p.text for p in pages])
    votes: Counter = Counter()
    for start in range(0, len(captions), STEP):
        said = _unit(Counter(_words(" ".join(captions[start : start + WINDOW]))), idf)
        scores = [_similarity(said, v) for v in vectors]
        best = max(range(len(pages)), key=scores.__getitem__)
        if scores[best] > MIN_SIMILARITY:
            votes[pages[best].part] += 1
    if not votes:
        return []
    top = votes.most_common(1)[0][1]
    parts = [
        part
        for part, n in votes.most_common(MAX_PARTS)
        if n >= MIN_VOTES and n >= SHARE_OF_TOP * top
    ]
    chosen, total = [], 0
    for part in parts:
        for page in pages:
            if page.part != part:
                continue
            total += len(page.text)
            if total > MAX_CHARS:
                return chosen
            chosen.append(page)
    return chosen


def slide_pages_for(entry_id: str, lecture: list[db.ChunkHit]) -> list[db.ChunkHit]:
    """The slide pages that go with this lecture, as passages, or [] when none clearly do."""
    course_id = db.lecture_course(entry_id)
    if not course_id or not lecture:
        return []
    pages = candidate_pages(db.course_pages(course_id), lecture[0].lecture_title)
    return [
        db.ChunkHit(
            p.doc_id, p.name, None, p.url, float(p.page), float(p.page), p.text, 0.0, p.page
        )
        for p in choose(pages, [h.text for h in lecture])
    ]
