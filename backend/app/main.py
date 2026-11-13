"""FastAPI app. Sync and courses are stored in Postgres (app/db.py); after a sync, captions are
chunked and embedded in the background (app/ingest.py); /ask answers from the stored chunks
(app/answer.py); /study writes a lesson, flashcards or practice problems from one lecture
(app/study.py)."""

import logging
import re
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import InterfaceError, OperationalError

from . import answer, coursesite, db, ingest, slides, study
from .config import settings
from .schemas import (
    AskRequest,
    AskResponse,
    CardsResponse,
    CourseDetail,
    CourseSummary,
    DocumentInfo,
    DocumentStored,
    LectureStatus,
    LessonResponse,
    PracticeResponse,
    SiteCandidate,
    SiteInfo,
    SitePick,
    SiteSearch,
    StudyRequest,
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
        db.rename_documents(slides.display_name)
    except DB_DOWN:
        log.warning("database unavailable at startup; schema will be applied on first use")
    yield


app = FastAPI(title="AI Lecture Search", version="0.0.1", lifespan=lifespan)

# The extension calls us from its background service worker (host_permissions cover
# localhost), so CORS mainly matters for the dev UI and console snippets.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.cors_origin_regex,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


def _trusted_origin(request: Request) -> None:
    """Calls that change or cost something are refused from other web pages. A browser sends
    `Origin` with a cross-site request, and a POST with no body needs no CORS preflight, so CORS
    alone would not stop a page from starting a paid web search or a delete."""
    origin = request.headers.get("origin")
    if origin and not re.fullmatch(settings.cors_origin_regex, origin):
        raise HTTPException(status_code=403, detail="origin not allowed")


TRUSTED = [Depends(_trusted_origin)]


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


@app.get("/courses/{course_id}", response_model=CourseDetail)
def course_detail(course_id: str) -> CourseDetail:
    course = db.get_course(course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="course not synced")
    return course


@app.get("/courses/{course_id}/lectures", response_model=list[LectureStatus])
def course_lectures(course_id: str) -> list[LectureStatus]:
    if not db.course_exists(course_id):
        raise HTTPException(status_code=404, detail="course not synced")
    return db.list_lecture_statuses(course_id)


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    if not db.course_exists(req.course_id):
        raise HTTPException(status_code=404, detail="course not synced")
    if req.entry_id is None:
        return answer.answer_question(req.course_id, req.question, req.top_k)
    return answer.answer_question(req.course_id, req.question, req.top_k, entry_id=req.entry_id)


@app.get("/courses/{course_id}/documents", response_model=list[DocumentInfo])
def course_documents(course_id: str) -> list[DocumentInfo]:
    if not db.course_exists(course_id):
        raise HTTPException(status_code=404, detail="course not synced")
    return db.list_documents(course_id)


@app.put(
    "/courses/{course_id}/documents/canvas/{canvas_file_id}",
    response_model=DocumentStored,
    dependencies=TRUSTED,
)
async def put_canvas_document(
    course_id: str,
    canvas_file_id: int,
    request: Request,
    background: BackgroundTasks,
    name: str = Query(min_length=1, max_length=300),
    version: str = Query(min_length=1, max_length=100),
) -> DocumentStored:
    """One Canvas file (a PDF or PowerPoint) as the raw request body. The extension sends each
    slide deck here after a course sync; the text is read now and embedded in the background."""
    if canvas_file_id < 1 or canvas_file_id > 2**53:
        raise HTTPException(status_code=422, detail="invalid file id")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > slides.MAX_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > slides.MAX_BYTES:
            raise HTTPException(status_code=413, detail="file too large")
    return await run_in_threadpool(
        _store_canvas_document, course_id, canvas_file_id, name, version, bytes(data), background
    )


# --- the course's own website ---

_site_jobs: set[str] = set()
_site_jobs_lock = threading.Lock()


def _sync_site_job(course_id: str, url: str) -> None:
    """Background: read the site's lecture material into the course's documents. One job per
    course at a time; progress is written to course_sites as it goes. A job stops as soon as the
    site is forgotten or replaced, and a site chosen while a job was running is read after it."""
    with _site_jobs_lock:
        if course_id in _site_jobs:
            return  # the running job picks up the new choice when it ends
        _site_jobs.add(course_id)

    def counts(out) -> dict[str, int]:
        keys = ("found", "stored", "unchanged", "unreadable", "failed")
        return {k: getattr(out, k) for k in keys}

    def progress(out) -> None:
        if not db.update_site(course_id, url, "syncing", counts(out)):
            raise coursesite.SiteChanged

    try:
        while True:
            try:
                out = coursesite.sync_site(course_id, url, progress=progress)
                db.update_site(course_id, url, "done", counts(out))
            except coursesite.SiteChanged:
                log.info("course site sync stopped: the site changed")
            except Exception as exc:  # a background job must record its failure, not raise it
                log.error("course site sync failed: %s", type(exc).__name__)
                db.update_site(course_id, url, "failed", {})
            with _site_jobs_lock:
                site = db.get_site(course_id)
                if site is None:
                    # Forgotten mid-sync: a file stored after the forget must not stay behind.
                    db.delete_web_documents_except(course_id, set())
                if site is None or site.url == url or site.status != "syncing":
                    _site_jobs.discard(course_id)
                    return
                url = site.url
    except BaseException:
        with _site_jobs_lock:
            _site_jobs.discard(course_id)
        raise


def _facts_or_404(course_id: str) -> coursesite.CourseFacts:
    facts = coursesite.facts_for(course_id)
    if facts is None:
        raise HTTPException(status_code=404, detail="course not synced")
    return facts


@app.get("/courses/{course_id}/site", response_model=SiteInfo | None)
def course_site(course_id: str) -> SiteInfo | None:
    """The course's website and how its sync is going, or null when none is chosen yet."""
    _facts_or_404(course_id)
    return db.get_site(course_id)


@app.post("/courses/{course_id}/site/search", response_model=SiteSearch, dependencies=TRUSTED)
def search_course_site(course_id: str, background: BackgroundTasks) -> SiteSearch:
    """Search the web for the course's own website and check each result against what Canvas
    says about the course. A page that matches (course, term, school, and off ucsd.edu also the
    lecture calendar) is used right away unless the student already picked one; pages that
    might be it are returned for the student to decide. Takes up to a minute."""
    facts = _facts_or_404(course_id)
    if not settings.openrouter_api_key:
        raise HTTPException(status_code=503, detail="web search needs OPENROUTER_API_KEY")
    verdicts = coursesite.find_course_site(facts)
    current = db.get_site(course_id)
    best = verdicts[0] if verdicts and verdicts[0].decision == coursesite.ACCEPT else None
    if best and not (current and current.chosen_by == "student"):
        if current is None or current.url != best.url or current.status == "failed":
            current = db.set_site(course_id, best.url, best.title, "search")
            background.add_task(_sync_site_job, course_id, best.url)
    candidates = [
        SiteCandidate(
            url=v.url,
            title=v.title,
            decision=v.decision,
            term_found=v.term_found,
            reasons=v.reasons,
        )
        for v in verdicts
        if v.decision in (coursesite.ACCEPT, coursesite.ASK)
    ][:5]
    return SiteSearch(site=current, candidates=candidates)


@app.put("/courses/{course_id}/site", response_model=SiteInfo, dependencies=TRUSTED)
def pick_course_site(course_id: str, pick: SitePick, background: BackgroundTasks) -> SiteInfo:
    """The student's choice of website (usually one the search asked about). It is checked again
    here: a page that is not this course's is refused."""
    facts = _facts_or_404(course_id)
    verdict = coursesite.check_site(facts, pick.url)
    if verdict.decision == coursesite.REJECT:
        raise HTTPException(
            status_code=422, detail="not this course's website: " + "; ".join(verdict.reasons)
        )
    site = db.set_site(course_id, verdict.url, verdict.title, "student")
    background.add_task(_sync_site_job, course_id, verdict.url)
    return site


@app.post("/courses/{course_id}/site/sync", response_model=SiteInfo, dependencies=TRUSTED)
def resync_course_site(course_id: str, background: BackgroundTasks) -> SiteInfo:
    """Read the chosen site again for new or changed material."""
    _facts_or_404(course_id)
    site = db.get_site(course_id)
    if site is None:
        raise HTTPException(status_code=404, detail="no website chosen")
    db.update_site(course_id, site.url, "syncing", {})
    background.add_task(_sync_site_job, course_id, site.url)
    return db.get_site(course_id)


@app.delete("/courses/{course_id}/site", status_code=204, dependencies=TRUSTED)
def forget_course_site(course_id: str) -> Response:
    """Not this site: forget it and remove what was read from it."""
    _facts_or_404(course_id)
    db.delete_site(course_id)
    return Response(status_code=204)


@app.delete(
    "/courses/{course_id}/documents/canvas/{canvas_file_id}", status_code=204, dependencies=TRUSTED
)
def delete_canvas_document(course_id: str, canvas_file_id: int) -> Response:
    """Drop a Canvas file the extension found a newer copy of (`Week 3_1015.pdf` replacing
    `Week 3.pdf`), so an old revision is not searched next to the new one."""
    if canvas_file_id < 1 or canvas_file_id > 2**53:
        raise HTTPException(status_code=422, detail="invalid file id")
    if not db.delete_document(course_id, f"canvas-file-{canvas_file_id}"):
        raise HTTPException(status_code=404, detail="document not found")
    return Response(status_code=204)


def _store_canvas_document(
    course_id: str,
    canvas_file_id: int,
    name: str,
    version: str,
    data: bytes,
    background: BackgroundTasks,
) -> DocumentStored:
    course = db.get_course(course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="course not synced")
    doc_id = f"canvas-file-{canvas_file_id}"
    # The link is built here from ids, never taken from the request.
    url = f"https://canvas.ucsd.edu/courses/{course.canvas_course_id}/files/{canvas_file_id}"
    try:
        pages, chunks = slides.store(
            course_id, doc_id, source="canvas", name=name, url=url, version=version, data=data
        )
    except slides.UnreadableDocument:
        raise HTTPException(status_code=422, detail="file could not be read") from None
    background.add_task(slides.embed_document, doc_id)
    return DocumentStored(doc_id=doc_id, pages=pages, chunks=chunks, status="stored")


@app.exception_handler(study.LectureNotIndexed)
def lecture_not_indexed(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "lecture not indexed"})


@app.post("/study", response_model=LessonResponse | CardsResponse | PracticeResponse)
def study_lecture(req: StudyRequest) -> LessonResponse | CardsResponse | PracticeResponse:
    if not db.lecture_exists(req.entry_id):
        raise HTTPException(status_code=404, detail="lecture not synced")
    return study.study_lecture(req.entry_id, req.kind)


# The search page (web/) at /app/, so the extension can link to a course on the same origin.
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
if WEB_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="app")
