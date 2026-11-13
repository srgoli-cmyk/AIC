# ruff: noqa: E501 (the HTML and the pages in these tests read best on one line)
"""Finding a course's own website: checking a candidate page against the course, reading lecture
material off it, and refusing addresses that are not public https pages. Synthetic pages; the
network is never reached (fetch is replaced)."""

from datetime import date

import pytest

from app import coursesite as cs

DATES = tuple(date(2026, m, d) for m, d in [(9, 25), (9, 28), (9, 30), (10, 2), (10, 5), (10, 7)])
DSC10 = cs.CourseFacts(
    code="DSC010_FA26_001",
    name="DSC 010 - Principles of Data Science",
    term="Fall 2026",
    instructors=("Peter Chi", "Janine Tiefenbruck"),
    lecture_dates=DATES,
)
SCHEDULE = "Week 1 Fri Sep 25 LEC 1 Intro Mon Sep 28 LEC 2 Variables Wed Sep 30 LEC 3 Arrays Fri Oct 2 LEC 4"


def judge(text, url="https://dsc10.com/", title="Home | DSC 10", facts=DSC10):
    return cs.judge(facts, url, title, text)


# --- what Canvas tells us ---


def test_course_facts_are_read_from_canvas_codes():
    assert DSC10.subject_number == ("DSC", "10")
    assert DSC10.season_year == ("Fall", 2026)
    assert DSC10.surnames == ("Chi", "Tiefenbruck")
    math = cs.CourseFacts(code="MATH018_WI27_002", name="Linear Algebra", term=None)
    assert (math.subject_number, math.season_year) == (("MATH", "18"), ("Winter", 2027))
    assert cs.CourseFacts(code="CSE 8A", name="x", term="Spring Quarter 2026").subject_number == (
        "CSE",
        "8A",
    )
    assert cs.CourseFacts(code="???", name="x", term=None).subject_number is None


# --- checking a candidate ---


def test_the_right_course_term_and_instructors_is_used_without_asking():
    v = judge(
        f"DSC 10, Fall 2026 at UC San Diego. Instructors: Peter Chi, Janine Tiefenbruck. {SCHEDULE}"
    )
    assert v.decision == cs.ACCEPT
    assert any("lecture dates" in r for r in v.reasons)


def test_another_term_of_the_right_course_is_asked_about_never_used():
    v = judge("DSC 10, Winter 2025 at UC San Diego. Instructor: Janine Tiefenbruck.")
    assert v.decision == cs.ASK
    assert v.term_found == "Winter 2025" and any("not Fall 2026" in r for r in v.reasons)


def test_a_page_that_does_not_say_its_term_is_asked_about_when_it_names_an_instructor():
    assert judge("DSC 10 at UC San Diego. Staff: Peter Chi.").decision == cs.ASK
    assert judge("DSC 10 at UC San Diego. Staff: someone else.").decision == cs.REJECT


@pytest.mark.parametrize(
    "text, url, title",
    [
        (
            "DSC 10, Fall 2026. Instructors: Peter Chi.",
            "https://dsc10.example/",
            "DSC 10",
        ),  # no school
        ("DSC 20, Fall 2026 at UC San Diego. Peter Chi.", "https://x.ucsd.edu/dsc20/", "DSC 20"),
        ("DSC 100, Fall 2026 at UC San Diego. Peter Chi.", "https://x.ucsd.edu/", "DSC 100"),
        ("Welcome to Fall 2026 at UC San Diego.", "https://x.ucsd.edu/", "Courses"),
    ],
)
def test_another_school_another_course_or_no_course_is_rejected(text, url, title):
    assert judge(text, url, title).decision == cs.REJECT


def test_a_catalog_that_lists_many_courses_is_not_a_course_site():
    listing = " ".join(f"DSC {n} Something." for n in (10, 20, 30, 40, 80, 100, 102, 106))
    v = judge(
        f"Fall 2026 at UC San Diego. Chi, Tiefenbruck. {listing}",
        "https://datascience.ucsd.edu/courses",
        "DSC 10 and more",
    )
    assert v.decision == cs.REJECT and any("catalog" in r for r in v.reasons)


def test_the_same_term_of_another_year_is_not_this_term():
    assert judge("DSC 10, Fall 2025 at UC San Diego. Peter Chi.").decision == cs.ASK


def test_the_course_code_in_the_address_counts_and_a_longer_number_does_not():
    ok = cs.judge(DSC10, "https://x.ucsd.edu/~dsc10/", "Home", "Fall 2026, UC San Diego, Peter Chi")
    assert ok.decision == cs.ACCEPT
    no = cs.judge(
        DSC10, "https://x.ucsd.edu/~dsc100/", "Home", "Fall 2026, UC San Diego, Peter Chi"
    )
    assert no.decision == cs.REJECT


def test_candidates_are_checked_and_ordered_best_first(monkeypatch):
    pages = {
        "https://dsc10.com/": f"<title>Home | DSC 10</title>DSC 10, Fall 2026, UC San Diego. Peter Chi. {SCHEDULE}",
        "https://dsc10.com/staff/": "<title>Staff | DSC 10</title>DSC 10, Fall 2026, UC San Diego. Peter Chi.",
        "https://old.ucsd.edu/dsc10/": "<title>DSC 10</title>DSC 10, Winter 2025, UC San Diego. Janine Tiefenbruck.",
        "https://blog.example/": "<title>My blog</title>hello",
    }

    def fake_fetch(url, **_):
        if url not in pages:
            raise cs.FetchRefused
        return url, "text/html", pages[url].encode()

    monkeypatch.setattr(cs, "fetch", fake_fetch)
    urls = [
        "https://blog.example/",
        "https://old.ucsd.edu/dsc10/",
        "https://gone.example/",
        *list(pages)[:2],
    ]
    verdicts = cs.find_course_site(DSC10, urls)
    assert [(v.url, v.decision) for v in verdicts] == [
        ("https://dsc10.com/", cs.ACCEPT),  # one entry for the site, its best page
        ("https://old.ucsd.edu/dsc10/", cs.ASK),
        ("https://blog.example/", cs.REJECT),
    ]


# --- only public https pages ---


@pytest.mark.parametrize(
    "url",
    [
        "http://dsc10.com/",
        "https://localhost/x",
        "https://127.0.0.1/x",
        "https://10.0.0.5/x",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/x",
        "https://user:pw@dsc10.com/",
        "https://dsc10.com:8443/",
        "file:///etc/passwd",
        "https://",
    ],
)
def test_private_local_and_non_https_addresses_are_refused(url, monkeypatch):
    # Name lookups are answered here, so the test needs no network.
    table = {"localhost": "127.0.0.1", "dsc10.com": "93.184.216.34"}

    def lookup(host, port, **_):
        ip = table.get(host, host.strip("[]"))
        return [(None, None, None, "", (ip, port))]

    monkeypatch.setattr(cs.socket, "getaddrinfo", lookup)
    with pytest.raises(cs.FetchRefused):
        cs.checked_url(url)


def test_a_public_https_address_is_allowed_and_loses_its_fragment(monkeypatch):
    monkeypatch.setattr(
        cs.socket, "getaddrinfo", lambda h, p, **_: [(None, None, None, "", ("93.184.216.34", p))]
    )
    assert cs.checked_url("https://dsc10.com/a?b=1#frag") == "https://dsc10.com/a?b=1"


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    monkeypatch.setattr(
        cs.socket, "getaddrinfo", lambda h, p, **_: [(None, None, None, "", ("192.168.1.10", p))]
    )
    with pytest.raises(cs.FetchRefused):
        cs.checked_url("https://looks-public.example/")


# --- lecture material ---


def test_lecture_numbers_are_read_from_names_and_labels():
    assert cs.lecture_number("lec11_002.pdf") == 11
    assert cs.lecture_number("/resources/lectures/lec03/lec03.html") == 3
    assert cs.lecture_number("slides", "Lecture 12: joins") == 12
    assert cs.lecture_number("Lecture%205.pdf") == 5
    assert cs.lecture_number("Electric 5", "collection.pdf", "hw3") is None
    assert cs.lecture_number("lecture 99") is None, "not a lecture number"


def test_an_html_lecture_page_is_cut_at_its_headings():
    html = b"""<html><head><title>Notebook</title><script>var x=1</script></head><body>
      <nav>menu</nav><h1>Lecture 3 - Strings and Arrays</h1><p>DSC 10, Fall 2026</p>
      <h2>Strings</h2><p>A string is text.</p><pre>"a" + "b"</pre>
      <h2>Arrays</h2><p>An array holds one type.</p></body></html>"""
    title, sections = cs.html_sections(html)
    assert title == "Lecture 3 - Strings and Arrays"
    assert len(sections) == 3
    assert sections[1].startswith("Strings A string is text.") and '"a" + "b"' in sections[1]
    assert sections[2] == "Arrays An array holds one type."
    assert "menu" not in " ".join(sections) and "var x" not in " ".join(sections)


def test_materials_come_from_the_site_itself_with_one_copy_per_lecture(monkeypatch):
    home = """<title>DSC 10</title><table>
      <tr><td>Fri Sep 25</td><td>Lecture 1</td><td><a href="/lectures/lec01/lec01.html">notes</a></td></tr>
      <tr><td>Mon Sep 28</td><td>Lecture 2</td><td><a href="/files/intro-deck.pdf">slides</a></td></tr>
      <tr><td>Wed Sep 30</td><td><a href="/lectures/lec03_001.pdf">001</a> <a href="/lectures/lec03_002.pdf">002</a></td></tr>
      <tr><td>Homework</td><td><a href="/hw/hw1.pdf">Homework 1</a></td></tr>
      <tr><td><a href="https://elsewhere.example/normal-table.pdf">tables</a></td></tr>
      <tr><td><a href="/resources/">Resources</a> <a href="/data.zip">data</a></td></tr></table>"""
    resources = '<a href="https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#slide=1">Diagrams deck</a>'
    lec01 = '<h1>Lecture 1</h1><a href="/lectures/lec01/lec01.html">self</a>'
    pages = {
        "https://dsc10.com/": home,
        "https://dsc10.com/resources/": resources,
        "https://dsc10.com/lectures/lec01/lec01.html": lec01,
    }

    def fake_fetch(url, **_):
        if url not in pages:
            raise cs.FetchRefused
        return url, "text/html", pages[url].encode()

    monkeypatch.setattr(cs, "fetch", fake_fetch)
    found = [(m.lecture_no, m.kind, m.url) for m in cs.lecture_materials("https://dsc10.com/")]
    assert found == [
        (1, "html", "https://dsc10.com/lectures/lec01/lec01.html"),
        (2, "pdf", "https://dsc10.com/files/intro-deck.pdf"),  # numbered by its row
        (3, "pdf", "https://dsc10.com/lectures/lec03_001.pdf"),  # one copy of the two sections
        (
            None,
            "pdf",
            "https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/export/pdf",
        ),
    ]


def test_a_pdf_on_the_web_opens_at_the_cited_page():
    from app import answer

    assert (
        answer.document_url("https://dsc10.com/lec11.pdf", 4)
        == "https://dsc10.com/lec11.pdf#page=4"
    )
    assert answer.document_url("https://dsc10.com/lec03.html", 4) == "https://dsc10.com/lec03.html"
    canvas = "https://canvas.ucsd.edu/courses/1/files/7"
    assert answer.document_url(canvas, 4) == canvas


# --- review fixes ---


def test_the_client_connects_through_the_checking_backend():
    with cs.new_client() as client:
        assert isinstance(client._transport._pool._network_backend, cs._PublicOnlyBackend)


def test_the_connection_goes_to_an_address_checked_at_connect_time(monkeypatch):
    seen = []
    monkeypatch.setattr(
        cs.httpcore.SyncBackend, "connect_tcp", lambda self, host, *a, **k: seen.append(host)
    )
    answer = {"ip": "93.184.216.34"}
    monkeypatch.setattr(
        cs.socket, "getaddrinfo", lambda h, p, **_: [(None, None, None, "", (answer["ip"], p))]
    )
    cs._PublicOnlyBackend().connect_tcp("dsc10.example", 443)
    assert seen == ["93.184.216.34"], "connects to the checked address, not the name"
    # The name now answers with a private address (DNS rebinding): no connection is made.
    answer["ip"] = "127.0.0.1"
    with pytest.raises(cs.httpcore.ConnectError):
        cs._PublicOnlyBackend().connect_tcp("dsc10.example", 443)
    assert seen == ["93.184.216.34"]


def test_off_ucsd_a_page_needs_the_lecture_calendar_to_be_used_without_asking():
    words = "DSC 10, Fall 2026 at UC San Diego. Instructors: Peter Chi, Janine Tiefenbruck."
    assert judge(words).decision == cs.ASK
    assert judge(words, url="https://dsc10.ucsd.edu/").decision == cs.ACCEPT
    assert judge(f"{words} {SCHEDULE}").decision == cs.ACCEPT


def test_the_course_code_counts_only_in_the_host_or_path():
    v = cs.judge(DSC10, "https://x.ucsd.edu/?q=dsc10", "Home", "Fall 2026, UC San Diego, Peter Chi")
    assert v.decision == cs.REJECT


@pytest.mark.parametrize(
    "href, ok",
    [
        ("https://site.example/dsc10/lec01.pdf", True),
        ("https://SITE.example/dsc10/a/b.pdf", True),
        ("https://site.example/dsc10-old/lec01.pdf", False),
        ("https://site.example/dsc10/../admin/x.pdf", False),
        ("https://site.example/dsc10/%2e%2e/admin/x.pdf", False),
        ("https://site.example/dsc10/%252E%252E/admin/x.pdf", False),
        ("https://site.example/dsc10/..%5cadmin/x.pdf", False),
        ("https://site.example/dsc10", True),  # the folder itself
        ("https://site.example/dsc10/sub/lec02.html", True),
        ("https://other.example/dsc10/lec01.pdf", False),
    ],
)
def test_same_site_compares_whole_folders(href, ok):
    root = cs.urlsplit("https://site.example/dsc10/index.html")
    assert cs._same_site(href, root) is ok
