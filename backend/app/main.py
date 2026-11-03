"""FastAPI app. Step 0: contract-shaped stubs. Storage, ingestion and /ask come in steps 2-4."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .schemas import (
    AskRequest,
    AskResponse,
    CourseSummary,
    SyncPayload,
    SyncResponse,
    course_id_for,
)

app = FastAPI(title="AI Lecture Search", version="0.0.1")

# The extension calls us from its background service worker (host_permissions cover
# localhost), so CORS mainly matters for the dev UI and console snippets.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.cors_origin_regex,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Step 0 only: in-memory store so the smoke test can round-trip. Replaced by Postgres in step 2.
_COURSES: dict[str, CourseSummary] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sync", response_model=SyncResponse)
def sync(payload: SyncPayload) -> SyncResponse:
    cid = course_id_for(payload.course.canvas_course_id)
    _COURSES[cid] = CourseSummary(
        course_id=cid,
        code=payload.course.code,
        name=payload.course.name,
        lectures=len(payload.lectures),
        indexed=False,
    )
    return SyncResponse(
        course_id=cid,
        lectures_received=len(payload.lectures),
        files_received=len(payload.files),
        status="accepted",
    )


@app.get("/courses", response_model=list[CourseSummary])
def courses() -> list[CourseSummary]:
    return list(_COURSES.values())


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    if req.course_id not in _COURSES:
        raise HTTPException(status_code=404, detail="course not synced")
    raise HTTPException(status_code=501, detail="/ask is implemented in step 4")
