"""Pydantic models mirroring contracts/sync_payload.schema.json and contracts/api.md.

If you change anything here, the contract must change first (maintainer approval).
extra="forbid" is a security control: unknown fields such as tokens are rejected with 422.
"""

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# Same pattern as media_url in contracts/sync_payload.schema.json (a test compares them). A
# canvaskaf link needs its category (without it the page answers "Access denied"); a
# podcast.ucsd.edu link is one lecture of one section.
MEDIA_URL_PATTERN = (
    r"^https://(canvaskaf\.ucsd\.edu/media/t/[0-9]_[a-z0-9]{8}/[0-9]+"
    r"|podcast\.ucsd\.edu/watch/[a-z]{2}[0-9]{2}/[a-z0-9]{2,12}_[0-9]{3}-[0-9]{3}-[a-z]{2}/[0-9]{1,3})$"
)
MEDIA_URL_RE = re.compile(MEDIA_URL_PATTERN)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Instructor = Annotated[str, StringConstraints(min_length=1, max_length=200)]
SectionName = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class Course(_Strict):
    canvas_course_id: int = Field(ge=1)
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    term: str | None = Field(default=None, max_length=50)
    term_start: date | None = None
    term_end: date | None = None
    instructors: list[Instructor] | None = Field(default=None, max_length=20)
    sections: list[SectionName] | None = Field(default=None, max_length=50)


class Lecture(_Strict):
    source: Literal["kaltura"]
    entry_id: str = Field(pattern=r"^[0-9]_[a-z0-9]{8}$")
    title: str = Field(min_length=1, max_length=300)
    recorded_at: date | None = None
    recorded_start: datetime | None = None
    duration_sec: float = Field(ge=0)
    media_url: str
    captions_srt: str = Field(min_length=1, max_length=2_000_000)

    @field_validator("media_url")
    @classmethod
    def _media_url_has_category(cls, value: str) -> str:
        # fullmatch, not match: `$` in a pattern can accept a trailing newline, fullmatch cannot.
        # The message never repeats the rejected value.
        if not MEDIA_URL_RE.fullmatch(value):
            raise ValueError(
                "media_url must be https://canvaskaf.ucsd.edu/media/t/<entryId>/<categoryId> "
                "or https://podcast.ucsd.edu/watch/<term>/<section>/<n>, with no query or fragment"
            )
        return value


class CanvasFile(_Strict):
    canvas_file_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=300)
    content_type: str | None = Field(default=None, max_length=100)


class SyncPayload(_Strict):
    schema_version: Literal["1"]
    synced_at: datetime
    course: Course
    lectures: list[Lecture] = Field(max_length=200)
    files: list[CanvasFile] = Field(max_length=500)


class SyncResponse(BaseModel):
    course_id: str
    lectures_received: int
    files_received: int
    lectures_skipped_other_course: int
    status: Literal["accepted"]


class CourseSummary(BaseModel):
    course_id: str
    code: str
    name: str
    lectures: int
    indexed: bool


class LectureStatus(BaseModel):
    entry_id: str
    has_captions: bool


class LectureInfo(BaseModel):
    entry_id: str
    title: str
    lecture_no: int | None
    section: str | None
    recorded_at: date | None
    recorded_start: datetime | None
    duration_sec: float
    media_url: str
    indexed: bool


class CourseDetail(BaseModel):
    course_id: str
    canvas_course_id: int
    code: str
    name: str
    term: str | None
    term_start: date | None
    term_end: date | None
    instructors: list[str]
    sections: list[str]
    synced_at: datetime
    indexed: bool
    lectures: list[LectureInfo]


class AskRequest(_Strict):
    course_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=12, ge=1, le=20)
    # Optional: search one lecture of the course instead of all of them.
    entry_id: str | None = Field(default=None, pattern=r"^[0-9]_[a-z0-9]{8}$")


class Source(BaseModel):
    lecture_title: str
    entry_id: str
    start_sec: float
    end_sec: float
    timestamp: str
    url: str
    snippet: str


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]


class StudyRequest(_Strict):
    entry_id: str = Field(pattern=r"^[0-9]_[a-z0-9]{8}$")
    kind: Literal["teach", "flashcards", "quiz"]


class LessonProblem(BaseModel):
    question: str
    steps: list[str]
    answer: str
    # True only when the lecture works this problem; `source` is then where.
    from_lecture: bool
    source: Source | None


class LessonSection(BaseModel):
    title: str
    body: str
    source: Source | None
    problems: list[LessonProblem]


class LessonResponse(BaseModel):
    sections: list[LessonSection]
    check: list[str]


class Card(BaseModel):
    front: str
    back: str
    source: Source | None


class CardsResponse(BaseModel):
    cards: list[Card]


class PracticeQuestion(BaseModel):
    question: str
    choices: list[str]
    answer: int
    why: str
    level: int
    source: Source | None


class PracticeResponse(BaseModel):
    questions: list[PracticeQuestion]


def course_id_for(canvas_course_id: int) -> str:
    return f"canvas-{canvas_course_id}"
