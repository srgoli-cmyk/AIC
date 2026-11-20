-- Runs once when the Postgres container is first created, and is also applied by the backend at
-- startup and on first database use (app/db.py), so every statement must stay idempotent.
CREATE EXTENSION IF NOT EXISTS vector;

-- Authenticated UCSD students. No passwords or Google OAuth access/refresh tokens are stored;
-- Google verifies identity and `google_sub` is Google's permanent, non-secret subject id.
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,              -- opaque "usr_<random>" id
    google_sub    TEXT NOT NULL UNIQUE,
    email         TEXT NOT NULL UNIQUE,          -- lowercased @ucsd.edu email
    name          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    last_login_at TIMESTAMPTZ NOT NULL
);

-- Active web cookie sessions ("web") and Chrome extension bearer tokens ("extension").
-- Only the SHA-256 hash of each 256-bit random token is stored so a DB dump cannot hijack logins.
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,                 -- opaque "ses_<random>" id
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL CHECK (kind IN ('web', 'extension')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions(expires_at);

-- Short-lived single-use codes handed to the Chrome extension login tab and exchanged once for a
-- bearer token. Only the SHA-256 hash of the code is stored.
CREATE TABLE IF NOT EXISTS auth_exchange_codes (
    code_hash  TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    -- base64url(SHA-256(nonce)) the extension sent at login. Rows without it (made before this
    -- column existed) are never accepted.
    nonce_hash TEXT
);

ALTER TABLE auth_exchange_codes ADD COLUMN IF NOT EXISTS nonce_hash TEXT;

CREATE INDEX IF NOT EXISTS auth_exchange_codes_expires_idx ON auth_exchange_codes(expires_at);

-- Default local student account to support backwards-compatible dev/tests and composite foreign keys
INSERT INTO users (id, google_sub, email, name, created_at, last_login_at)
VALUES ('usr_local', 'sub_local', 'local@ucsd.edu', 'Local Student', now(), now())
ON CONFLICT (id) DO NOTHING;

-- All course data below is scoped by `user_id` using composite primary/foreign keys so two
-- students syncing the same Canvas class keep completely isolated courses, lectures, and slides.
CREATE TABLE IF NOT EXISTS courses (
    user_id          TEXT NOT NULL DEFAULT 'usr_local' REFERENCES users(id) ON DELETE CASCADE,
    course_id        TEXT NOT NULL,              -- "canvas-<id>"
    canvas_course_id INTEGER NOT NULL,
    code             TEXT NOT NULL,
    name             TEXT NOT NULL,
    term             TEXT,
    term_start       DATE,
    term_end         DATE,
    instructors      TEXT[],
    sections         TEXT[],
    synced_at        TIMESTAMPTZ NOT NULL,
    indexed          BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (user_id, course_id),
    UNIQUE (user_id, canvas_course_id)
);

CREATE TABLE IF NOT EXISTS lectures (
    user_id        TEXT NOT NULL DEFAULT 'usr_local',
    entry_id       TEXT NOT NULL,                -- Kaltura entry id
    course_id      TEXT NOT NULL,
    title          TEXT NOT NULL,
    lecture_no     INTEGER,
    section        TEXT,
    recorded_at    DATE,
    recorded_start TIMESTAMPTZ,
    duration_sec   REAL NOT NULL,
    media_url      TEXT NOT NULL,
    captions_srt   TEXT NOT NULL,
    summary        TEXT,
    chunked_hash   TEXT,
    PRIMARY KEY (user_id, entry_id),
    FOREIGN KEY (user_id, course_id) REFERENCES courses(user_id, course_id) ON DELETE CASCADE
);

-- Course files (metadata only). A sync never deletes rows here.
CREATE TABLE IF NOT EXISTS files (
    user_id        TEXT NOT NULL DEFAULT 'usr_local',
    canvas_file_id BIGINT NOT NULL,
    course_id      TEXT NOT NULL,
    name           TEXT NOT NULL,
    content_type   TEXT,
    PRIMARY KEY (user_id, canvas_file_id),
    FOREIGN KEY (user_id, course_id) REFERENCES courses(user_id, course_id) ON DELETE CASCADE
);

-- 384 = BAAI/bge-small-en-v1.5. Change together with EMBEDDING_DIM.
CREATE TABLE IF NOT EXISTS chunks (
    id         BIGSERIAL PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'usr_local',
    entry_id   TEXT NOT NULL,
    start_sec  REAL NOT NULL,
    end_sec    REAL NOT NULL,
    text       TEXT NOT NULL,
    embedding  vector(384),
    FOREIGN KEY (user_id, entry_id) REFERENCES lectures(user_id, entry_id) ON DELETE CASCADE
);

-- (The (user_id, ...) b-tree indexes are created at the end, after the migration below, because on
-- an older database the user_id column does not exist yet when the tables are declared.)
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- Course documents (slides and notes as PDF or PowerPoint) and their search chunks. `source` is
-- "canvas" (a Canvas file) or "web" (found on the course's own website). `version` is whatever
-- tells the sender the file has not changed; `page` is the 1-based page or slide.
CREATE TABLE IF NOT EXISTS documents (
    user_id      TEXT NOT NULL DEFAULT 'usr_local',
    doc_id       TEXT NOT NULL,                  -- "canvas-file-<id>" or "web-<hash of url>"
    course_id    TEXT NOT NULL,
    source       TEXT NOT NULL,
    name         TEXT NOT NULL,
    url          TEXT NOT NULL,
    version      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    pages        INTEGER NOT NULL,
    lecture_no   INTEGER,
    synced_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, doc_id),
    FOREIGN KEY (user_id, course_id) REFERENCES courses(user_id, course_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id         BIGSERIAL PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'usr_local',
    doc_id     TEXT NOT NULL,
    page       INTEGER NOT NULL,
    text       TEXT NOT NULL,
    embedding  vector(384),
    FOREIGN KEY (user_id, doc_id) REFERENCES documents(user_id, doc_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS doc_chunks_embedding_idx ON doc_chunks USING hnsw (embedding vector_cosine_ops);

-- The course's own website, once a search matched it or the student picked it, and how its
-- last sync went. Its material is stored as documents with source "web".
CREATE TABLE IF NOT EXISTS course_sites (
    user_id     TEXT NOT NULL DEFAULT 'usr_local',
    course_id   TEXT NOT NULL,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    chosen_by   TEXT NOT NULL,              -- "search" (matched without asking) or "student"
    status      TEXT NOT NULL,              -- "syncing", "done" or "failed"
    found       INTEGER NOT NULL DEFAULT 0,
    stored      INTEGER NOT NULL DEFAULT 0,
    unchanged   INTEGER NOT NULL DEFAULT 0,
    unreadable  INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, course_id),
    FOREIGN KEY (user_id, course_id) REFERENCES courses(user_id, course_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------------------------
-- Migration of databases created from the older schema. `CREATE TABLE IF NOT EXISTS` above does
-- nothing on an existing table, so everything below brings an old database up to the same shape
-- as a fresh one. All of it is a no-op on a fresh or already migrated database, and it keeps
-- every row and embedding.
-- ---------------------------------------------------------------------------------------------

-- Columns added after the tables first shipped (lecture numbering, chunk hash, course details).
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS lecture_no     INTEGER;
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS section        TEXT;
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS chunked_hash   TEXT;
ALTER TABLE lectures ADD COLUMN IF NOT EXISTS recorded_start TIMESTAMPTZ;
ALTER TABLE courses  ADD COLUMN IF NOT EXISTS term_start     DATE;
ALTER TABLE courses  ADD COLUMN IF NOT EXISTS term_end       DATE;
ALTER TABLE courses  ADD COLUMN IF NOT EXISTS instructors    TEXT[];
ALTER TABLE courses  ADD COLUMN IF NOT EXISTS sections       TEXT[];

-- Per-student ownership: every existing row becomes the local student's ('usr_local' is seeded
-- above).
ALTER TABLE courses      ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'usr_local';
ALTER TABLE lectures     ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'usr_local';
ALTER TABLE files        ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'usr_local';
ALTER TABLE chunks       ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'usr_local';
ALTER TABLE documents    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'usr_local';
ALTER TABLE doc_chunks   ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'usr_local';
ALTER TABLE course_sites ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'usr_local';

-- (The blocks below build statements with || and quote_ident, not format(): the driver treats a
-- percent sign in this file as a parameter placeholder.)
-- Step 1: drop the old keys. Constraints are found by what they cover, not by guessing their
-- default names. Foreign keys go first, because they depend on the primary keys.
DO $$
DECLARE
    r record;
BEGIN
    -- Old single-column foreign keys to courses(course_id), lectures(entry_id), documents(doc_id).
    FOR r IN
        SELECT conrelid::regclass AS tbl, conname
        FROM pg_constraint
        WHERE contype = 'f'
          AND array_length(conkey, 1) = 1
          AND conrelid = ANY (ARRAY[
              'lectures'::regclass::oid, 'files'::regclass::oid, 'documents'::regclass::oid,
              'course_sites'::regclass::oid, 'chunks'::regclass::oid, 'doc_chunks'::regclass::oid])
          AND confrelid = ANY (ARRAY[
              'courses'::regclass::oid, 'lectures'::regclass::oid, 'documents'::regclass::oid])
    LOOP
        EXECUTE 'ALTER TABLE ' || r.tbl::text || ' DROP CONSTRAINT ' || quote_ident(r.conname);
    END LOOP;

    -- Old primary keys and the old UNIQUE(canvas_course_id): any primary key or unique
    -- constraint on these tables that does not include user_id.
    FOR r IN
        SELECT c.conrelid::regclass AS tbl, c.conname
        FROM pg_constraint c
        WHERE c.contype IN ('p', 'u')
          AND c.conrelid = ANY (ARRAY[
              'courses'::regclass::oid, 'lectures'::regclass::oid, 'files'::regclass::oid,
              'documents'::regclass::oid, 'course_sites'::regclass::oid])
          AND NOT EXISTS (
              SELECT 1 FROM pg_attribute a
              WHERE a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) AND a.attname = 'user_id')
    LOOP
        EXECUTE 'ALTER TABLE ' || r.tbl::text || ' DROP CONSTRAINT ' || quote_ident(r.conname);
    END LOOP;
END
$$;

-- Step 2: add the composite keys the CREATE TABLE statements declare, where missing. Primary
-- keys first, since the foreign keys point at them.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('courses', 'user_id, course_id'),
            ('lectures', 'user_id, entry_id'),
            ('files', 'user_id, canvas_file_id'),
            ('documents', 'user_id, doc_id'),
            ('course_sites', 'user_id, course_id')
        ) AS t(tbl, cols)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conrelid = r.tbl::regclass AND contype = 'p'
        ) THEN
            EXECUTE 'ALTER TABLE ' || quote_ident(r.tbl) || ' ADD PRIMARY KEY (' || r.cols || ')';
        END IF;
    END LOOP;

    -- One course per (student, Canvas course): replaces UNIQUE(canvas_course_id).
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'courses'::regclass AND contype = 'u' AND array_length(conkey, 1) = 2
    ) THEN
        ALTER TABLE courses ADD UNIQUE (user_id, canvas_course_id);
    END IF;

    -- courses.user_id -> users(id).
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'courses'::regclass AND contype = 'f'
          AND confrelid = 'users'::regclass
    ) THEN
        ALTER TABLE courses
            ADD FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;

    -- Composite foreign keys (child table, child columns, parent table, parent columns).
    FOR r IN
        SELECT * FROM (VALUES
            ('lectures', 'user_id, course_id', 'courses', 'user_id, course_id'),
            ('files', 'user_id, course_id', 'courses', 'user_id, course_id'),
            ('documents', 'user_id, course_id', 'courses', 'user_id, course_id'),
            ('course_sites', 'user_id, course_id', 'courses', 'user_id, course_id'),
            ('chunks', 'user_id, entry_id', 'lectures', 'user_id, entry_id'),
            ('doc_chunks', 'user_id, doc_id', 'documents', 'user_id, doc_id')
        ) AS t(child, child_cols, parent, parent_cols)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = r.child::regclass AND contype = 'f'
              AND confrelid = r.parent::regclass AND array_length(conkey, 1) = 2
        ) THEN
            EXECUTE 'ALTER TABLE ' || quote_ident(r.child)
                || ' ADD FOREIGN KEY (' || r.child_cols || ') REFERENCES '
                || quote_ident(r.parent) || ' (' || r.parent_cols || ') ON DELETE CASCADE';
        END IF;
    END LOOP;
END
$$;

-- Step 3: the b-tree indexes on (user_id, ...). An old index of the same name that does not
-- cover user_id is dropped first, because CREATE INDEX IF NOT EXISTS would keep it.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT i.indexrelid::regclass AS idx
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE c.relname IN ('chunks_entry_idx', 'documents_course_idx', 'doc_chunks_doc_idx')
          AND c.relnamespace = current_schema()::regnamespace
          AND NOT EXISTS (
              SELECT 1 FROM pg_attribute a
              WHERE a.attrelid = i.indexrelid AND a.attname = 'user_id')
    LOOP
        EXECUTE 'DROP INDEX ' || r.idx::text;
    END LOOP;
END
$$;

CREATE INDEX IF NOT EXISTS chunks_entry_idx ON chunks(user_id, entry_id);
CREATE INDEX IF NOT EXISTS documents_course_idx ON documents(user_id, course_id);
CREATE INDEX IF NOT EXISTS doc_chunks_doc_idx ON doc_chunks(user_id, doc_id);
