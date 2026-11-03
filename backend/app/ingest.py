"""Turn stored captions into embedded chunks. Runs in the background after /sync.

Nothing here may raise out of ingest_course, and nothing logs caption text: failures are logged
as the lecture id (or course id) and the exception type only.
"""

import logging

from . import db, embeddings
from .chunking import chunk_cues
from .config import settings
from .srt import parse_srt_with_stats

log = logging.getLogger("aic")


class EmbeddingDimensionError(ValueError):
    """The embedder returned vectors that do not match the vector column."""


def ingest_lecture(entry_id: str) -> None:
    captions = db.lecture_captions(entry_id)
    if captions is None:
        return
    digest = db.captions_hash(captions)
    cues, dropped = parse_srt_with_stats(captions)
    if dropped:
        log.warning("lecture %s: %d malformed caption blocks dropped", entry_id, dropped)
    if not cues:
        log.warning("lecture %s: captions have no cues; stored with 0 chunks", entry_id)
    chunks = chunk_cues(cues)
    # No database transaction is open while the (slow) embedding runs.
    vectors = embeddings.embed([c.text for c in chunks])
    if len(vectors) != len(chunks) or any(len(v) != settings.embedding_dim for v in vectors):
        raise EmbeddingDimensionError
    db.replace_chunks(entry_id, digest, chunks, vectors)


def ingest_course(course_id: str) -> None:
    """Chunk every lecture of the course that is due, then recompute `indexed`."""
    try:
        for entry_id in db.due_lecture_ids(course_id):
            try:
                ingest_lecture(entry_id)
            except embeddings.ModelLoadError as exc:
                # The next lecture would fail the same way. Stop; they stay due for the next sync.
                log.error("ingestion stopped: embedding model not loaded (%s)", exc.cause)
                break
            except Exception as exc:
                log.error("ingestion of lecture %s failed: %s", entry_id, type(exc).__name__)
        db.refresh_indexed(course_id)
    except Exception as exc:
        log.error("ingestion of course %s failed: %s", course_id, type(exc).__name__)
