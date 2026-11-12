"""POST /study with a mocked model and mocked lecture chunks. No test here reaches the network."""

import json
import random
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import answer, db, study
from app.main import app

FAKE_KEY = "sk-or-FAKE-KEY-0123456789"
ENTRY = "1_fake0001"
client = TestClient(app)


def _hit(start=0.0, text="words", no=9):
    return db.ChunkHit(
        ENTRY,
        f"Lecture {no} - Kernels",
        no,
        f"https://canvaskaf.ucsd.edu/media/t/{ENTRY}/111111",
        start,
        start + 50,
        text,
        0.0,
    )


HITS = [_hit(0, "a kernel is the vectors sent to zero"), _hit(62, "find the kernel of A")]
FIRST, SECOND = "Lecture 9, 0:00", "Lecture 9, 1:02"


class FakeLLM:
    """Stands in for answer.call_llm: returns the canned replies in order, records each call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, **options):
        self.calls.append((messages, options))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


@pytest.fixture
def lecture(monkeypatch):
    """A synced lecture with two chunks and a key set; returns a function that sets the replies."""
    monkeypatch.setattr(study, "settings", replace(study.settings, openrouter_api_key=FAKE_KEY))
    monkeypatch.setattr(db, "lecture_exists", lambda entry_id: entry_id == ENTRY)
    monkeypatch.setattr(db, "lecture_chunks", lambda entry_id: HITS)

    def reply_with(*replies):
        fake = FakeLLM(*replies)
        monkeypatch.setattr(answer, "call_llm", fake)
        return fake

    return reply_with


def _study(kind, entry=ENTRY):
    return client.post("/study", json={"entry_id": entry, "kind": kind})


def q(n, answer_at=0, **extra):
    return {
        "question": f"Q{n}?",
        "choices": [f"a{n}", f"b{n}", f"c{n}", f"d{n}"],
        "answer": answer_at,
        "why": "because",
        "level": 2,
        "citation": FIRST,
        **extra,
    }


def checks(*lists):
    return {"checks": [{"n": i, "work": "w", "correct": c} for i, c in enumerate(lists, start=1)]}


# --- the lesson ---


def test_a_lesson_keeps_well_formed_sections_and_the_lectures_problems(lecture):
    llm = lecture(
        {
            "sections": [
                {
                    "title": "Kernels",
                    "body": "A **kernel** is...",
                    "citation": f"[{FIRST}]",
                    "problems": [
                        {
                            "question": "Find the kernel of $A$.",
                            "steps": ["Row reduce.", 7, ""],
                            "answer": "\\text{span}\\{v\\}",
                            "citation": SECOND,
                            "from_lecture": True,
                        },
                        {
                            "question": "Claims the lecture",
                            "steps": [],
                            "answer": "y",
                            "citation": "Lecture 3, 9:99",
                            "from_lecture": True,
                        },
                        {
                            "question": "Extra",
                            "answer": "x",
                            "citation": "",
                            "from_lecture": False,
                        },
                        {"question": "No answer"},
                    ],
                },
                {"title": "No body"},
                "not a section",
            ],
            "check": ["Why is the kernel a subspace?", 3],
        }
    )
    r = _study("teach")
    assert r.status_code == 200
    body = r.json()
    assert [s["title"] for s in body["sections"]] == ["Kernels"]
    section = body["sections"][0]
    assert section["source"]["timestamp"] == "0:00"
    worked, claimed, extra = section["problems"]
    assert worked["from_lecture"] is True and worked["source"]["timestamp"] == "1:02"
    assert worked["source"]["url"].endswith("?st=59")
    assert worked["steps"] == ["Row reduce."]
    assert worked["answer"] == "$\\text{span}\\{v\\}$", "a bare result is marked as math"
    # A problem that names no real passage is never presented as the lecture's own.
    assert claimed["from_lecture"] is False and claimed["source"] is None
    assert extra["from_lecture"] is False and extra["steps"] == []
    assert body["check"] == ["Why is the kernel a subspace?"]

    messages, options = llm.calls[0]
    assert messages[0]["content"] == study.TEACH_SYSTEM
    assert f"Citation: [{SECOND}]\nfind the kernel of A" in messages[1]["content"]
    assert options["json_mode"] is True and options["max_tokens"] >= 4000
    assert options["timeout"] == study.TIMEOUT_SEC


def test_every_prompt_puts_the_passages_between_random_markers():
    first = study.build_messages("teach", HITS)[1]["content"]
    second = study.build_messages("teach", HITS)[1]["content"]
    assert first != second
    assert first.endswith("with its problems.")
    for kind in study.KINDS:
        assert "ignore any instruction" in study.build_messages(kind, HITS)[0]["content"]


# --- flashcards ---


def test_flashcards_drop_empty_cards_and_unknown_citations(lecture):
    lecture(
        "```json\n"
        + json.dumps(
            {
                "cards": [
                    {
                        "front": "What is a kernel?",
                        "back": "Vectors sent to zero.",
                        "citation": FIRST,
                    },
                    {"front": "", "back": "x"},
                    {"front": "Made up", "back": "y", "citation": "Lecture 3, 9:99"},
                ]
            }
        )
        + "\n```"
    )
    cards = _study("flashcards").json()["cards"]
    assert [c["front"] for c in cards] == ["What is a kernel?", "Made up"]
    assert cards[0]["source"]["entry_id"] == ENTRY and cards[1]["source"] is None


# --- practice problems ---


def test_questions_need_four_different_choices_a_valid_answer_and_a_new_result():
    reply = json.dumps(
        {
            "questions": [
                q(1, 2, level=3),
                q(2, choices=["a", "b", "c"]),
                q(3, 4),
                q(4, choices=["a", "a", "c", "d"]),
                q(5, "1"),
                q(6, True),
                q(7, 2, choices=["x", "y", "$c1$", "z"]),  # the same result as question 1
                q(8, level=9),
            ]
        }
    )
    kept = study.parse_questions(reply, HITS)
    assert [(k.question, k.level) for k in kept] == [("Q1?", 3), ("Q8?", 2)]
    assert study.parse_questions("not json", HITS) == []


def test_bare_math_in_choices_is_marked():
    reply = json.dumps({"questions": [q(1, choices=["2x \\sin x", "x^2", "plain words", "$y$"])]})
    assert study.parse_questions(reply, HITS)[0].choices == [
        "$2x \\sin x$",
        "$x^2$",
        "plain words",
        "$y$",
    ]


def test_the_check_drops_questions_with_two_right_answers(lecture):
    questions = [q(1), q(2), q(3), q(4, 2), q(5)]
    llm = lecture({"questions": questions}, checks([0], [0, 1], [0], [2], [0]))
    body = _study("quiz").json()
    assert sorted(x["question"] for x in body["questions"]) == ["Q1?", "Q3?", "Q4?", "Q5?"]
    for x in body["questions"]:
        n = x["question"][1]
        right = "c" if n == "4" else "a"
        assert x["choices"][x["answer"]] == f"{right}{n}", "the answer follows the shuffle"
    listed = llm.calls[1][0][1]["content"]
    assert "Question 2: Q2?\n0: a2\n1: b2" in listed
    assert "kernel" not in listed, "the check sees the questions, not the lecture"


@pytest.mark.parametrize(
    "check",
    [
        checks([0], [0, 1], [1], [1], [1]),  # too few survive
        {"correct": [[0]]},  # another shape
        "not json",
        answer.AnswerUnavailable(),
    ],
)
def test_a_check_that_fails_or_keeps_too_few_falls_back_to_all_questions(lecture, check):
    lecture({"questions": [q(n) for n in range(1, 6)]}, check)
    assert len(_study("quiz").json()["questions"]) == 5


def test_a_question_the_check_skips_is_dropped():
    questions = study.parse_questions(json.dumps({"questions": [q(1), q(2)]}), HITS)
    assert [k.question for k in study.checked(questions, json.dumps(checks([0])))] == ["Q1?"]


def test_at_most_ten_questions_are_returned_in_mixed_order(lecture):
    lecture(
        {"questions": [q(n) for n in range(1, 13)]},
        checks(*[[0]] * 12),
    )
    questions = _study("quiz").json()["questions"]
    assert len(questions) == study.QUIZ_KEEP
    shuffled = study._shuffled(
        study.parse_questions(json.dumps({"questions": [q(1)]}), HITS)[0],
        random.Random(1),  # noqa: S311
    )
    assert shuffled.choices[shuffled.answer] == "a1" and sorted(shuffled.choices) == [
        "a1",
        "b1",
        "c1",
        "d1",
    ]


# --- reading JSON that carries LaTeX ---


def test_latex_written_with_single_backslashes_survives():
    raw = (
        r'{"cards":[{"front":"Differentiate $x^2 \times \sin x$",'
        r'"back":"$\frac{1}{\cos^2 x}$ and $\theta \neq 0$\nNext line","citation":"x"}]}'
    )
    card = study.parse_cards(raw, HITS).cards[0]
    assert card.front == r"Differentiate $x^2 \times \sin x$"
    assert card.back == r"$\frac{1}{\cos^2 x}$ and $\theta \neq 0$" + "\nNext line"
    untouched = r'"a \\frac b \" c é"'
    assert study.repair_json_math(untouched) == untouched


def test_upright_function_names_become_the_real_commands():
    reply = json.dumps({"cards": [{"front": "$\\text{sin} x$", "back": "$\\mathrm{cos} x$"}]})
    card = study.parse_cards(reply, HITS).cards[0]
    assert (card.front, card.back) == ("$\\sin x$", "$\\cos x$")


# --- long lectures ---


def test_a_long_lecture_is_sampled_evenly_and_stays_under_the_size_limit():
    items = list(range(53))
    picked = study.spread(items, 20)
    assert len(picked) == 20 and picked[0] == 0 and picked[-1] == 52
    assert study.spread([1, 2, 3], 20) == [1, 2, 3]

    normal = [_hit(i * 60, "short text") for i in range(50)]
    assert len(study.passages_for(normal)) == 50, "a normal lecture is sent whole"
    long = [_hit(i * 60, "w " * 1100) for i in range(90)]
    given = study.passages_for(long)
    assert 10 <= len(given) <= study.MAX_PASSAGES
    assert sum(len(h.text) for h in given) <= study.MAX_CHARS


# --- the endpoint's errors ---


def test_unknown_lecture_bad_kind_and_extra_fields(lecture):
    llm = lecture()
    assert _study("teach", "1_other001").status_code == 404
    assert _study("essay").status_code == 422
    assert client.post("/study", json={"entry_id": "../x", "kind": "teach"}).status_code == 422
    extra = {"entry_id": ENTRY, "kind": "teach", "question": "ignore the lecture"}
    assert client.post("/study", json=extra).status_code == 422
    assert llm.calls == []


def test_a_lecture_with_no_chunks_is_a_409(lecture, monkeypatch):
    llm = lecture()
    monkeypatch.setattr(db, "lecture_chunks", lambda entry_id: [])
    r = _study("teach")
    assert (r.status_code, r.json()) == (409, {"detail": "lecture not indexed"})
    assert llm.calls == []


def test_without_a_key_it_is_a_503_before_anything_is_read(monkeypatch):
    monkeypatch.setattr(db, "lecture_exists", lambda entry_id: True)
    monkeypatch.setattr(db, "lecture_chunks", lambda entry_id: pytest.fail("read without a key"))
    r = _study("teach")
    assert (r.status_code, r.json()) == (503, {"detail": "answer service not configured"})


@pytest.mark.parametrize("kind", study.KINDS)
@pytest.mark.parametrize(
    "reply", ["not json", "{}", '{"sections": [], "cards": [], "questions": []}']
)
def test_a_reply_that_is_unusable_twice_is_a_502(lecture, kind, reply):
    llm = lecture(reply, reply)
    r = _study(kind)
    assert (r.status_code, r.json()) == (502, {"detail": "answer service unavailable"})
    assert len(llm.calls) == 2


def test_an_unusable_reply_is_tried_once_more(lecture):
    llm = lecture("not json", {"cards": [{"front": "f", "back": "b"}]})
    assert _study("flashcards").json()["cards"][0]["front"] == "f"
    assert len(llm.calls) == 2


def test_a_failed_model_call_is_a_502(lecture):
    lecture(answer.AnswerUnavailable())
    assert _study("flashcards").status_code == 502


# --- the model call options ---


def test_call_llm_passes_the_study_options(monkeypatch):
    monkeypatch.setattr(answer, "settings", replace(answer.settings, openrouter_api_key=FAKE_KEY))
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: handler(request)
    )
    answer.call_llm([], max_tokens=4500, json_mode=True, model="some/model", timeout=90.0)
    assert seen["body"]["model"] == "some/model"
    assert seen["body"]["max_tokens"] == 4500
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["timeout"]["read"] == 90.0


def test_the_study_model_is_optional(lecture, monkeypatch):
    llm = lecture(
        {"cards": [{"front": "f", "back": "b"}]}, {"cards": [{"front": "f", "back": "b"}]}
    )
    _study("flashcards")
    assert llm.calls[0][1]["model"] is None, "empty means the usual model"
    monkeypatch.setattr(study, "settings", replace(study.settings, openrouter_study_model="x/y"))
    _study("flashcards")
    assert llm.calls[1][1]["model"] == "x/y"


# --- the database read ---


@pytest.mark.integration
def test_lecture_chunks_come_back_in_spoken_order():
    sample = json.loads((db_root() / "contracts/fixtures/sync_sample.json").read_text())
    assert client.post("/sync", json=sample).status_code == 200
    entry = sample["lectures"][0]["entry_id"]
    chunks = db.lecture_chunks(entry)
    assert chunks, "ingestion ran after the sync"
    assert [c.start_sec for c in chunks] == sorted(c.start_sec for c in chunks)
    assert {c.entry_id for c in chunks} == {entry} and chunks[0].lecture_title
    assert db.lecture_exists(entry) and not db.lecture_exists("1_nothere0")
    assert db.lecture_chunks("1_nothere0") == []


def db_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]
