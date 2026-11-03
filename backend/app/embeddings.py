"""Sentence embeddings. sentence-transformers is imported lazily so the app, and the default test
run, work without the heavy ML dependencies installed."""

import threading

from .config import settings

_model = None
_lock = threading.Lock()  # guards loading, and serializes inference on the shared model


class ModelLoadError(Exception):
    """The embedding model could not be loaded (missing package, download failure, ...). Unlike
    a failure on one lecture, retrying the next lecture would fail the same way. `cause` is the
    type name of the underlying error; its message is dropped on purpose."""

    def __init__(self, cause: str):
        super().__init__(cause)
        self.cause = cause


def _load():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415 (lazy)

            _model = SentenceTransformer(settings.embedding_model)
        except Exception as exc:
            raise ModelLoadError(type(exc).__name__) from None
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts: one normalized vector per text, in order. The model is loaded on
    first use and shared by the whole process."""
    if not texts:
        return []
    with _lock:
        vectors = _load().encode(
            texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False
        )
    return [[float(x) for x in v] for v in vectors]


# bge models are trained to retrieve passages for a question written with this prefix. Passages
# themselves are embedded without it (see embed).
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def embed_query(question: str) -> list[float]:
    """Embed a search question with the model's query instruction. Shares the one loaded model."""
    return embed([QUERY_PREFIX + question])[0]
