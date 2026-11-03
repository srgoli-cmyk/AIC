"""Runs the page headless with the backend calls mocked; nothing here uses the network."""

import re
import tomllib
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
    link = "[Lecture 8, 0\\:04](https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa)"
    assert link in markdown(page)  # in the sources
    assert f"Use the product rule {link}." in markdown(page)  # and in the answer


def test_snippet_is_shown_as_plain_text_not_markup(backend):
    page = ask(run_page())
    assert SOURCE["snippet"] in texts(page)
    assert not any("evil.example" in m for m in markdown(page))
    assert not page.caption


def test_injected_link_in_the_answer_is_stripped_and_citation_kept(backend):
    answer = "See [click](https://evil.example) and https://evil.example [Lecture 8, 0:04]."
    backend["ask"] = response(payload={"answer": answer, "sources": [SOURCE]})
    page = ask(run_page())
    assert not any("evil.example" in m for m in markdown(page))
    assert any(
        m.startswith("See click and")
        and "[Lecture 8, 0\\:04](https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa)" in m
        for m in markdown(page)
    )


def test_answer_is_rendered_as_markdown_without_raw_html(backend):
    backend["ask"] = response(payload={"answer": "**Step 1:** $x^2$ <b>hi</b>", "sources": []})
    page = ask(run_page())
    shown = next(m for m in page.markdown if "Step 1" in m.value)
    assert shown.value == "**Step 1:** $x^2$ \\<b>hi\\</b>"
    assert shown.allow_html is False
    assert [s.value for s in page.subheader] == ["Answer"]


def _links(answer, sources=()):
    return logic.answer_markdown(answer, list(sources))


@pytest.mark.parametrize(
    "answer",
    [
        "[click](https://evil.example)",
        "[click](https://evil.example 'title')",
        "[click](<https://evil.example>)",
        "[click](javascript:alert(1))",
        "[click](&#104;ttps://evil.example)",
        "![img](https://evil.example/x.png)",
        "[![img](https://evil.example/x.png)](https://evil.example)",
        "[[nested]](https://evil.example)",
        "<https://evil.example>",
        "<mailto:a@evil.example>",
        "https://evil.example/a_b",
        "HTTP://EVIL.EXAMPLE",
        "www.evil.example",
        "a@evil.example",
        "$[click](https://evil.example)$",
        "\\[click\\](https://evil.example)",
        "see https\\://evil.example/g",
        "see https&#58;//evil.example/h",
        "see https&amp;#58;//evil.example/h2",
        "see www\\.evil.example/i",
        "see www&#46;evil.example/j",
        "mail a&#64;evil.example",
        "mail a\\@evil.example",
        "see https:a@b.c//evil.example/k",
        "mailto:a@evil.example",
        "[\\<foo@evil.example>] end",
    ],
)
def test_no_form_of_link_or_image_survives(answer):
    out = _links(answer)
    assert "evil.example" not in out
    assert "javascript" not in out
    assert "](" not in out


@pytest.mark.parametrize(
    "answer",
    [
        "[a][ref]\n\n[ref]: https://evil.example",
        "[a][ref]\n\n[ref]: /relative",
        "[a]\n\n[a]: relative",
        "[a](b(c(d(e))))",
        "\\[a](b)",
        "[a\\](b)",
        "a $$\n\n![x][r] and [c][r]\n\n[r]: //evil.example/x.png\n\nb $$",
        "a $$\n\n[c][r]\n\n[r]: https:other/x\n\nb $$",
        "`$$`\n\n![x][r]\n\n[r]: //evil.example/c.png\n\n`$$`",
        "```\n$$\n```\n\n[c][r]\n\n[r]: //evil.example/e\n\n```\n$$\n```",
    ],
)
def test_leftover_link_syntax_has_only_escaped_brackets(answer):
    out = _links(answer)
    assert "evil.example" not in out
    assert not re.search(r"(?<!\\)[\[\]]", out)


def test_citation_with_a_forged_target_goes_to_the_checked_url():
    out = _links("[Lecture 8, 0:04](https://evil.example) [Lecture 8, 0:04]", [SOURCE])
    assert "evil.example" not in out
    assert out.endswith("[Lecture 8, 0\\:04](https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa)")


def test_citation_of_a_source_with_a_bad_url_is_not_a_link():
    source = {**SOURCE, "url": "https://evil.example/media/t/1_a"}
    out = _links("Said [Lecture 8, 0:04].", [source])
    assert "evil.example" not in out and "](" not in out


def test_other_brackets_stay_visible_but_are_not_markup():
    assert _links("On [a, b] and [Lecture 9, 1:00].") == r"On \[a, b\] and \[Lecture 9, 1:00\]."


def test_a_bang_before_a_citation_does_not_make_an_image():
    out = _links("look ![Lecture 8, 0:04] here", [SOURCE])
    assert "\\![Lecture 8" in out


def test_a_link_inside_what_looks_like_math_cannot_form():
    """If the renderer reads the span as text, no `][`, `](`, `]:` or tag remains."""
    out = _links("$[a][r]$ and $[b](c)$ and $<b>$", [])
    assert "][" not in out and "](" not in out and "<b" not in out


@pytest.mark.parametrize(
    "answer",
    [
        "$https://evil.example$",
        "$a@evil.example$",
        "$www.evil.example$",
        "$a&#64;evil.example$",
        "$ https://evil.example $",
        "$ a@evil.example $",
    ],
)
def test_a_url_inside_what_looks_like_math_is_removed(answer):
    assert "evil.example" not in _links(answer)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("On $[a, b]$ we get $x<y$.", "On $[a, b] $ we get $x< y$."),
        ("$[f,g](x) = 1$", "$[f,g] (x) = 1$"),
        ("$n![a+b](c)$", "$n![a+b] (c)$"),
        ("$$\n\\int_a^b [f(x)]\\,dx\n$$", "$$\n\\int_a^b [f(x)] \\,dx\n$$"),
        ("So \\(x^2\\) and \\[y^2\\].", "So $x^2$ and $$y^2$$."),
        ("$$a \\\\[4pt] b$$ and \\[ c \\]", "$$a \\\\[4pt]  b$$ and $$ c $$"),
        ("It costs \\$5 on [a].", "It costs \\$5 on \\[a\\]."),
        ("It costs $5 and then $6 more.", "It costs \\$5 and then \\$6 more."),
        ("$a\\$b$ next", "\\$a\\$b\\$ next"),
    ],
)
def test_math_keeps_its_meaning_and_other_dollars_are_text(answer, expected):
    assert _links(answer) == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Let $ x = a $ here.", "Let $x = a$ here."),
        ("Let $x = a $ here.", "Let $x = a$ here."),
        ("Let $ x = a$ here.", "Let $x = a$ here."),
        (r"So $ x \geq a $ holds.", r"So $x \geq a$ holds."),
        (r"So \( x \geq a \) holds.", r"So $x \geq a$ holds."),
        (r"So \(x \geq a\) holds.", r"So $x \geq a$ holds."),
        (r"So \[ x \geq a \] holds.", r"So $$ x \geq a $$ holds."),
        ("$ x < y $", "$x <  y$"),
        ("$ [a, b] $", "$[a, b] $"),
        ("$x$ and $ y $ and $z$", "$x$ and $y$ and $z$"),
    ],
)
def test_math_with_inner_spaces_is_trimmed_and_delimiters_are_converted(answer, expected):
    assert _links(answer) == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("It costs $5.", r"It costs \$5."),
        ("From $5 to $10.", r"From \$5 to \$10."),
        ("Paid $5 and $ 6.", r"Paid \$5 and \$ 6."),
        ("Paid $ 5 and $ 6.", r"Paid \$ 5 and \$ 6."),
        ("It was $5, then $ more.", r"It was \$5, then \$ more."),
        ("US$ and CA$ rates.", r"US\$ and CA\$ rates."),
        ("Paid $ 5 for the $ amount.", r"Paid \$ 5 for the \$ amount."),
        ("A $ sign and a $ sign.", r"A \$ sign and a \$ sign."),
        ("It costs 5 $ and 6 $.", r"It costs 5 \$ and 6 \$."),
        ("That is 10 $, and the other is 20 $.", r"That is 10 \$, and the other is 20 \$."),
        ("Compare $AAPL and $TSLA today.", r"Compare \$AAPL and \$TSLA today."),
        ("The vars $HOME and $PATH are set.", r"The vars \$HOME and \$PATH are set."),
    ],
)
def test_money_stays_plain_text(answer, expected):
    assert _links(answer) == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Use the $ sign for $x$ here.", r"Use the \$ sign for $x$ here."),
        ("Costs $a for $x$ here.", r"Costs \$a for $x$ here."),
        (r"So 2$\pi r$ and f$x$ and x$^2$.", r"So 2$\pi r$ and f$x$ and x$^2$."),
        (r"Then \( x \) 5 times, and \(x\) 5 times.", "Then $x$ 5 times, and $x$ 5 times."),
        (r"\(a\)\(b\) and \(x\)2", "$a$ $b$ and $x$2"),
        ("\\( x\n+ y \\)", "$x + y$"),
        (r"\( \$5 + x \)", r"( \$5 + x )"),
        (r"$ x\ $", r"$x\ $"),
        ("$a//b$", "$a//b$"),
    ],
)
def test_math_next_to_dollars_and_explicit_delimiters(answer, expected):
    assert _links(answer) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "It cost $ 5 [Lecture 8, 0:04] and the $ rate rose.",
        "See $AAPL [Lecture 8, 0:04] and $TSLA now.",
        "A $ x = [Lecture 8, 0:04] $ here.",
        r"A \( [Lecture 8, 0:04] \) here.",
    ],
)
def test_a_citation_is_never_swallowed_by_math(answer):
    assert "(https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa)" in _links(answer, [SOURCE])


def test_math_after_money_still_renders():
    assert _links("Costs $5 for $x$.") == r"Costs \$5 for $x$."


def test_math_before_a_number_still_renders():
    assert _links("Then $x$ 5 times.") == "Then $x$ 5 times."


def test_currency_does_not_swallow_a_citation():
    out = _links("It costs $5 [Lecture 8, 0:04] and then $6 more.", [SOURCE])
    assert "(https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa)" in out


def test_code_is_shown_as_text():
    assert _links("`a[0] < b`") == r"\`a\[0\] \< b\`"


def test_markdown_formatting_survives():
    text = (
        "### Step 1\n\n**Bold** and *italic*\n\n1. one\n2. two\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    )
    assert _links(text) == text


@pytest.mark.parametrize(
    ("title", "timestamp", "citation"),
    [
        ("Review [part 2]", "1:15", "Review part 2, 1:15"),
        ("Double  Space", "2:00", "Double Space, 2:00"),
    ],
)
def test_a_title_links_in_the_form_the_backend_cites(title, timestamp, citation):
    source = {**SOURCE, "lecture_title": title, "timestamp": timestamp}
    assert f"({SOURCE['url']})" in _links(f"Said [{citation}].", [source])


def test_a_title_cannot_become_a_link_in_its_label():
    source = {**SOURCE, "lecture_title": "www.evil.example", "url": "https://evil.example/x"}
    assert "evil" not in logic.source_link(source)


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
    assert any(m.startswith("Use the product rule") for m in markdown(page))  # the answer stays


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
        "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa?st=0",
        "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/111111",
        "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/111111?st=127",
    ],
)
def test_source_link_accepts_a_lecture_url_with_its_start_time(url):
    assert logic.source_link({**SOURCE, "url": url}).endswith(f"]({url})")
    assert f"]({url})" in logic.answer_markdown("Said [Lecture 8, 0:04].", [{**SOURCE, "url": url}])


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/media/t/1_aaaaaaaa/111111?st=5",
        "https://canvaskaf.ucsd.edu.evil.example/media/t/1_aaaaaaaa?st=5",
        "http://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa?st=5",
        "https://canvaskaf.ucsd.edu/other?st=5",
        "https://canvaskaf.ucsd.edu/media/other?st=5",
        "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa?st=5&x=1",
    ],
)
def test_source_link_with_a_start_time_still_refuses_other_urls(url):
    assert "](" not in logic.source_link({**SOURCE, "url": url})


def test_streamlit_config_hides_the_deploy_button_and_stays_local():
    config = tomllib.loads(
        (Path(__file__).resolve().parents[2] / ".streamlit/config.toml").read_text()
    )
    assert config["client"]["toolbarMode"] == "minimal"
    assert config["server"]["address"] == "localhost"
    assert config["browser"]["gatherUsageStats"] is False


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
