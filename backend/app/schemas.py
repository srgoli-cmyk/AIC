"""Pydantic models mirroring contracts/sync_payload.schema.json and contracts/api.md.

If you change anything here, the contract must change first (maintainer approval).
extra="forbid" is a security control: unknown fields such as tokens are rejected with 422.
"""

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Same pattern as media_url in contracts/sync_payload.schema.json (a test compares them). Without
# the category the lecture page answers "Access denied".
MEDIA_URL_PATTERN = r"^https://canvaskaf\.ucsd\.edu/media/t/[0-9]_[a-z0-9]{8}/[0-9]+$"
MEDIA_URL_RE = re.compile(MEDIA_URL_PATTERN)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Course(_Strict):
    canvas_course_id: int = Field(ge=1)
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    term: str | None = Field(default=None, max_length=50)


class Lecture(_Strict):
    source: Literal["kaltura"]
    entry_id: str = Field(pattern=r"^[0-9]_[a-z0-9]{8}$")
    title: str = Field(min_length=1, max_length=300)
    recorded_at: date | None = None
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
                "media_url must be https://canvaskaf.ucsd.edu/media/t/<entryId>/<categoryId>, "
                "with no query or fragment"
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


class AskRequest(_Strict):
    course_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=12, ge=1, le=20)


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


def course_id_for(canvas_course_id: int) -> str:
    return f"canvas-{canvas_course_id}"
