"""FastAPI app. Sync and courses are stored in Postgres (app/db.py); after a sync, captions are
chunked and embedded in the background (app/ingest.py); /ask answers from the stored chunks
(app/answer.py)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError

from . import answer, db, ingest
from .config import settings
from .schemas import (
    AskRequest,
    AskResponse,
    CourseSummary,
    LectureStatus,
    SyncPayload,
    SyncResponse,
)

log = logging.getLogger("aic")
# Show INFO from our own logger under uvicorn. Only the `aic` logger is touched, not the root
# logger, so httpx's per-request INFO lines stay off. The guard avoids a second handler (and
# doubled lines) if this module is imported again.
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
    log.addHandler(_handler)
log.setLevel(logging.INFO)

# Connection-level failures (server down, refused, dropped). Their text can contain the
# connection string or bound parameters, so it is never logged or returned.
DB_DOWN = (OperationalError, InterfaceError, db.SchemaBusy)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Create tables missing on an existing database volume. If Postgres is down (or the schema is
    # locked) we still start; the schema is applied again on the first database use, and requests
    # answer 503 until then.
    try:
        db.ensure_schema()
    except DB_DOWN:
        log.warning("database unavailable at startup; schema will be applied on first use")
    yield


app = FastAPI(title="AI Lecture Search", version="0.0.1", lifespan=lifespan)

# The extension calls us from its background service worker (host_permissions cover
# localhost), so CORS mainly matters for the dev UI and console snippets.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.cors_origin_regex,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
@app.exception_handler(db.SchemaBusy)
def database_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "database unavailable"})


@app.exception_handler(db.ValueRejected)
def value_rejected(_request: Request, _exc: db.ValueRejected) -> JSONResponse:
    # Only /sync raises this: the database or driver refused a value the contract allows (integer
    # out of range, NUL byte, ...). Fixed text, nothing from the original error.
    return JSONResponse(status_code=422, content={"detail": "a value was rejected by the database"})


def _clean(part, limit=None):
    # Field names come from the request body: a lone surrogate would break JSON encoding, and a
    # huge unknown key should not be echoed whole.
    if not isinstance(part, str):
        return part
    part = part.encode("utf-8", "replace").decode("utf-8")
    return part[:limit]


@app.exception_handler(RequestValidationError)
def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # Same `{"detail": [...]}` shape as the default handler, minus `input` and `ctx`, which echo
    # the rejected value (a token, a caption) back and into logs.
    detail = [
        {
            "type": err["type"],
            "loc": [_clean(p, 100) for p in err["loc"]],
            "msg": _clean(err["msg"]),
        }
        for err in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sync", response_model=SyncResponse)
def sync(payload: SyncPayload, background: BackgroundTasks) -> SyncResponse:
    cid, skipped = db.save_sync(payload)
    # Chunking and embedding run after the response is sent; their failures never fail the sync.
    background.add_task(ingest.ingest_course, cid)
    # Counts what was received, not what was newly stored.
    return SyncResponse(
        course_id=cid,
        lectures_received=len(payload.lectures),
        files_received=len(payload.files),
        lectures_skipped_other_course=skipped,
        status="accepted",
    )


@app.get("/courses", response_model=list[CourseSummary])
def courses() -> list[CourseSummary]:
    return db.list_courses()


@app.exception_handler(answer.AnswerNotConfigured)
def answer_not_configured(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "answer service not configured"})


@app.exception_handler(answer.AnswerUnavailable)
def answer_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": "answer service unavailable"})


@app.exception_handler(answer.SearchUnavailable)
def search_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "search service unavailable"})


@app.get("/courses/{course_id}/lectures", response_model=list[LectureStatus])
def course_lectures(course_id: str) -> list[LectureStatus]:
    if not db.course_exists(course_id):
        raise HTTPException(status_code=404, detail="course not synced")
    return db.list_lecture_statuses(course_id)


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    if not db.course_exists(req.course_id):
        raise HTTPException(status_code=404, detail="course not synced")
    return answer.answer_question(req.course_id, req.question, req.top_k)
