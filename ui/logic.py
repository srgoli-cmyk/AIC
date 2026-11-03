"""Backend calls and display helpers for the Streamlit page."""

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
    name = f"Lecture {int(match.group(1))}" if match else source["lecture_title"]
    return f"{name}, {source['timestamp']}"


def escape_markdown(text: str) -> str:
    """Keeps a lecture title from acting as markup inside a source link's label."""
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|~$:])", r"\\\1", text)


def source_link(source: dict) -> str:
    label = escape_markdown(source_label(source))
    url = source["url"]
    if not SOURCE_URL.fullmatch(url):
        return label
    return f"[{label}]({url})"
