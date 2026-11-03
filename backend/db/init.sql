-- Runs once when the Postgres container is first created.
-- Owned by the backend; schema changes after step 2 go through migrations or a fresh `make db-reset`.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS courses (
    course_id        TEXT PRIMARY KEY,           -- "canvas-<id>"
    canvas_course_id INTEGER NOT NULL UNIQUE,
    code             TEXT NOT NULL,
    name             TEXT NOT NULL,
    term             TEXT,
    synced_at        TIMESTAMPTZ NOT NULL,
    indexed          BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS lectures (
    entry_id      TEXT PRIMARY KEY,               -- Kaltura entry id
    course_id     TEXT NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    recorded_at   DATE,
    duration_sec  REAL NOT NULL,
    media_url     TEXT NOT NULL,
    captions_srt  TEXT NOT NULL,
    summary       TEXT
);

-- 384 = BAAI/bge-small-en-v1.5. Change together with EMBEDDING_DIM.
CREATE TABLE IF NOT EXISTS chunks (
    id         BIGSERIAL PRIMARY KEY,
    entry_id   TEXT NOT NULL REFERENCES lectures(entry_id) ON DELETE CASCADE,
    start_sec  REAL NOT NULL,
    end_sec    REAL NOT NULL,
    text       TEXT NOT NULL,
    embedding  vector(384)
);

CREATE INDEX IF NOT EXISTS chunks_entry_idx ON chunks(entry_id);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
