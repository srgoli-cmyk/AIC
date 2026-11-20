-- Runs once when the Postgres container is first created, and is also applied by the backend at
-- startup and on first database use (app/db.py), so every statement must stay idempotent.
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

-- Derived from the title at sync time (see app/titles.py) and the hash of the captions the
-- current chunks were built from (NULL until chunked). Added after the table first shipped.
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS lecture_no   INTEGER;
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS section      TEXT;
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS chunked_hash TEXT;

-- Course details for the course page, and when each recording started (Kaltura createdAt), which
-- the page uses to show the usual meeting days and time. Added after the tables first shipped.
ALTER TABLE courses  ADD COLUMN IF NOT EXISTS term_start     DATE;
ALTER TABLE courses  ADD COLUMN IF NOT EXISTS term_end       DATE;
ALTER TABLE courses  ADD COLUMN IF NOT EXISTS instructors    TEXT[];
ALTER TABLE courses  ADD COLUMN IF NOT EXISTS sections       TEXT[];
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS recorded_start TIMESTAMPTZ;

-- Course files (metadata only). A sync never deletes rows here.
CREATE TABLE IF NOT EXISTS files (
    canvas_file_id BIGINT PRIMARY KEY,
    course_id      TEXT NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    content_type   TEXT
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

-- Course documents (slides and notes as PDF or PowerPoint) and their search chunks. `source` is
-- "canvas" (a Canvas file) or "web" (found on the course's own website). `version` is whatever
-- tells the sender the file has not changed; `page` is the 1-based page or slide.
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,               -- "canvas-file-<id>" or "web-<hash of url>"
    course_id    TEXT NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    source       TEXT NOT NULL,
    name         TEXT NOT NULL,
    url          TEXT NOT NULL,
    version      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    pages        INTEGER NOT NULL,
    lecture_no   INTEGER,
    synced_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id         BIGSERIAL PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page       INTEGER NOT NULL,
    text       TEXT NOT NULL,
    embedding  vector(384)
);

CREATE INDEX IF NOT EXISTS documents_course_idx ON documents(course_id);
CREATE INDEX IF NOT EXISTS doc_chunks_doc_idx ON doc_chunks(doc_id);
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_idx ON doc_chunks USING hnsw (embedding vector_cosine_ops);

-- The course's own website, once a search matched it or the student picked it, and how its
-- last sync went. Its material is stored as documents with source "web".
CREATE TABLE IF NOT EXISTS course_sites (
    course_id   TEXT PRIMARY KEY REFERENCES courses(course_id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    chosen_by   TEXT NOT NULL,              -- "search" (matched without asking) or "student"
    status      TEXT NOT NULL,              -- "syncing", "done" or "failed"
    found       INTEGER NOT NULL DEFAULT 0,
    stored      INTEGER NOT NULL DEFAULT 0,
    unchanged   INTEGER NOT NULL DEFAULT 0,
    unreadable  INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL
);
