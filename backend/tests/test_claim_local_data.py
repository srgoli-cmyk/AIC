"""Tests for the one-time claim of pre-login (usr_local) data by a real account."""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth, auth_db, claim_local_data, db
from app.main import app

pytestmark = pytest.mark.integration

TABLES = auth_db.CLAIM_TABLES
LOCAL = db.LOCAL_USER_ID


def _vec(i: int) -> list[float]:
    v = [0.0] * db.EMBEDDING_DIM
    v[i] = 1.0
    return v


def _seed_course(conn, user_id: str, course_id: str = "canvas-100", canvas_id: int = 100) -> None:
    """One course with 2 lectures, 3 chunks, 1 file, 1 document, 2 doc_chunks and a site."""
    p = {"u": user_id, "c": course_id, "n": canvas_id}
    conn.execute(
        text(
            "INSERT INTO courses (user_id, course_id, canvas_course_id, code, name, synced_at,"
            " indexed) VALUES (:u, :c, :n, 'MATH 20A', 'Calc', now(), true)"
        ),
        p,
    )
    for entry in ("1_aaa", "1_bbb"):
        conn.execute(
            text(
                "INSERT INTO lectures (user_id, entry_id, course_id, title, duration_sec,"
                " media_url, captions_srt, chunked_hash)"
                " VALUES (:u, :e, :c, 'L', 60, 'https://x.test/m', 'cap', 'h')"
            ),
            {**p, "e": entry},
        )
    for i, entry in enumerate(("1_aaa", "1_aaa", "1_bbb")):
        conn.execute(
            text(
                "INSERT INTO chunks (user_id, entry_id, start_sec, end_sec, text, embedding)"
                " VALUES (:u, :e, :s, :s + 45, :t, CAST(:v AS vector))"
            ),
            {**p, "e": entry, "s": i * 45.0, "t": f"chunk {i}", "v": str(_vec(i))},
        )
    conn.execute(
        text(
            "INSERT INTO files (user_id, canvas_file_id, course_id, name)"
            " VALUES (:u, 7, :c, 'a.pdf')"
        ),
        p,
    )
    conn.execute(
        text(
            "INSERT INTO documents (user_id, doc_id, course_id, source, name, url, version,"
            " content_hash, pages, synced_at)"
            " VALUES (:u, 'canvas-file-7', :c, 'canvas', 'a.pdf', 'u', 'v', 'h', 2, now())"
        ),
        p,
    )
    for page in (1, 2):
        conn.execute(
            text(
                "INSERT INTO doc_chunks (user_id, doc_id, page, text, embedding)"
                " VALUES (:u, 'canvas-file-7', :p, 'slide', CAST(:v AS vector))"
            ),
            {**p, "p": page, "v": str(_vec(10 + page))},
        )
    conn.execute(
        text(
            "INSERT INTO course_sites (user_id, course_id, url, title, chosen_by, status,"
            " updated_at) VALUES (:u, :c, 'https://x.test', 'Site', 'student', 'done', now())"
        ),
        p,
    )


def _counts(user_id: str) -> dict[str, int]:
    with db.engine.connect() as conn:
        return {
            t: conn.execute(
                text(f"SELECT count(*) FROM {t} WHERE user_id = :u"),  # noqa: S608
                {"u": user_id},
            ).scalar_one()
            for t in TABLES
        }


EXPECTED = {
    "courses": 1,
    "lectures": 2,
    "files": 1,
    "documents": 1,
    "course_sites": 1,
    "chunks": 3,
    "doc_chunks": 2,
}


@pytest.fixture
def user():
    return auth_db.upsert_user("sub-claim", "Claimer@ucsd.edu", "Claimer")


@pytest.fixture
def seeded(user):
    with db.engine.begin() as conn:
        _seed_course(conn, LOCAL)
    return user


def test_claim_moves_everything(seeded):
    result = auth_db.claim_local_data("claimer@UCSD.edu", dry_run=False)

    assert result["moved"] == EXPECTED
    assert _counts(LOCAL) == dict.fromkeys(TABLES, 0)
    assert _counts(seeded.id) == EXPECTED


def test_claim_waits_for_a_chunk_write_in_flight(seeded):
    """A chunk written for a usr_local lecture by an open transaction must not slip between the
    claim's copy and its delete: the claim waits for that transaction, then moves the new chunk."""
    result: dict = {}
    with db.engine.connect() as writer:
        trans = writer.begin()
        writer.execute(
            text(
                "INSERT INTO chunks (user_id, entry_id, start_sec, end_sec, text, embedding)"
                " VALUES (:u, '1_aaa', 90, 135, 'late chunk', CAST(:v AS vector))"
            ),
            {"u": LOCAL, "v": str(_vec(5))},
        )
        thread = threading.Thread(
            target=lambda: result.update(auth_db.claim_local_data(seeded.email, dry_run=False))
        )
        thread.start()
        thread.join(timeout=1.5)
        assert thread.is_alive(), "the claim should be blocked by the in-flight chunk write"
        trans.commit()
    thread.join(timeout=15)
    assert not thread.is_alive()

    assert result["moved"]["chunks"] == EXPECTED["chunks"] + 1
    assert _counts(LOCAL) == dict.fromkeys(TABLES, 0)
    assert _counts(seeded.id)["chunks"] == EXPECTED["chunks"] + 1


def test_claimed_data_is_searchable_and_listed(seeded):
    auth_db.claim_local_data(seeded.email, dry_run=False)

    hits = db.search_chunks("canvas-100", _vec(1), 3, user_id=seeded.id)
    assert hits[0].text == "chunk 1"  # the embedding survived
    assert db.search_chunks("canvas-100", _vec(1), 3, user_id=LOCAL) == []
    doc_hits = db.search_doc_chunks("canvas-100", _vec(12), 2, user_id=seeded.id)
    assert len(doc_hits) == 2

    token, _ = auth_db.create_session(seeded.id, kind="web", ttl_days=1)
    app.dependency_overrides.pop(auth.require_user, None)
    client = TestClient(app, cookies={auth.SESSION_COOKIE_NAME: token})
    response = client.get("/courses")
    assert response.status_code == 200
    assert [c["course_id"] for c in response.json()] == ["canvas-100"]


def test_dry_run_changes_nothing(seeded):
    result = auth_db.claim_local_data(seeded.email, dry_run=True)

    assert result["moved"] == EXPECTED
    assert _counts(LOCAL) == EXPECTED
    assert _counts(seeded.id) == dict.fromkeys(TABLES, 0)


def test_unknown_email_refused(seeded):
    with pytest.raises(auth_db.ClaimRefused, match="Sign in once"):
        auth_db.claim_local_data("nobody@ucsd.edu", dry_run=False)
    assert _counts(LOCAL) == EXPECTED


def test_local_account_itself_refused(seeded):
    with pytest.raises(auth_db.ClaimRefused):
        auth_db.claim_local_data("local@ucsd.edu", dry_run=False)
    assert _counts(LOCAL) == EXPECTED


def test_course_conflict_refused_and_named(seeded):
    with db.engine.begin() as conn:
        # Same course id under the target, but nothing else, to prove the course key alone blocks.
        conn.execute(
            text(
                "INSERT INTO courses (user_id, course_id, canvas_course_id, code, name, synced_at)"
                " VALUES (:u, 'canvas-100', 100, 'MATH 20A', 'Calc', now())"
            ),
            {"u": seeded.id},
        )
    before = _counts(seeded.id)

    with pytest.raises(auth_db.ClaimRefused, match="canvas-100"):
        auth_db.claim_local_data(seeded.email, dry_run=False)

    assert _counts(LOCAL) == EXPECTED
    assert _counts(seeded.id) == before


def test_lecture_conflict_refused(seeded):
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO courses (user_id, course_id, canvas_course_id, code, name, synced_at)"
                " VALUES (:u, 'canvas-200', 200, 'X', 'X', now())"
            ),
            {"u": seeded.id},
        )
        conn.execute(
            text(
                "INSERT INTO lectures (user_id, entry_id, course_id, title, duration_sec,"
                " media_url, captions_srt) VALUES (:u, '1_aaa', 'canvas-200', 'L', 1, 'm', 'c')"
            ),
            {"u": seeded.id},
        )

    with pytest.raises(auth_db.ClaimRefused, match="canvas-100"):
        auth_db.claim_local_data(seeded.email, dry_run=False)
    assert _counts(LOCAL) == EXPECTED


def test_second_run_finds_nothing(seeded, capsys):
    assert claim_local_data.main(["--email", seeded.email]) == 0
    capsys.readouterr()

    assert claim_local_data.main(["--email", seeded.email]) == 0
    out = capsys.readouterr().out
    assert "Nothing to move" in out
    assert _counts(seeded.id) == EXPECTED


def test_command_prints_counts_and_exits_nonzero_on_refusal(seeded, capsys):
    assert claim_local_data.main(["--email", seeded.email, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "chunks: 3" in out and "Dry run" in out

    assert claim_local_data.main(["--email", "nobody@ucsd.edu"]) == 1
    assert "REFUSED" in capsys.readouterr().out
