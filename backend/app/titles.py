"""Fields derived from a lecture title like "Lecture 17 - Topic - LE [002-000-LE]"."""

import re

_LECTURE_NO = re.compile(r"\bLecture\s+(\d+)", re.IGNORECASE)
_FIRST_BRACKET = re.compile(r"\[([^\]]*)\]")
_LEADING_DIGITS = re.compile(r"\s*(\d+)")
INT_MAX = 2**31 - 1  # lectures.lecture_no is an INTEGER


def parse_title(title: str) -> tuple[int | None, str | None]:
    """Return (lecture_no, section); each is None when the title does not have it."""
    lecture_no = None
    m = _LECTURE_NO.search(title)
    if m and int(m.group(1)) <= INT_MAX:  # a larger number is stored as NULL, not an error
        lecture_no = int(m.group(1))
    section = None
    b = _FIRST_BRACKET.search(title)
    if b:
        d = _LEADING_DIGITS.match(b.group(1))
        section = d.group(1) if d else None
    return lecture_no, section
