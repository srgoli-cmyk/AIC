"""Find a course's own website and the slides on it, when they are not on Canvas.

Finding is two separate steps, because a search engine (or a model) can be confidently wrong:

1. Candidates. A web search for the course, through OpenRouter's web plugin. Only the URLs the
   search itself returned are used; nothing the model writes is trusted.
2. Checking. Every candidate page is fetched and scored against what Canvas told us about the
   course: its code, school, term, instructors and lecture dates. A site is used without asking
   only when the course code, the school and this term all match. A site for the right course in
   another term, or one whose term cannot be told, is returned as "ask": the student decides.

Only public https pages are fetched: no credentials are sent, private and local addresses are
refused, and redirects are followed by hand so each hop is checked.
"""

import hashlib
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpcore
import httpx

from . import db, slides
from .config import settings

log = logging.getLogger("aic")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SEARCH_TIMEOUT_SEC = 60.0
FETCH_TIMEOUT_SEC = 15.0
MAX_PAGE_BYTES = 2_000_000
MAX_REDIRECTS = 4
MAX_CANDIDATES = 8
MAX_SITE_PAGES = 25
USER_AGENT = "LectureSearch/0.1 (student course tool)"

TERMS = {
    "FA": "Fall",
    "WI": "Winter",
    "SP": "Spring",
    "S1": "Summer",
    "S2": "Summer",
    "SU": "Summer",
}
MONTHS = (
    "January February March April May June July August September October November December".split()
)

ACCEPT, ASK, REJECT = "accept", "ask", "reject"


class FetchRefused(Exception):
    """The URL is not a public https page, or it could not be read."""


@dataclass(frozen=True)
class CourseFacts:
    """What Canvas says about the course."""

    code: str  # as Canvas has it, e.g. "DSC010_FA26_001"
    name: str
    term: str | None  # e.g. "Fall 2026"
    instructors: tuple[str, ...] = ()
    lecture_dates: tuple[date, ...] = ()

    @property
    def subject_number(self) -> tuple[str, str] | None:
        m = re.match(r"^\s*([A-Za-z]{2,5})[\s_-]*0*(\d{1,3}[A-Za-z]{0,2})", self.code)
        return (m.group(1).upper(), m.group(2).upper()) if m else None

    @property
    def season_year(self) -> tuple[str, int] | None:
        if self.term:
            m = re.search(r"(Fall|Winter|Spring|Summer)\D{0,12}(20\d{2})", self.term, re.IGNORECASE)
            if m:
                return m.group(1).title(), int(m.group(2))
        m = re.search(r"(FA|WI|SP|S1|S2|SU)(\d{2})(?!\d)", self.code.upper())
        return (TERMS[m.group(1)], 2000 + int(m.group(2))) if m else None

    @property
    def surnames(self) -> tuple[str, ...]:
        return tuple(n.split()[-1] for n in self.instructors if n.split())


@dataclass
class Verdict:
    url: str
    decision: str  # accept | ask | reject
    score: int
    reasons: list[str] = field(default_factory=list)
    term_found: str | None = None
    title: str = ""


# --- fetching public pages safely ---


def new_client() -> httpx.Client:
    """Certificates are checked against the operating system's store when `truststore` is
    installed: some university servers send an incomplete chain that the system can complete
    and Python's bundled list cannot. Verification is never turned off."""
    verify: object = True
    try:
        import ssl  # noqa: PLC0415

        import truststore  # noqa: PLC0415 (optional)

        verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        pass
    transport = httpx.HTTPTransport(verify=verify)
    # The name is looked up again when the connection is made, and the connection goes to the
    # address that was checked then. A name that answers with a public address for the check
    # and a private one for the connection (DNS rebinding) cannot reach the private one.
    transport._pool._network_backend = _PublicOnlyBackend()  # noqa: SLF001 (httpcore has no public hook)
    return httpx.Client(
        timeout=FETCH_TIMEOUT_SEC, follow_redirects=False, verify=verify, transport=transport
    )


def _public_ips(host: str, port: int = 443) -> list[str]:
    """Every address the name resolves to, or [] if any of them is not a public address."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return []
        ips.append(str(ip))
    return ips


def _public_host(host: str) -> bool:
    return bool(_public_ips(host))


class _PublicOnlyBackend(httpcore.SyncBackend):
    """Connects to an address checked at connect time. TLS still checks the certificate against
    the name in the URL (httpcore takes the server name from the request, not from here)."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        ips = _public_ips(host, port)
        if not ips:
            raise httpcore.ConnectError("address is not public")
        return super().connect_tcp(ips[0], port, timeout, local_address, socket_options)


def checked_url(url: str) -> str:
    """The URL without its fragment if it is https on a public host, else FetchRefused."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise FetchRefused
    if parts.port not in (None, 443) or not _public_host(parts.hostname):
        raise FetchRefused
    return urlunsplit(parts._replace(fragment=""))


def fetch(url: str, *, max_bytes: int = MAX_PAGE_BYTES, client: httpx.Client | None = None):
    """GET a public https URL. Returns (final_url, content_type, body bytes). Every redirect hop
    is checked like the first URL, and the body is cut off at max_bytes."""
    own = client is None
    client = client or new_client()
    try:
        current = checked_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", current, headers={"User-Agent": USER_AGENT}) as r:
                if r.status_code in (301, 302, 303, 307, 308) and "location" in r.headers:
                    current = checked_url(urljoin(current, r.headers["location"]))
                    continue
                if r.status_code != 200:
                    raise FetchRefused
                body = bytearray()
                for part in r.iter_bytes():
                    body.extend(part)
                    if len(body) > max_bytes:
                        raise FetchRefused
                return current, r.headers.get("content-type", "").split(";")[0].strip(), bytes(body)
        raise FetchRefused
    except (httpx.HTTPError, UnicodeError, ValueError):
        raise FetchRefused from None
    finally:
        if own:
            client.close()


# --- reading a page ---


def _soup(html: bytes):
    from bs4 import BeautifulSoup  # noqa: PLC0415 (lazy)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup


def _words(node) -> str:
    return " ".join(node.get_text(" ").split())


def _row_of(a) -> str:
    """The text of the schedule row a link sits in: its nearest table row, list item or
    definition, as long as that is a row and not the whole page."""
    for parent in a.parents:
        if parent.name in ("tr", "li", "dd", "p", "div", "section"):
            text = _words(parent)
            if len(text) > 400:
                return ""
            if parent.name in ("tr", "li", "dd") or len(text) > 40:
                return text
    return ""


def page_text_and_links(html: bytes, base: str) -> tuple[str, str, list[tuple[str, str, str]]]:
    """(title, visible text, [(absolute link, link text, text of the row it is in)])."""
    soup = _soup(html)
    title = " ".join((soup.title.string or "").split()) if soup.title else ""
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"].strip())
        if href.startswith("https://"):
            links.append((href.split("#")[0], _words(a)[:200], _row_of(a)))
    return title, _words(soup), links


def html_sections(html: bytes) -> tuple[str, list[str]]:
    """(title, sections) of an HTML lecture page, such as a notebook exported to HTML. A section
    is a top heading with what follows it, so each one can be cited like a slide."""
    soup = _soup(html)
    for tag in soup(["nav", "header", "footer", "aside"]):
        tag.decompose()
    first = soup.find("h1")
    title = (
        _words(first).rstrip("¶ ").strip() if first else (_words(soup.title) if soup.title else "")
    )
    level = "h2" if len(soup.find_all("h2")) >= 2 else "h1"
    sections, current = [], []
    for node in soup.find_all(string=True):
        text = " ".join(str(node).split())
        if not text:
            continue
        heading = node.find_parent(level)
        if heading is not None and current and not getattr(heading, "_seen", False):
            sections.append(" ".join(current))
            current = []
        if heading is not None:
            heading._seen = True
        current.append(text.replace("¶", "").strip())
    if current:
        sections.append(" ".join(current))
    return title[:200], [x for x in (" ".join(sec.split()) for sec in sections) if x]


# --- checking a candidate against the course ---


def _terms_in(text: str) -> set[tuple[str, int]]:
    found = set()
    for m in re.finditer(
        r"\b(Fall|Winter|Spring|Summer)\s*(?:Quarter\s*)?,?\s*(20\d{2})\b", text, re.IGNORECASE
    ):
        found.add((m.group(1).title(), int(m.group(2))))
    for m in re.finditer(r"\b(FA|WI|SP|S1|S2|SU)\s?(\d{2})\b", text):
        found.add((TERMS[m.group(1)], 2000 + int(m.group(2))))
    return found


def _dates_in(text: str, year: int) -> set[date]:
    """Dates written as `Sep 25`, `September 25` or `9/25`, read in the course's year."""
    found = set()
    for m in re.finditer(r"\b([A-Z][a-z]{2,8})\.?\s+(\d{1,2})\b", text):
        month = next(
            (
                i
                for i, name in enumerate(MONTHS, 1)
                if name.startswith(m.group(1)[:3]) and m.group(1) in (name, name[:3], name[:4])
            ),
            None,
        )
        if month:
            try:
                found.add(date(year, month, int(m.group(2))))
            except ValueError:
                pass
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})\b", text):
        try:
            found.add(date(year, int(m.group(1)), int(m.group(2))))
        except ValueError:
            pass
    return found


def judge(facts: CourseFacts, url: str, title: str, text: str) -> Verdict:
    """Score one fetched page against the course. Pure: no network."""
    v = Verdict(url=url, decision=REJECT, score=0, title=title[:200])
    head = f"{title} {text[:3000]}"
    host = (urlsplit(url).hostname or "").lower()

    sn = facts.subject_number
    if not sn:
        v.reasons.append("course code not understood")
        return v
    subject, number = sn
    code_re = re.compile(rf"\b{subject}[\s\-_]*0*{number}\b", re.IGNORECASE)
    parts = urlsplit(url)
    in_host_or_path = bool(
        re.search(
            rf"(?<![0-9a-z]){subject}[\-_]?0*{number}(?![0-9a-z])",
            f"{host}{parts.path}",
            re.IGNORECASE,
        )
    )
    in_head = bool(code_re.search(head))
    if not (in_head or in_host_or_path):
        v.reasons.append(f"does not name {subject} {number} near the top")
        return v
    v.score += 3
    v.reasons.append(f"names {subject} {number}")
    # A page that names several other numbers of the subject is a catalog or a list, not a course.
    others = {
        m.group(1)
        for m in re.finditer(
            rf"\b{subject}[\s\-_]*0*(\d{{1,3}}[A-Z]{{0,2}})\b", text, re.IGNORECASE
        )
    }
    others.discard(number)
    if len(others) >= 6:
        v.reasons.append(
            f"lists {len(others)} other {subject} courses (a catalog, not a course site)"
        )
        v.score = 0
        return v

    on_ucsd = host == "ucsd.edu" or host.endswith(".ucsd.edu")
    ucsd = on_ucsd or bool(
        re.search(r"UC San Diego|UCSD|University of California,? San Diego", text, re.IGNORECASE)
    )
    if ucsd:
        v.score += 2
        v.reasons.append("UC San Diego")
    else:
        v.reasons.append("no sign of UC San Diego")

    wanted = facts.season_year
    terms = _terms_in(head) or _terms_in(text[:20000])
    term_ok = term_other = False
    if wanted and terms:
        if wanted in terms and (len(terms) == 1 or wanted in _terms_in(head)):
            term_ok = True
            v.score += 3
            v.term_found = f"{wanted[0]} {wanted[1]}"
            v.reasons.append(f"says {v.term_found}")
        else:
            term_other = True
            other = sorted(terms, key=lambda t: -t[1])[0]
            v.term_found = f"{other[0]} {other[1]}"
            v.reasons.append(f"is for {v.term_found}, not {wanted[0]} {wanted[1]}")
    else:
        v.reasons.append("does not say which term")

    names = [s for s in facts.surnames if re.search(rf"\b{re.escape(s)}\b", text, re.IGNORECASE)]
    if names:
        v.score += 2
        v.reasons.append("names " + ", ".join(names))
    elif facts.surnames:
        v.reasons.append("names none of the instructors")

    overlap = 0
    dates_ok = False
    if wanted and facts.lecture_dates:
        overlap = len(_dates_in(text, wanted[1]) & set(facts.lecture_dates))
        share = overlap / len(set(facts.lecture_dates))
        if overlap >= 3 and share >= 0.5:
            dates_ok = True
            v.score += 3
            v.reasons.append(
                f"lists {overlap} of the {len(set(facts.lecture_dates))} lecture dates"
            )

    # Words on a page are easy to copy. Off a ucsd.edu address the page is used without asking
    # only when it also carries this term's real lecture calendar and an instructor's name;
    # anything less is the student's call.
    if on_ucsd:
        sure = term_ok and (names or dates_ok or not facts.surnames)
    else:
        sure = term_ok and dates_ok and (names or not facts.surnames)
        if term_ok and not sure:
            v.reasons.append("not on a ucsd.edu address and the lecture calendar does not match")
    if not ucsd:
        v.decision = REJECT
    elif sure:
        v.decision = ACCEPT
    elif term_ok or names or term_other:
        # Right course, but the term or the instructor is not confirmed: the student decides.
        v.decision = ASK if v.score >= 5 else REJECT
    else:
        v.decision = REJECT
    return v


# --- candidates ---


def search_queries(facts: CourseFacts) -> list[str]:
    sn = facts.subject_number
    code = f"{sn[0]} {sn[1]}" if sn else facts.code
    term = " ".join(map(str, facts.season_year)) if facts.season_year else ""
    who = " ".join(facts.surnames[:2])
    return [
        f"UC San Diego {code} {term} course website {who}".strip(),
        f"UCSD {code} {term} lecture slides schedule".strip(),
    ]


def search_candidates(facts: CourseFacts) -> list[str]:
    """URLs a web search returns for the course. Only the search's own result links are used
    (the `url_citation` annotations); the model's words are ignored."""
    if not settings.openrouter_api_key:
        return []
    urls: list[str] = []
    for query in search_queries(facts):
        try:
            r = httpx.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                json={
                    "model": settings.openrouter_model,
                    "plugins": [{"id": "web", "max_results": 6}],
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Find the official course website for: {query}. "
                            "List the pages you found.",
                        }
                    ],
                    "max_tokens": 200,
                },
                timeout=SEARCH_TIMEOUT_SEC,
            )
            message = r.json()["choices"][0]["message"] if r.status_code == 200 else {}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            log.error("course site search failed: %s", type(exc).__name__)
            continue
        for a in message.get("annotations") or []:
            url = (a.get("url_citation") or {}).get("url") if isinstance(a, dict) else None
            if isinstance(url, str) and url.startswith("https://"):
                url = url.split("#")[0].split("?utm_")[0]
                if url not in urls:
                    urls.append(url)
    return urls[: MAX_CANDIDATES * 2]


def find_course_site(facts: CourseFacts, candidates: list[str] | None = None) -> list[Verdict]:
    """Every candidate, checked, best first. The caller uses the first `accept`, or shows the
    `ask` ones to the student."""
    urls = candidates if candidates is not None else search_candidates(facts)
    verdicts, seen = [], set()
    with new_client() as client:
        for url in urls:
            if len(verdicts) >= MAX_CANDIDATES:
                break
            try:
                final, kind, body = fetch(url, client=client)
            except FetchRefused:
                continue
            if final in seen or kind != "text/html":
                continue
            seen.add(final)
            title, text, _ = page_text_and_links(body, final)
            verdicts.append(judge(facts, final, title, text))
    order = {ACCEPT: 0, ASK: 1, REJECT: 2}
    verdicts.sort(key=lambda v: (order[v.decision], -v.score, len(v.url)))
    # One entry per site: its best page stands for it.
    best, hosts = [], set()
    for v in verdicts:
        host = urlsplit(v.url).hostname
        if v.decision == REJECT or host not in hosts:
            best.append(v)
            hosts.add(host)
    return best


# --- lecture material on the site ---

# "Lecture 5", "lec05", "lec11_002", "Slides 3": the number may be followed by anything but a digit.
_LECTURE_NO = re.compile(
    r"(?<![a-z])(?:lec(?:ture)?(?:\s*notes?)?|slides?|class|session)[\s_\-#.:]*0*(\d{1,2})(?!\d)",
    re.IGNORECASE,
)
_FILE_EXT = re.compile(r"\.(pdf|pptx)$", re.IGNORECASE)
_GOOGLE_SLIDES = re.compile(r"^https://docs\.google\.com/presentation/d/([A-Za-z0-9_-]{20,})")
_NOT_LECTURE = re.compile(
    r"\b(syllabus|homework|hw\s?\d*|worksheet|solutions?|solns?|exam|midterm|final|quiz|project"
    r"|lab\s?\d*|discussion|disc\s?\d*|reference|cheat|calendar|practice)\b",
    re.IGNORECASE,
)
# The same lecture posted once per section: lec11_001.pdf, lec11_002.pdf, ...
_SECTION_COPY = re.compile(
    r"[_\-](?:0\d\d|[a-d]0\d|sec(?:tion)?[_\-]?\w{1,3})(?=\.\w+$)", re.IGNORECASE
)

PDF, PPTX, HTML = "pdf", "pptx", "html"


@dataclass(frozen=True)
class Material:
    url: str  # where to download it
    kind: str  # pdf | pptx | html
    label: str
    found_on: str  # the page of the site that links to it
    lecture_no: int | None


def lecture_number(*texts: str) -> int | None:
    """The lecture a link belongs to, from the first text that names one."""
    for t in texts:
        m = _LECTURE_NO.search(t.replace("%20", " "))
        if m and 0 < int(m.group(1)) <= 60:
            return int(m.group(1))
    return None


def _same_site(href: str, root) -> bool:
    """On the course site's host and inside its folder, compared by whole path segments, so
    `/dsc10` does not take in `/dsc10-old` and `..` cannot climb out."""
    parts = urlsplit(href)
    if (parts.hostname or "").lower() != (root.hostname or "").lower():
        return False
    # An encoded separator, a double encoding or a path parameter could make the path a server
    # reads differ from the one checked here, so such a link is not followed.
    if re.search(r"%(?:2f|5c|25)|;", parts.path, re.IGNORECASE):
        return False
    # Decoded twice so `%2e%2e` and `%252e%252e` are seen as the `..` a server may read them as.
    path = unquote(unquote(parts.path)).replace("\\", "/")
    if any(seg in (".", "..") for seg in path.split("/")):
        return False
    folder = [s for s in root.path.rsplit("/", 1)[0].split("/") if s]
    return [s for s in path.split("/") if s][: len(folder)] == folder


def lecture_materials(site_url: str, *, max_pages: int = MAX_SITE_PAGES) -> list[Material]:
    """What the course site posts for its lectures: PDF and PowerPoint files, public Google
    Slides, and lecture pages written as HTML (notebooks). The site's own pages are followed one
    level down; a lecture page is also read for the slide decks it links to.

    Only the site itself and Google Slides are used: a link to another site is not the course's
    own material. Homework, exams, labs and the like are left out by their text, and when a
    lecture is posted once per section one copy is kept."""
    root = urlsplit(site_url)
    queue, depth, seen_pages = [site_url], {site_url: 0}, set()
    found: dict[str, Material] = {}
    with new_client() as client:
        while queue and len(seen_pages) < max_pages:
            page = queue.pop(0)
            if page in seen_pages:
                continue
            seen_pages.add(page)
            try:
                final, kind, body = fetch(page, max_bytes=8_000_000, client=client)
            except FetchRefused:
                continue
            if kind != "text/html":
                continue
            page_no = lecture_number(urlsplit(final).path)
            for href, label, row in page_text_and_links(body, final)[2]:
                name = urlsplit(href).path.rsplit("/", 1)[-1]
                number = lecture_number(label, name, row) or page_no
                google = _GOOGLE_SLIDES.match(href)
                if google:
                    target, what = (
                        f"https://docs.google.com/presentation/d/{google.group(1)}/export/pdf",
                        PDF,
                    )
                elif not _same_site(href, root):
                    continue
                elif _FILE_EXT.search(name):
                    target, what = href, name.rsplit(".", 1)[-1].lower()
                elif name.lower().endswith((".html", ".htm")) and lecture_number(name) is not None:
                    target, what = href, HTML
                else:
                    if (
                        depth[page] < 1
                        and href not in depth
                        and not re.search(
                            r"\.(zip|ipynb|png|jpe?g|gif|svg|csv|mp4|py|txt)$", name, re.IGNORECASE
                        )
                    ):
                        depth[href] = depth[page] + 1
                        queue.append(href)
                    continue
                if number is None and _NOT_LECTURE.search(f"{label} {name}"):
                    continue
                if _NOT_LECTURE.search(name) and lecture_number(name) is None:
                    continue
                if target not in found:
                    found[target] = Material(target, what, (label or name)[:200], final, number)
                # A lecture page links to itself and to its decks: read it for them too.
                if what == HTML and href not in depth and len(depth) < max_pages * 3:
                    depth[href] = 1
                    queue.append(href)
    # One copy of a lecture that is posted per section.
    kept: dict[tuple, Material] = {}
    for m in found.values():
        base = _SECTION_COPY.sub("", urlsplit(m.url).path)
        key = (m.lecture_no, base) if m.lecture_no is not None else (m.url,)
        kept.setdefault(key, m)
    return sorted(kept.values(), key=lambda m: (m.lecture_no is None, m.lecture_no or 0, m.url))


# --- storing the site's material ---

MAX_MATERIALS = 80


@dataclass
class SiteSync:
    found: int = 0
    stored: int = 0
    unchanged: int = 0
    unreadable: int = 0
    failed: int = 0


def doc_id_for(url: str) -> str:
    return "web-" + hashlib.sha256(url.encode()).hexdigest()[:24]


def facts_for(course_id: str) -> CourseFacts | None:
    """What the backend knows about a synced course, for checking a site against it."""
    row = db.course_facts(course_id)
    if row is None:
        return None
    code, name, term, instructors, dates = row
    return CourseFacts(
        code=code, name=name, term=term, instructors=tuple(instructors), lecture_dates=tuple(dates)
    )


def check_site(facts: CourseFacts, url: str) -> Verdict:
    """Fetch one page the student picked and judge it like a search result. A page that cannot be
    fetched is rejected."""
    try:
        final, kind, body = fetch(url)
    except FetchRefused:
        return Verdict(url=url, decision=REJECT, score=0, reasons=["could not open the page"])
    if kind != "text/html":
        return Verdict(url=final, decision=REJECT, score=0, reasons=["not a web page"])
    title, text, _ = page_text_and_links(body, final)
    return judge(facts, final, title, text)


class SiteChanged(Exception):
    """Raised by a sync's progress callback: the student forgot this site or chose another, so
    the sync stops."""


def sync_site(course_id: str, site_url: str, progress=None) -> SiteSync:
    """Download the lecture material of a checked course site and store it as the course's
    documents (source "web"), each with the lecture number it belongs to. A file whose bytes are
    unchanged is skipped. Embedding runs here, since this is already a background job."""
    out = SiteSync()
    materials = lecture_materials(site_url)[:MAX_MATERIALS]
    out.found = len(materials)
    known = {d.doc_id: d.version for d in db.list_documents(course_id)}
    if progress:
        progress(out)
    with new_client() as client:
        for m in materials:
            if progress:
                progress(out)
            doc_id = doc_id_for(m.url)
            try:
                final, kind, body = fetch(m.url, max_bytes=slides.MAX_BYTES, client=client)
            except FetchRefused:
                out.failed += 1
                continue
            version = hashlib.sha256(body).hexdigest()[:32]
            if known.get(doc_id) == version:
                out.unchanged += 1
                continue
            name = urlsplit(m.url).path.rsplit("/", 1)[-1]
            pages = None
            link = m.url
            if m.kind == HTML:
                title, pages = html_sections(body)
                name = title or name
            elif "docs.google.com" in m.url:
                # The label on the site is often just "slides"; the deck's own link is the name.
                name = m.label if len(m.label) > 8 else f"Google Slides {doc_id[-6:]}"
                link = m.url.removesuffix("/export/pdf")
            if m.lecture_no is not None and not re.search(r"lec", name, re.IGNORECASE):
                name = f"Lecture {m.lecture_no} {name}"
            try:
                slides.store(
                    course_id,
                    doc_id,
                    source="web",
                    name=name,
                    url=link,
                    version=version,
                    data=body,
                    lecture_no=m.lecture_no,
                    pages=pages,
                )
            except slides.UnreadableDocument:
                out.unreadable += 1
                continue
            except db.ValueRejected:
                out.failed += 1
                continue
            slides.embed_document(doc_id)
            out.stored += 1
    if materials:
        if progress:
            progress(out)  # last chance to stop, before removing anything
        # What the site no longer posts is not searched any more.
        db.delete_web_documents_except(course_id, {doc_id_for(m.url) for m in materials})
    log.info(
        "course site sync: found=%d stored=%d unchanged=%d unreadable=%d failed=%d",
        out.found,
        out.stored,
        out.unchanged,
        out.unreadable,
        out.failed,
    )
    return out
