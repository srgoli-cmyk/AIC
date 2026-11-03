"""Pydantic models mirroring contracts/sync_payload.schema.json and contracts/api.md.

If you change anything here, the contract must change first (maintainer + human approval).
extra="forbid" is a security control: unknown fields such as tokens are rejected with 422.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    media_url: str = Field(pattern=r"^https://canvaskaf\.ucsd\.edu/media/")
    captions_srt: str = Field(min_length=1, max_length=2_000_000)


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
    status: Literal["accepted", "indexed"]


class CourseSummary(BaseModel):
    course_id: str
    code: str
    name: str
    lectures: int
    indexed: bool


class AskRequest(_Strict):
    course_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=20)


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
