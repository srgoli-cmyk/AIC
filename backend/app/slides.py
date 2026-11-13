"""Course documents: slides and notes as PDF or PowerPoint, from Canvas or a course website.

A document is read into text page by page (a slide is a page), cut into search chunks that keep
their page number, and embedded in the background, so /ask can cite `[Slides: name, p. 3]` next
to lecture times.

Nothing here logs a file name's contents, page text or bytes. Failures are logged as the document
id and an error type only.
"""

import hashlib
import io
import logging
import re
import zipfile
from dataclasses import dataclass

from . import db, embeddings
from .config import settings

log = logging.getLogger("aic")

MAX_BYTES = 40 * 1024 * 1024
MAX_PAGES = 400
# A slide is a few hundred characters; a page of typed notes is about 3,000.
MAX_PAGE_CHARS = 20_000
MAX_TOTAL_CHARS = 1_500_000
# Close to what the embedding model reads (512 tokens), like a lecture chunk.
CHUNK_CHARS = 1500
# A page with less text than this is a picture or a title; nothing to search.
MIN_PAGE_CHARS = 20
EMBED_BATCH = 32

PDF = "pdf"
PPTX = "pptx"


class UnreadableDocument(Exception):
    """Not a PDF or PowerPoint we can read, or it holds no text (scanned pages)."""


@dataclass(frozen=True)
class PageChunk:
    page: int  # 1-based page or slide number
    text: str


def kind_of(data: bytes) -> str | None:
    """PDF or PPTX by the file's own first bytes; the name and content type are not trusted."""
    if data[:5] == b"%PDF-":
        return PDF
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "ppt/presentation.xml" in z.namelist():
                    return PPTX
        except zipfile.BadZipFile:
            return None
    return None


def _tidy(text: str) -> str:
    # NUL cannot be stored; runs of spaces and blank lines carry nothing.
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()[:MAX_PAGE_CHARS]


def _pdf_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader  # noqa: PLC0415 (lazy: only needed when a file arrives)

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise UnreadableDocument
    pages = []
    for page in reader.pages[:MAX_PAGES]:
        try:
            pages.append(_tidy(page.extract_text() or ""))
        except Exception:  # one broken page should not lose the rest
            pages.append("")
    return pages


def _pptx_pages(data: bytes) -> list[str]:
    from pptx import Presentation  # noqa: PLC0415 (lazy)

    def shape_text(shape) -> list[str]:
        out = []
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            out.append(shape.text_frame.text)
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                out.append(" | ".join(cell.text for cell in row.cells))
        for inner in getattr(shape, "shapes", []) or []:  # grouped shapes
            out.extend(shape_text(inner))
        return out

    pages = []
    for slide in list(Presentation(io.BytesIO(data)).slides)[:MAX_PAGES]:
        parts = [t for shape in slide.shapes for t in shape_text(shape)]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"Notes: {notes}")
        pages.append(_tidy("\n".join(p for p in parts if p and p.strip())))
    return pages


def extract_pages(data: bytes) -> list[str]:
    """The text of each page or slide, in order ("" for one with none). Raises
    UnreadableDocument for anything that is not a readable PDF or PowerPoint."""
    kind = kind_of(data)
    if kind is None:
        raise UnreadableDocument
    try:
        return _pdf_pages(data) if kind == PDF else _pptx_pages(data)
    except UnreadableDocument:
        raise
    except Exception as exc:
        log.warning("document could not be read (%s)", type(exc).__name__)
        raise UnreadableDocument from None


_BREAK = re.compile(r"\n\n|(?<=[.?!])\s+|\n")


def _split(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Pieces of at most `limit` characters, cut at a paragraph, sentence or line where one
    exists."""
    pieces = []
    while len(text) > limit:
        cut = max((m.end() for m in _BREAK.finditer(text, 0, limit)), default=0)
        if cut < limit // 3:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return [p for p in pieces if p]


def chunk_pages(pages: list[str]) -> list[PageChunk]:
    """Search chunks that keep their page number. Pages with almost no text are left out, and
    the whole document stops at MAX_TOTAL_CHARS."""
    chunks: list[PageChunk] = []
    total = 0
    for number, text in enumerate(pages, start=1):
        if len(text) < MIN_PAGE_CHARS:
            continue
        for piece in _split(text):
            total += len(piece)
            if total > MAX_TOTAL_CHARS:
                return chunks
            chunks.append(PageChunk(number, piece))
    return chunks


def display_name(name: str) -> str:
    """The file name as it is cited: no extension, no brackets (they would end a citation), one
    line. `Lecture 5 [draft].pdf` becomes `Lecture 5 draft`."""
    name = re.sub(r"\.(pdf|pptx?|key)$", "", name.strip(), flags=re.IGNORECASE)
    name = re.sub(r"[\[\]\x00-\x1f]", " ", name)
    return " ".join(name.split())[:200] or "Slides"


def store(
    course_id: str, doc_id: str, *, source: str, name: str, url: str, version: str, data: bytes
) -> tuple[int, int]:
    """Read the file and store its chunks without embeddings (embed_document adds them).
    Returns (pages, chunks). Raises UnreadableDocument; a document with no text at all is one."""
    pages = extract_pages(data)
    chunks = chunk_pages(pages)
    if not chunks:
        raise UnreadableDocument
    db.save_document(
        course_id,
        doc_id,
        source=source,
        name=display_name(name),
        url=url,
        version=version,
        content_hash=hashlib.sha256(data).hexdigest(),
        pages=len(pages),
        chunks=[(c.page, c.text) for c in chunks],
    )
    return len(pages), len(chunks)


def embed_document(doc_id: str) -> None:
    """Embed the document's chunks that have no vector yet. Runs in the background after an
    upload; never raises."""
    try:
        while True:
            pending = db.doc_chunks_without_embedding(doc_id, EMBED_BATCH)
            if not pending:
                return
            vectors = embeddings.embed([text for _, text in pending])
            if len(vectors) != len(pending) or any(
                len(v) != settings.embedding_dim for v in vectors
            ):
                raise ValueError("embedding dimension")
            db.set_doc_embeddings(
                [(chunk_id, v) for (chunk_id, _), v in zip(pending, vectors, strict=True)]
            )
    except embeddings.ModelLoadError as exc:
        log.error("document %s not embedded: embedding model not loaded (%s)", doc_id, exc.cause)
    except Exception as exc:
        log.error("document %s not embedded: %s", doc_id, type(exc).__name__)
