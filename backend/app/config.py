import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# .env lives at the repo root, one level above backend/
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql://aic:aic@localhost:5432/aic")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "384"))
    cors_origin_regex: str = os.getenv(
        "CORS_ORIGIN_REGEX",
        r"^(chrome-extension://[a-p]{32}|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?)$",
    )


settings = Settings()
