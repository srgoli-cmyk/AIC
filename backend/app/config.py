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
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    # Optional: a stronger model for the study tools only (lessons, flashcards, practice
    # problems). Empty uses openrouter_model.
    openrouter_study_model: str = os.getenv("OPENROUTER_STUDY_MODEL", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "384"))
    cors_origin_regex: str = os.getenv(
        "CORS_ORIGIN_REGEX",
        r"^(chrome-extension://[a-p]{32}|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?)$",
    )


settings = Settings()
