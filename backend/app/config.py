import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# .env lives at the repo root, one level above backend/
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass(frozen=True)
class Settings:
    # repr=False: a repr of Settings (e.g. in an assertion failure or a log line) must not print
    # the database password or the API key.
    database_url: str = field(
        default=os.getenv("DATABASE_URL", "postgresql://aic:aic@localhost:5432/aic"), repr=False
    )
    openrouter_api_key: str = field(default=os.getenv("OPENROUTER_API_KEY", ""), repr=False)
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5")
    # Optional: a stronger model for the study tools only (lessons, flashcards, practice
    # problems). Empty uses openrouter_model.
    openrouter_study_model: str = os.getenv("OPENROUTER_STUDY_MODEL", "")
    # The model that works a lesson's problems and the practice problems again to check them. A
    # reasoning model: the writing model gets matrix arithmetic wrong far more often.
    openrouter_check_model: str = os.getenv("OPENROUTER_CHECK_MODEL", "openai/gpt-5.6-luna")
    # Reads scanned PDFs (pages that are pictures of text): a vision model that writes out each
    # page, math as LaTeX. Only used when a PDF has almost no text of its own.
    openrouter_ocr_model: str = os.getenv("OPENROUTER_OCR_MODEL", "google/gemini-3.5-flash")
    # Reasoning per model: empty (default, send nothing), off, or an effort level; see
    # reasoning_payload. Reasoning models can spend the whole token limit thinking and come back
    # cut off, so `off` is the usual fix for the answer model. The default is `off`; getenv only
    # uses it when the variable is unset, so an explicit `OPENROUTER_REASONING=` stays empty
    # (the model's default).
    openrouter_reasoning: str = os.getenv("OPENROUTER_REASONING", "off")  # OPENROUTER_MODEL
    # The study writer. Empty here still means the default; the fallback to openrouter_reasoning
    # when OPENROUTER_STUDY_MODEL is empty is in study._call.
    openrouter_study_reasoning: str = os.getenv("OPENROUTER_STUDY_REASONING", "")
    openrouter_check_reasoning: str = os.getenv("OPENROUTER_CHECK_REASONING", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "384"))
    cors_origin_regex: str = os.getenv(
        "CORS_ORIGIN_REGEX",
        r"^(chrome-extension://[a-p]{32}|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?)$",
    )


settings = Settings()

REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
_warned: set[str] = set()  # setting names already warned about


def reasoning_payload(value: str, setting_name: str) -> dict | None:
    """The OpenRouter `reasoning` object for a setting's value, or None to send no such key.
    "" is the default (None), "off" is {"enabled": false}, an effort level is {"effort": level}.
    Case and surrounding spaces are ignored. An unknown value is logged (the setting's name only,
    never the value) and treated as the default."""
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    if cleaned == "off":
        return {"enabled": False}
    if cleaned in REASONING_EFFORTS:
        return {"effort": cleaned}
    # Once per setting name per process, so a bad value does not fill the log on every request.
    if setting_name not in _warned:
        _warned.add(setting_name)
        logging.getLogger("aic").warning(
            "%s has an unknown value; using the model's default", setting_name
        )
    return None
