"""Backend calls and display helpers for the Streamlit page."""

import html
import re

import httpx

ASK_TIMEOUT = 120  # seconds; the answer service alone may take up to 60
LIST_TIMEOUT = 10
UNREADABLE = "The backend sent a response this page cannot read. Is BACKEND_URL right?"
COURSE_KEYS = {"course_id", "code", "name", "lectures", "indexed"}
SOURCE_KEYS = {"lecture_title", "timestamp", "url", "snippet"}
# The same rule the sync contract applies to media_url.
SOURCE_URL = re.compile(r"https://canvaskaf\.ucsd\.edu/media/[^\s()<>]+")


class BackendError(Exception):
    """A message that is safe to show on the page."""


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) else f"the backend returned {response.status_code}"


def _json(call) -> object:
    try:
        response = call()
    except httpx.HTTPError as exc:
        raise BackendError("Could not reach the backend. Is make dev running?") from exc
    if response.status_code != 200:
        raise BackendError(_detail(response))
    try:
        return response.json()
    except ValueError as exc:
        raise BackendError(UNREADABLE) from exc


def _has_keys(value: object, keys: set[str]) -> bool:
    return isinstance(value, dict) and keys <= value.keys()


def fetch_courses(base_url: str) -> list[dict]:
    courses = _json(lambda: httpx.get(f"{base_url}/courses", timeout=LIST_TIMEOUT))
    if not isinstance(courses, list) or not all(_has_keys(c, COURSE_KEYS) for c in courses):
        raise BackendError(UNREADABLE)
    return courses


def ask(base_url: str, course_id: str, question: str) -> dict:
    body = {"course_id": course_id, "question": question}
    answer = _json(lambda: httpx.post(f"{base_url}/ask", json=body, timeout=ASK_TIMEOUT))
    if (
        not _has_keys(answer, {"answer", "sources"})
        or not isinstance(answer["sources"], list)
        or not all(_has_keys(s, SOURCE_KEYS) for s in answer["sources"])
    ):
        raise BackendError(UNREADABLE)
    return answer


def course_label(course: dict) -> str:
    status = "indexed" if course["indexed"] else "not indexed yet"
    lectures = course["lectures"]
    noun = "lecture" if lectures == 1 else "lectures"
    return f"{course['code']}: {course['name']} ({lectures} {noun}, {status})"


def source_label(source: dict) -> str:
    """'Lecture N, m:ss', or the title when the lecture has no number."""
    match = re.search(r"\bLecture\s+(\d+)", source["lecture_title"], re.IGNORECASE)
    # The same form the backend cites: no brackets, single spaces.
    title = " ".join(re.sub(r"[\[\]]", " ", source["lecture_title"]).split())
    name = f"Lecture {int(match.group(1))}" if match else title
    return f"{name}, {source['timestamp']}"


def escape_markdown(text: str) -> str:
    """Keeps a lecture title from acting as markup inside a source link's label."""
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|~$:&])", r"\\\1", text)


def source_link(source: dict) -> str:
    label = escape_markdown(_without_links(source_label(source), {}))
    url = source["url"]
    if not SOURCE_URL.fullmatch(url):
        return label
    return f"[{label}]({url})"


# --- the answer as Markdown ---
#
# An answer can carry text from captions, so it is untrusted. Outside math, every link form is
# removed from the decoded text, because the renderer finds bare URLs after decoding, and then all
# syntax that could start a link, image, tag, code span or math is escaped. Emphasis, lists,
# headings and tables still work. Code is shown as text, so nothing can hide in fake code.

_DELIMITED_INLINE = re.compile(r"(?<!\\)\\\((.+?)(?<!\\)\\\)", re.DOTALL)
_DELIMITED_DISPLAY = re.compile(r"(?<!\\)\\\[(.+?)(?<!\\)\\\]", re.DOTALL)
# A closing `$` never sits after a space or before a digit, so "$5 and $6" stays text.
_MATH = re.compile(
    r"(?<![\\$])\$\$[^$]+?(?<!\\)\$\$(?!\$)|(?<![\\$])\$(?![\s$])[^$\n]*?(?<![\s\\])\$(?![\d$])"
)
_NOT_MATH = re.compile(r"://|www\.|@|&#?\w+;|\n\s*\n", re.IGNORECASE)
_BACKSLASH_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_LINK_OR_IMAGE = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\((?:[^()]|\([^()]*\))*\)")
_URL_FORMS = (
    re.compile(r"(?:[a-z][a-z0-9+.-]*:)?//[^\s\[\]]+", re.IGNORECASE),
    re.compile(r"www\.[^\s\[\]]*", re.IGNORECASE),
    re.compile(r"[^\s\[\]<>()]*@[^\s\[\]<>()]+"),
)
_CITATION = re.compile(r"\[([^\[\]\n]*)\]")
_SYNTAX = re.compile(r"[\\\[\]<!&`$]")


def _without_links(text: str, citations: dict[str, str]) -> str:
    """Decodes the text and removes links, images and URLs, until nothing more changes: removing
    one can reveal another."""

    def replace(match: re.Match) -> str:
        if match.group(1):
            return ""
        return f"[{match.group(2)}]" if match.group(2) in citations else match.group(2)

    previous = None
    while text != previous:
        previous = text
        text = _BACKSLASH_ESCAPE.sub(r"\1", html.unescape(text))
        # Before URLs, which would otherwise take a link's closing parenthesis with them.
        linked = None
        while text != linked:
            linked = text
            text = _LINK_OR_IMAGE.sub(replace, text)
        for pattern in _URL_FORMS:
            text = pattern.sub("", text)
    return text


def _escaped(text: str) -> str:
    return _SYNTAX.sub(lambda m: "\\" + m.group(), text)


def _safe_text(text: str, citations: dict[str, str]) -> str:
    """Text outside math. The only links in the result are the citations' own."""
    text = _without_links(text, citations)
    parts: list[str] = []
    end = 0
    for match in _CITATION.finditer(text):
        if match.group(1) in citations:
            parts += [_escaped(text[end : match.start()]), citations[match.group(1)]]
            end = match.end()
    parts.append(_escaped(text[end:]))
    return "".join(parts)


def _safe_math(span: str) -> str:
    """Spaces are ignored in TeX, and they stop `][`, `](`, `]:` and `<tag` from meaning anything
    in case the renderer reads this span as text."""
    return span.replace("]", "] ").replace("<", "< ")


def answer_markdown(answer: str, sources: list[dict]) -> str:
    """The answer, safe to render: its only links are citations of `sources`, built from their
    checked URLs. Math keeps its brackets, so `$[a, b]$` still renders."""
    citations: dict[str, str] = {}
    for source in sources:
        citations.setdefault(source_label(source), source_link(source))

    def delimited(template: str):
        def convert(match: re.Match) -> str:
            is_citation = match.group(1).strip() in citations
            return f"[{match.group(1)}]" if is_citation else template.format(match.group(1))

        return convert

    text = _DELIMITED_INLINE.sub(delimited("${}$"), answer)
    text = _DELIMITED_DISPLAY.sub(delimited("$${}$$"), text)
    parts: list[str] = []
    end = 0
    for match in _MATH.finditer(text):
        if not _NOT_MATH.search(_BACKSLASH_ESCAPE.sub(r"\1", match.group())):
            parts += [_safe_text(text[end : match.start()], citations), _safe_math(match.group())]
            end = match.end()
    parts.append(_safe_text(text[end:], citations))
    return "".join(parts)
