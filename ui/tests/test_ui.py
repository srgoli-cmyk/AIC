"""Runs the page headless with the backend calls mocked; nothing here uses the network."""

from pathlib import Path

import httpx
import logic
import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")
COURSE = {
    "course_id": "canvas-1",
    "code": "TEST 1",
    "name": "Synthetic",
    "lectures": 2,
    "indexed": True,
}
OTHER = {
    "course_id": "canvas-2",
    "code": "TEST 2",
    "name": "Other",
    "lectures": 1,
    "indexed": False,
}
SOURCE = {
    "lecture_title": "Lecture 8 - Product Rule [001-000-LE]",
    "entry_id": "1_aaaaaaaa",
    "start_sec": 4.5,
    "end_sec": 19.0,
    "timestamp": "0:04",
    "url": "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa",
    "snippet": "A synthetic *snippet* about [links](https://evil.example).",
}
ANSWER = {"answer": "Use the product rule [Lecture 8, 0:04].", "sources": [SOURCE]}


def response(status=200, payload=None):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "http://test"))


@pytest.fixture
def backend(monkeypatch):
    state = {
        "courses": response(payload=[COURSE, OTHER]),
        "ask": response(payload=ANSWER),
        "asked": [],
    }

    def get(url, **kwargs):
        if isinstance(state["courses"], Exception):
            raise state["courses"]
        return state["courses"]

    def post(url, json, **kwargs):
        state["asked"].append((url, json))
        if isinstance(state["ask"], Exception):
            raise state["ask"]
        return state["ask"]

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setenv("BACKEND_URL", "http://backend.test")
    return state


def run_page():
    page = AppTest.from_file(APP, default_timeout=30).run()
    assert not page.exception
    return page


def ask(page, question="what is it?"):
    page.text_input[0].set_value(question)
    page.button[0].click()
    page.run()
    assert not page.exception
    return page


def markdown(page):
    return [m.value for m in page.markdown]


def texts(page):
    return [t.value for t in page.text]


def test_picker_shows_lecture_count_and_indexed_status(backend):
    page = run_page()
    assert page.selectbox[0].options == [
        "TEST 1: Synthetic (2 lectures, indexed)",
        "TEST 2: Other (1 lecture, not indexed yet)",
    ]


def test_unindexed_course_warns(backend):
    page = run_page()
    assert not page.warning
    page.selectbox[0].select_index(1).run()
    assert "still being indexed" in page.warning[0].value


def test_asks_selected_course_and_shows_answer_and_linked_sources(backend):
    page = ask(run_page())
    assert backend["asked"] == [
        ("http://backend.test/ask", {"course_id": "canvas-1", "question": "what is it?"})
    ]
    assert ANSWER["answer"] in texts(page)
    assert "[Lecture 8, 0\\:04](https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa)" in markdown(page)


def test_snippet_is_shown_as_plain_text_not_markup(backend):
    page = ask(run_page())
    assert SOURCE["snippet"] in texts(page)
    assert not any("evil.example" in m for m in markdown(page))
    assert not page.caption


def test_answer_is_shown_as_plain_text_not_markup(backend):
    answer = "[click](https://evil.example) https://evil.example\n==="
    backend["ask"] = response(payload={"answer": answer, "sources": []})
    page = ask(run_page())
    assert answer in texts(page)
    assert not any("evil.example" in m for m in markdown(page))
    assert [s.value for s in page.subheader] == ["Answer"]


def test_question_is_shown_with_its_answer(backend):
    page = ask(run_page(), "what is it?")
    assert "Q: what is it?" in texts(page)


def test_answer_with_no_sources(backend):
    backend["ask"] = response(payload={"answer": "Not in the captions.", "sources": []})
    page = ask(run_page())
    assert [s.value for s in page.subheader] == ["Answer"]


def test_blank_question_is_not_sent(backend):
    page = ask(run_page())
    page = ask(page, "   ")
    assert len(backend["asked"]) == 1
    assert "Type a question" in page.warning[0].value
    assert ANSWER["answer"] in texts(page)  # the previous answer stays


def test_answer_is_hidden_after_switching_course(backend):
    page = ask(run_page())
    page.selectbox[0].select_index(1).run()
    assert not page.subheader


def test_backend_error_detail_is_shown(backend):
    backend["ask"] = response(502, {"detail": "answer service unavailable"})
    page = ask(run_page())
    assert page.error[0].value == "answer service unavailable"
    assert not page.subheader


def test_backend_down_on_load(backend):
    backend["courses"] = httpx.ConnectError("refused")
    page = run_page()
    assert "Could not reach the backend" in page.error[0].value
    assert not page.selectbox


def test_backend_down_on_ask(backend):
    backend["ask"] = httpx.ReadTimeout("slow")
    page = ask(run_page())
    assert "Could not reach the backend" in page.error[0].value


def test_no_courses(backend):
    backend["courses"] = response(payload=[])
    page = run_page()
    assert "No courses" in page.info[0].value


def test_source_label_without_a_lecture_number_uses_the_title():
    source = {"lecture_title": "Midterm review", "timestamp": "1:02:03"}
    assert logic.source_label(source) == "Midterm review, 1:02:03"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa",
        "data:text/html,x",
        "https://evil.example/media/t/1_aaaaaaaa",
        "https://canvaskaf.ucsd.edu/media/t/a b",
        "https://canvaskaf.ucsd.edu/media/t/a)[x](https://evil.example",
    ],
)
def test_source_link_refuses_other_urls(url):
    link = logic.source_link({**SOURCE, "url": url})
    assert "](" not in link
    assert "http" not in link


def test_source_label_matches_the_citation_for_padded_and_suffixed_numbers():
    assert logic.source_label({"lecture_title": "Lecture 08 - Limits", "timestamp": "1:00"}) == (
        "Lecture 8, 1:00"
    )
    assert logic.source_label({"lecture_title": "Lecture 12a", "timestamp": "1:00"}) == (
        "Lecture 12, 1:00"
    )


def test_selection_survives_course_labels_changing_before_ask(backend):
    page = run_page()
    page.selectbox[0].select_index(1).run()
    backend["courses"] = response(
        payload=[
            {**COURSE, "lectures": 3},
            {**OTHER, "indexed": True},
            {**COURSE, "course_id": "canvas-3"},
        ]
    )
    page = ask(page)
    assert backend["asked"][0][1]["course_id"] == "canvas-2"


def test_removed_course_warns_and_falls_back(backend):
    page = run_page()
    page.selectbox[0].select_index(1).run()
    backend["courses"] = response(payload=[COURSE])
    page.run()
    assert not page.exception
    assert "no longer available" in page.warning[0].value


@pytest.mark.parametrize(
    "payload",
    [None, {"a": 1}, [1], [{"course_id": "x"}]],
)
def test_wrong_shaped_courses_show_a_message(backend, payload):
    backend["courses"] = response(payload=payload)
    page = run_page()
    assert "cannot read" in page.error[0].value


@pytest.mark.parametrize(
    "payload",
    [None, [], {"answer": "x"}, {"answer": "x", "sources": 1}, {"answer": "x", "sources": [{}]}],
)
def test_wrong_shaped_answer_shows_a_message(backend, payload):
    backend["ask"] = response(payload=payload)
    page = ask(run_page())
    assert "cannot read" in page.error[0].value


def test_non_json_response_shows_a_message(backend):
    backend["courses"] = httpx.Response(
        200, text="<html>", request=httpx.Request("GET", "http://t")
    )
    page = run_page()
    assert "cannot read" in page.error[0].value
