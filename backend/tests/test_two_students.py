"""Two students who synced the same Canvas course (same course id, same entry ids) never see
each other's captions or slides, whatever they ask.

Each student has different, recognisable text. The fake embedder gives every text a one-hot
vector, so a question can be built to be an exact match for one chosen passage. Each student
asks exactly what the OTHER student's passages say: if the search were not scoped to the user,
those passages would be the closest ones and would reach the prompt.
"""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import answer, auth_db, db, embeddings, slides, study
from app.config import settings
from app.main import app

client = TestClient(app)

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
CID = "canvas-999999"
LONG, SHORT = "1_fake0001", "1_fake0002"  # both students have both lectures

# Words with no meaning anywhere else, so a leak is found by a plain `in`. The slide words are
# what the lecture says too, which makes the slide matcher (study tools) pick the student's deck.
WHO = {
    "alice": {"token": "aliceonlymarker", "topic": "zorblax quenchwave frobnicate"},
    "bob": {"token": "bobonlymarker", "topic": "plimbus snarfle wumbology"},
}


# --- test data ---


def _srt(cues: list[str]) -> str:
    def stamp(sec: int) -> str:
        return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d},000"

    return "\n".join(
        f"{i}\n{stamp((i - 1) * 30)} --> {stamp(i * 30)}\n{text}\n"
        for i, text in enumerate(cues, start=1)
    )


def _payload(name: str) -> dict:
    token, topic = WHO[name]["token"], WHO[name]["topic"]
    payload = json.loads(json.dumps(SAMPLE))
    long_lecture, short_lecture = payload["lectures"]
    long_lecture["title"] = f"Lecture 8 - {name} edition"
    long_lecture["duration_sec"] = 600.0
    # 20 cues of 30 s: enough chunks for the slide matcher to vote.
    long_lecture["captions_srt"] = _srt(
        [f"{token} says {topic} point number {i} of the lecture" for i in range(20)]
    )
    short_lecture["title"] = f"Lecture 9 - {name} edition"
    short_lecture["duration_sec"] = 120.0
    short_lecture["captions_srt"] = _srt(
        [f"{token} again {topic} short lecture part {i}" for i in range(4)]
    )
    return payload


def _make_student(name: str) -> dict:
    user = auth_db.upsert_user(f"sub-{name}", f"{name}@ucsd.edu", name.title())
    raw, _ = auth_db.create_session(user.id, kind="extension", ttl_days=1)
    return {"id": user.id, "name": name, "headers": {"Authorization": f"Bearer {raw}"}}


def _add_slides(student: dict) -> None:
    """A stored and embedded slide deck, without a real PDF (slides.store takes the pages)."""
    name = student["name"]
    token, topic = WHO[name]["token"], WHO[name]["topic"]
    pages = [
        f"{token} {topic} {topic} the main idea of the lecture {topic}",
        "agenda for this week and reading list",
        "logistics office hours and homework deadlines",
        "references and further reading",
    ]
    slides.store(
        CID,
        f"canvas-file-{7 if name == 'alice' else 8}",
        source="canvas",
        name=f"{name.title()} Slides.pdf",
        url=f"https://canvas.ucsd.edu/courses/999999/files/{7 if name == 'alice' else 8}",
        version="1:2026-10-01T00:00:00Z",
        data=f"deck of {name}".encode(),
        pages=pages,
        user_id=student["id"],
    )
    slides.embed_document(f"canvas-file-{7 if name == 'alice' else 8}", user_id=student["id"])


@pytest.fixture
def students(monkeypatch):
    """Alice and Bob, both synced into the same course and indexed, each with a slide deck.
    The model is given a key so /ask and /study run (the model itself is mocked per test)."""
    monkeypatch.setattr(answer, "settings", replace(answer.settings, openrouter_api_key="sk-fake"))
    monkeypatch.setattr(study, "settings", replace(study.settings, openrouter_api_key="sk-fake"))
    made = {}
    for name in WHO:
        made[name] = _make_student(name)
        r = client.post("/sync", json=_payload(name), headers=made[name]["headers"])
        assert r.status_code == 200, r.text
        _add_slides(made[name])
    return made


def _other(me: str) -> str:
    return "bob" if me == "alice" else "alice"


def _question_matching(text: str) -> str:
    """A question whose fake query vector is the same as the fake vector of `text`, so that
    passage is the closest possible match (distance 0). The fake vector is one-hot at
    sum(ord) % dim, and the query is embedded with the search prefix."""
    dim = settings.embedding_dim
    wanted = sum(map(ord, text)) % dim
    base = "Explain this please: "
    # Pad with characters whose codes add up to the missing amount (plus a multiple of dim).
    missing = (wanted - sum(map(ord, embeddings.QUERY_PREFIX + base))) % dim + dim
    pad = ""
    while missing > 244:  # keep the last two characters inside printable ASCII (32..122)
        pad += "z"
        missing -= 122
    pad += chr(missing // 2) + chr(missing - missing // 2)
    question = base + pad
    assert sum(map(ord, embeddings.QUERY_PREFIX + question)) % dim == wanted
    return question


def _attack_questions(victim: dict) -> list[tuple[str, str]]:
    """(kind, question) pairs that each exactly match one of the victim's passages: a chunk of
    each lecture and a slide page. The first element names what the question targets."""
    out = []
    for entry in (LONG, SHORT):
        chunk = db.lecture_chunks(entry, user_id=victim["id"])[0]
        out.append((f"chunk of {entry}", _question_matching(chunk.text)))
    page = db.course_pages(CID, user_id=victim["id"])[0]
    out.append(("slide page", _question_matching(page[4])))
    return out


# --- the mocked model ---


def _labels(messages: list[dict]) -> list[str]:
    """The citation labels of the passages in the prompt, in order."""
    return re.findall(r"^Citation: \[([^\]]+)\]$", messages[1]["content"], re.MULTILINE)


def _everything(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


class Model:
    """Records every prompt and answers by citing the first three passages it was given."""

    def __init__(self):
        self.prompts: list[list[dict]] = []

    def reply(self, messages: list[dict]) -> str:
        self.prompts.append(messages)
        return "It is covered. " + " ".join(f"[{label}]" for label in _labels(messages)[:3])


@pytest.fixture
def model(monkeypatch):
    fake = Model()
    monkeypatch.setattr(answer, "call_llm", lambda messages, **options: fake.reply(messages))

    async def stream(messages, **options):
        yield fake.reply(messages)

    monkeypatch.setattr(answer, "stream_llm", stream)
    return fake


def _no_leak(me: str, texts: list[str]) -> None:
    mine, theirs = WHO[me], WHO[_other(me)]
    joined = "\n".join(texts)
    for word in (theirs["token"], *theirs["topic"].split(), _other(me) + " edition"):
        assert word not in joined, f"{me} got text of {_other(me)}: {word}"
    assert mine["token"] in joined, f"{me} got none of their own text"


def _sse_final(raw: str) -> dict:
    for block in raw.split("\n\n"):
        lines = block.split("\n")
        if lines[0] == "event: final":
            return json.loads(lines[1][len("data: ") :])
    raise AssertionError("no final event")


# --- setup is what the tests think it is ---


@pytest.mark.integration
@pytest.mark.no_auth_override
def test_the_attack_questions_really_match_the_other_students_passages(students):
    """If this fails, the isolation tests below prove nothing: without the user scoping, the
    question would not find the other student's passage first."""
    for me in WHO:
        victim = students[_other(me)]
        for what, question in _attack_questions(victim):
            vector = embeddings.embed_query(question)
            lecture_hits = db.search_chunks(CID, vector, 3, user_id=victim["id"])
            slide_hits = db.search_doc_chunks(CID, vector, 3, user_id=victim["id"])
            best = min([*lecture_hits, *slide_hits], key=lambda h: h.distance)
            assert best.distance == pytest.approx(0.0, abs=1e-6), what
            assert WHO[_other(me)]["token"] in best.text, what


# --- /ask, /ask/stream ---


@pytest.mark.integration
@pytest.mark.no_auth_override
@pytest.mark.parametrize("me", ["alice", "bob"])
def test_ask_never_uses_the_other_students_passages(students, model, me):
    mine = students[me]
    for what, question in _attack_questions(students[_other(me)]):
        model.prompts.clear()
        r = client.post(
            "/ask", json={"course_id": CID, "question": question}, headers=mine["headers"]
        )
        assert r.status_code == 200, (what, r.text)
        assert len(model.prompts) == 1
        _no_leak(me, [_everything(model.prompts[0])])
        sources = r.json()["sources"]
        assert sources, what
        _no_leak(me, [json.dumps(r.json())])


@pytest.mark.integration
@pytest.mark.no_auth_override
@pytest.mark.parametrize("me", ["alice", "bob"])
def test_ask_stream_never_uses_the_other_students_passages(students, model, me):
    mine = students[me]
    for what, question in _attack_questions(students[_other(me)]):
        model.prompts.clear()
        r = client.post(
            "/ask/stream", json={"course_id": CID, "question": question}, headers=mine["headers"]
        )
        assert r.status_code == 200, (what, r.text)
        assert len(model.prompts) == 1
        _no_leak(me, [_everything(model.prompts[0])])
        final = _sse_final(r.text)
        assert final["sources"], what
        _no_leak(me, [json.dumps(final), r.text])


@pytest.mark.integration
@pytest.mark.no_auth_override
@pytest.mark.parametrize("me", ["alice", "bob"])
def test_ask_about_one_lecture_never_uses_the_other_students_passages(students, model, me):
    """The same entry id exists for both students: scoping by entry alone would mix them."""
    mine = students[me]
    victim = students[_other(me)]
    for entry in (LONG, SHORT):
        question = _question_matching(db.lecture_chunks(entry, user_id=victim["id"])[0].text)
        for path in ("/ask", "/ask/stream"):
            model.prompts.clear()
            r = client.post(
                path,
                json={"course_id": CID, "question": question, "entry_id": entry},
                headers=mine["headers"],
            )
            assert r.status_code == 200, (path, entry, r.text)
            _no_leak(me, [_everything(model.prompts[0])])
            _no_leak(me, [r.text])
            # Only that lecture: no slides, and no other lecture.
            assert all(label.startswith("Lecture") for label in _labels(model.prompts[0]))


# --- /study ---


def _study_reply(messages: list[dict]) -> str:
    """A valid reply for each study kind, citing the first passage it was given. Any other call
    (the checker of lessons and quizzes) fails, which the study code answers with its fallback."""
    system = messages[0]["content"]
    label = _labels(messages)[0] if system in (study.TEACH_SYSTEM, study.CARDS_SYSTEM) else ""
    if system == study.TEACH_SYSTEM:
        return json.dumps(
            {"sections": [{"title": "Idea", "body": "The lecture idea.", "citation": label}]}
        )
    if system == study.CARDS_SYSTEM:
        return json.dumps({"cards": [{"front": "Q?", "back": "A.", "citation": label}]})
    if system == study.QUIZ_SYSTEM:
        label = _labels(messages)[0]
        question = {
            "question": "Which one?",
            "choices": ["a", "b", "c", "d"],
            "answer": 0,
            "why": "because",
            "level": 2,
            "citation": label,
        }
        return json.dumps({"questions": [question]})
    raise answer.AnswerUnavailable


@pytest.mark.integration
@pytest.mark.no_auth_override
@pytest.mark.parametrize("me", ["alice", "bob"])
@pytest.mark.parametrize("kind", ["teach", "flashcards", "quiz"])
def test_study_never_uses_the_other_students_passages(students, monkeypatch, me, kind):
    prompts: list[list[dict]] = []

    def fake(messages, **options):
        prompts.append(messages)
        return _study_reply(messages)

    monkeypatch.setattr(answer, "call_llm", fake)
    r = client.post(
        "/study", json={"entry_id": LONG, "kind": kind}, headers=students[me]["headers"]
    )
    assert r.status_code == 200, r.text
    assert prompts, "the model was not called"
    theirs = WHO[_other(me)]
    for messages in prompts:  # the writer and, for a lesson or quiz, the checker
        for word in (theirs["token"], *theirs["topic"].split(), _other(me).title() + " Slides"):
            assert word not in _everything(messages)
    assert theirs["token"] not in r.text
    # The student's own lecture and slides are in the writer's prompt, so the slide path was
    # exercised and the check above is not passing on an empty prompt.
    writer = _everything(prompts[0])
    assert WHO[me]["token"] in writer
    assert f"Slides: {me.title()} Slides, p." in writer


# --- the course page and the documents ---


@pytest.mark.integration
@pytest.mark.no_auth_override
@pytest.mark.parametrize("me", ["alice", "bob"])
def test_course_page_and_documents_show_only_the_students_own(students, me):
    mine = students[me]
    other = _other(me)

    detail = client.get(f"/courses/{CID}", headers=mine["headers"])
    assert detail.status_code == 200
    titles = sorted(lec["title"] for lec in detail.json()["lectures"])
    assert titles == [f"Lecture 8 - {me} edition", f"Lecture 9 - {me} edition"]
    assert other not in json.dumps(detail.json())

    docs = client.get(f"/courses/{CID}/documents", headers=mine["headers"])
    assert docs.status_code == 200
    assert [d["name"] for d in docs.json()] == [f"{me.title()} Slides"]
    assert other.title() not in json.dumps(docs.json())

    lectures = client.get(f"/courses/{CID}/lectures", headers=mine["headers"])
    assert sorted(item["entry_id"] for item in lectures.json()) == [LONG, SHORT]
    courses = client.get("/courses", headers=mine["headers"])
    assert [c["course_id"] for c in courses.json()] == [CID]
