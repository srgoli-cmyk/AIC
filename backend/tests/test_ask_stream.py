"""POST /ask/stream with a mocked streaming model. The upstream is an httpx.MockTransport that
yields server-sent-event bytes; no test here reaches the network (see netguard.py)."""

import asyncio
import json
import logging
import re
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from netguard import NetworkBlocked

from app import answer, auth_db, db, ingest
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "contracts/fixtures/sync_sample.json").read_text())
CID = "canvas-999999"
FAKE_KEY = "sk-or-FAKE-KEY-0123456789"
SECRET_TEXT = "SECRETREPLYWORDS"  # noqa: S105  (a marker for "no text in the logs", not a secret)

client = TestClient(app)

# --- a fake OpenRouter that streams ---


def chunk(text=None, **extra):
    """One `data:` event of OpenRouter's stream."""
    body = {"choices": [{"delta": {"content": text}}]} if text is not None else {}
    return ("data: " + json.dumps({**body, **extra}) + "\n\n").encode()


DONE = b"data: [DONE]\n\n"


class Upstream(httpx.AsyncByteStream):
    """The body of the fake response. `closed` shows that the response was closed."""

    def __init__(self, parts, then=None):
        self.parts = parts
        self.then = then  # optional coroutine function run after the parts (e.g. to hang)
        self.closed = False

    async def __aiter__(self):
        for part in self.parts:
            yield part
            await asyncio.sleep(0)
        if self.then:
            await self.then()

    async def aclose(self):
        self.closed = True


class FakeOpenRouter:
    def __init__(self, monkeypatch):
        self.requests = []
        self.bodies = []
        self.status = 200
        self.parts = []
        self.then = None
        self.raises = None
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            answer.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(self._handle), **kw),
        )

    def _handle(self, request):
        self.requests.append(request)
        if self.raises:
            raise self.raises
        self.body = Upstream(self.parts, self.then)
        return httpx.Response(
            self.status, headers={"content-type": "text/event-stream"}, stream=self.body
        )

    def reply(self, *texts):
        self.parts = [chunk(t) for t in texts] + [DONE]


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setattr(answer, "settings", replace(answer.settings, openrouter_api_key=FAKE_KEY))
    return FakeOpenRouter(monkeypatch)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(answer, "settings", replace(answer.settings, openrouter_api_key=FAKE_KEY))


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    """(event, data) for every frame; checks the frame shape on the way."""
    frames = []
    for block in raw.split("\n\n"):
        if not block:
            continue
        lines = block.split("\n")
        assert len(lines) == 2, block  # one event line, one data line, no raw newline
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ")
        frames.append((lines[0][7:], json.loads(lines[1][6:])))
    return frames


def _stream(question="What is the product rule?", course=CID, **extra):
    r = client.post("/ask/stream", json={"course_id": course, "question": question, **extra})
    return r


def _events(**kwargs):
    r = _stream(**kwargs)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    return parse_sse(r.text)


def _sync():
    assert client.post("/sync", json=SAMPLE).status_code == 200


def collect(messages=None, **kwargs):
    async def run():
        return [t async for t in answer.stream_llm(messages or [], **kwargs)]

    return asyncio.run(run())


# --- stream_llm: reading the upstream ---


def test_stream_llm_sends_the_same_request_as_call_llm_plus_stream_options(upstream):
    upstream.reply("Hi")
    assert collect([{"role": "user", "content": "q"}]) == ["Hi"]
    (request,) = upstream.requests
    body = json.loads(request.content)
    assert body["model"] == answer.settings.openrouter_model
    assert body["temperature"] == answer.TEMPERATURE
    assert body["max_tokens"] == answer.MAX_TOKENS
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"


def test_stream_llm_takes_model_and_max_tokens_options(upstream):
    upstream.reply("Hi")
    collect([], model="some/model", max_tokens=7)
    body = json.loads(upstream.requests[0].content)
    assert (body["model"], body["max_tokens"]) == ("some/model", 7)


def test_stream_llm_ignores_comments_empty_deltas_and_stops_at_done(upstream):
    upstream.parts = [
        b": OPENROUTER PROCESSING\n\n",
        chunk("Hel"),
        b": keep-alive\n\n",
        chunk(""),  # an empty delta
        chunk(None, choices=[{"delta": {"role": "assistant"}}]),  # no content
        chunk("lo"),
        DONE,
        chunk("after the end"),  # nothing after [DONE] is read
    ]
    assert collect() == ["Hel", "lo"]


def test_stream_llm_reads_the_usage_chunk_that_has_no_choices(upstream, caplog):
    upstream.parts = [chunk("a"), b'data: {"choices": [], "usage": {"completion_tokens": 42}}\n\n']
    with caplog.at_level(logging.INFO, logger="aic"):
        assert collect() == ["a"]
    (line,) = [r.getMessage() for r in caplog.records if "answer stream" in r.getMessage()]
    assert "tokens=42" in line


def test_the_log_line_has_numbers_only_and_no_text(upstream, caplog):
    upstream.parts = [chunk(SECRET_TEXT), b'data: {"usage": {"completion_tokens": 5}}\n\n', DONE]
    with caplog.at_level(logging.DEBUG):
        collect([{"role": "user", "content": "SECRETQUESTION"}])
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("answer")]
    assert len(lines) == 1
    assert re.fullmatch(r"answer stream tokens=\d+ first_token_ms=\d+ latency_ms=\d+", lines[0])
    for secret in (SECRET_TEXT, "SECRETQUESTION", FAKE_KEY):
        assert secret not in caplog.text


def test_unknown_usage_is_logged_as_unknown(upstream, caplog):
    upstream.reply("a")
    with caplog.at_level(logging.INFO, logger="aic"):
        collect()
    assert "answer stream tokens=unknown first_token_ms=" in caplog.text


@pytest.mark.parametrize(
    "parts",
    [
        [chunk("part"), b'data: {"error": {"message": "SECRET-ERR", "code": 500}}\n\n'],
        [b'data: {"error": {"message": "SECRET-ERR"}}\n\n'],
        [chunk("part"), b"data: this is not json SECRET-ERR\n\n"],
        [chunk("part"), b"data: [1, 2]\n\n"],  # valid JSON of the wrong shape
        [DONE],  # no text at all
        [],
    ],
)
def test_stream_llm_failures_are_one_error_and_log_no_text(upstream, caplog, parts):
    upstream.parts = parts
    with caplog.at_level(logging.DEBUG), pytest.raises(answer.AnswerUnavailable):
        collect()
    assert "SECRET-ERR" not in caplog.text and FAKE_KEY not in caplog.text
    assert "answer stream tokens" not in caplog.text  # no success line


@pytest.mark.parametrize("status", [401, 429, 500])
def test_stream_llm_upstream_http_error(upstream, caplog, status):
    upstream.status = status
    upstream.parts = [b'{"error": "SECRET-BODY"}']
    with caplog.at_level(logging.DEBUG), pytest.raises(answer.AnswerUnavailable):
        collect()
    assert f"status {status}" in caplog.text and "SECRET-BODY" not in caplog.text
    assert upstream.body.closed


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("SECRET"), httpx.ReadTimeout("SECRET"), httpx.ConnectTimeout("")]
)
def test_stream_llm_network_errors(upstream, caplog, exc):
    upstream.raises = exc
    with caplog.at_level(logging.DEBUG), pytest.raises(answer.AnswerUnavailable):
        collect()
    assert "SECRET" not in caplog.text and type(exc).__name__ in caplog.text


def test_stream_llm_total_deadline(upstream, caplog):
    async def hang():
        await asyncio.sleep(30)

    upstream.parts = [chunk("a")]
    upstream.then = hang
    with caplog.at_level(logging.DEBUG), pytest.raises(answer.AnswerUnavailable):
        collect(timeout=0.2)
    assert "timeout" in caplog.text
    assert upstream.body.closed


def test_stream_llm_non_ascii_key_is_an_error_not_a_crash(monkeypatch, upstream, caplog):
    monkeypatch.setattr(answer, "settings", replace(answer.settings, openrouter_api_key="clé-é"))
    with caplog.at_level(logging.DEBUG), pytest.raises(answer.AnswerUnavailable):
        collect()
    assert "clé" not in caplog.text


def test_closing_the_generator_closes_the_upstream_response(upstream):
    upstream.reply("one", "two", "three")

    async def run():
        deltas = answer.stream_llm([])
        assert await anext(deltas) == "one"
        assert not upstream.body.closed
        await deltas.aclose()  # what the consumer does when it stops early
        return upstream.body.closed

    assert asyncio.run(run()) is True


def test_cancelling_the_consumer_closes_the_upstream_response(upstream):
    async def hang():
        await asyncio.sleep(30)

    upstream.parts = [chunk("one")]
    upstream.then = hang

    async def run():
        async def consume():
            async for _ in answer.stream_llm([]):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)  # now waiting on the upstream
        assert not upstream.body.closed
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return upstream.body.closed

    assert asyncio.run(run()) is True


# --- the endpoint ---


@pytest.mark.integration
def test_deltas_come_in_order_then_one_final(upstream):
    _sync()
    upstream.reply("Use the product ", "rule ", "[Lecture 8, 0:00].")
    events = _events()
    assert [e for e, _ in events] == ["delta", "delta", "delta", "final"]
    assert [d["text"] for e, d in events[:3]] == ["Use the product ", "rule ", "[Lecture 8, 0:00]."]
    assert events[-1][1]["answer"] == "Use the product rule [Lecture 8, 0:00]."
    assert [s["entry_id"] for s in events[-1][1]["sources"]] == ["1_fake0001"]


@pytest.mark.integration
def test_the_frames_are_exactly_event_data_blank_line(upstream):
    _sync()
    upstream.reply("See [Lecture 9, 0:00].")
    raw = _stream().text
    assert raw.startswith('event: delta\ndata: {"text": "See [Lecture 9, 0:00]."}\n\n')
    assert "\nevent: final\ndata: {" in raw and raw.endswith("}\n\n")


@pytest.mark.integration
def test_final_equals_what_ask_returns_for_the_same_reply(upstream, monkeypatch):
    _sync()
    reply = "Use the product rule [Lecture 8, 0:00]. Rates are velocities [Lecture 9, 0:00]."
    monkeypatch.setattr(answer, "call_llm", lambda messages: reply)
    expected = client.post("/ask", json={"course_id": CID, "question": "q", "top_k": 8}).json()
    assert len(expected["sources"]) == 2
    # The same reply, cut into odd pieces (and with spaces around it, which /ask strips).
    upstream.reply("  Use the prod", "uct rule [Lecture 8, 0:", "00]. Rates are velocities ")
    upstream.parts[-1:-1] = [chunk("[Lecture 9, 0:00].  ")]
    events = _events(question="q", top_k=8)
    assert events[-1] == ("final", expected)


@pytest.mark.integration
def test_the_question_is_in_the_prompt_sent_upstream(upstream):
    _sync()
    upstream.reply("[Lecture 8, 0:00]")
    _events(question="Why is the sky blue?")
    prompt = json.loads(upstream.requests[0].content)["messages"][1]["content"]
    assert "Why is the sky blue?" in prompt and "[Lecture 8, 0:00]" in prompt


@pytest.mark.integration
def test_invalid_citations_are_removed_in_final_but_not_in_deltas(upstream):
    _sync()
    upstream.reply("Product rule [Lecture 99, 0:10] ", "then [Lecture 8, 0:00].")
    events = _events()
    assert events[0] == ("delta", {"text": "Product rule [Lecture 99, 0:10] "})
    final = events[-1][1]
    assert final["answer"] == "Product rule then [Lecture 8, 0:00]."
    assert [s["entry_id"] for s in final["sources"]] == ["1_fake0001"]


@pytest.mark.integration
def test_no_valid_citation_gives_not_covered(upstream):
    _sync()
    upstream.reply("It is said [Lecture 99, 0:10].")
    events = _events()
    assert events[0][0] == "delta"
    assert events[-1] == ("final", {"answer": answer.NOT_COVERED, "sources": []})


@pytest.mark.integration
def test_comments_and_done_do_not_reach_the_client(upstream):
    _sync()
    upstream.parts = [b": OPENROUTER PROCESSING\n\n", chunk("[Lecture 8, 0:00]"), DONE]
    events = _events()
    assert [e for e, _ in events] == ["delta", "final"]
    assert "OPENROUTER" not in json.dumps(events) and "DONE" not in json.dumps(events)


@pytest.mark.integration
def test_text_with_newlines_and_quotes_stays_on_one_data_line(upstream):
    _sync()
    upstream.reply('Line one\n\nline "two"   [Lecture 8, 0:00]')
    events = _events()  # parse_sse fails on a raw newline inside a frame
    assert events[0][1]["text"] == 'Line one\n\nline "two"   [Lecture 8, 0:00]'


@pytest.mark.integration
@pytest.mark.parametrize(
    "parts, status",
    [
        ([chunk("[Lecture 8, 0:00] part"), b'data: {"error": {"message": "x"}}\n\n'], 200),
        ([], 500),
    ],
)
def test_a_failing_model_call_gives_an_error_event(upstream, parts, status):
    _sync()
    upstream.parts, upstream.status = parts, status
    r = _stream()
    assert r.status_code == 200  # the stream had started (the status is already sent)
    events = parse_sse(r.text)
    assert events[-1] == ("error", {"status": 502, "detail": "answer service unavailable"})
    assert "final" not in [e for e, _ in events]
    assert [e for e, _ in events].count("error") == 1


@pytest.mark.integration
def test_a_timeout_gives_an_error_event(upstream):
    _sync()
    upstream.raises = httpx.ReadTimeout("slow")
    assert _events() == [("error", {"status": 502, "detail": "answer service unavailable"})]


@pytest.mark.integration
def test_unknown_course_is_a_plain_404(upstream):
    r = _stream(course="canvas-1")
    assert r.status_code == 404 and r.json() == {"detail": "course not synced"}
    assert upstream.requests == []


@pytest.mark.integration
def test_missing_key_is_a_plain_503_before_anything_is_embedded(fake_model):
    _sync()
    embedded = len(fake_model.texts)
    r = _stream()
    assert r.status_code == 503
    assert r.headers["content-type"] == "application/json"
    assert r.json() == {"detail": "answer service not configured"}
    assert len(fake_model.texts) == embedded


@pytest.mark.parametrize(
    "body",
    [
        {"course_id": CID},
        {"course_id": CID, "question": ""},
        {"course_id": CID, "question": "q", "top_k": 21},
        {"course_id": CID, "question": "q", "entry_id": "bad"},
        {"course_id": CID, "question": "q", "token": "x"},
    ],
)
def test_invalid_bodies_are_a_plain_422(body):
    r = client.post("/ask/stream", json=body)
    assert r.status_code == 422 and r.headers["content-type"] == "application/json"


@pytest.mark.integration
def test_nothing_indexed_sends_only_final_and_no_model_call(upstream, monkeypatch):
    monkeypatch.setattr(ingest, "ingest_course", lambda course_id, *, user_id: None)
    _sync()
    assert _events() == [("final", {"answer": answer.NOTHING_INDEXED, "sources": []})]
    assert upstream.requests == []


@pytest.mark.integration
def test_the_endpoint_log_has_numbers_only(upstream, caplog):
    _sync()
    upstream.reply(SECRET_TEXT, " [Lecture 8, 0:00]")
    with caplog.at_level(logging.DEBUG):
        _events(question="SECRETQUESTION")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("answer")]
    assert len(lines) == 1 and lines[0].startswith("answer stream tokens=")
    for secret in (SECRET_TEXT, "SECRETQUESTION", FAKE_KEY):
        assert secret not in caplog.text


@pytest.mark.integration
def test_closing_the_event_generator_early_closes_the_upstream_response(upstream):
    _sync()
    upstream.reply("one", "two", "three")
    given = answer.prepare(CID, "q", 8, user_id=db.LOCAL_USER_ID)

    async def run():
        events = answer.answer_events("q", given)
        assert await anext(events) == ("delta", {"text": "one"})
        assert not upstream.body.closed
        await events.aclose()  # the client went away
        return upstream.body.closed

    assert asyncio.run(run()) is True


@pytest.mark.integration
def test_cancelling_the_event_generator_closes_the_upstream_response(upstream):
    _sync()

    async def hang():
        await asyncio.sleep(30)

    upstream.parts = [chunk("one")]
    upstream.then = hang
    given = answer.prepare(CID, "q", 8, user_id=db.LOCAL_USER_ID)

    async def run():
        async def consume():
            async for _ in answer.answer_events("q", given):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return upstream.body.closed

    assert asyncio.run(run()) is True


@pytest.mark.integration
@pytest.mark.integration
def test_a_slow_consumer_does_not_stop_the_deadline_from_becoming_an_error_event(
    upstream, monkeypatch
):
    _sync()

    async def hang():
        await asyncio.sleep(30)

    upstream.parts = [chunk("[Lecture 8, 0:00] one")]
    upstream.then = hang
    given = answer.prepare(CID, "q", 8, user_id=db.LOCAL_USER_ID)
    real = answer.stream_llm
    monkeypatch.setattr(answer, "stream_llm", lambda messages, **kw: real(messages, timeout=0.3))

    async def run():
        got = []
        async for event in answer.answer_events("q", given):
            got.append(event)
            # Paused at `yield` past the deadline. The sleep must not be cancelled by it.
            await asyncio.sleep(0.6)
        return got

    got = asyncio.run(run())
    assert [e for e, _ in got] == ["delta", "error"]


def test_stream_llm_deadline_passing_while_the_consumer_is_slow(upstream):
    async def hang():
        await asyncio.sleep(30)

    upstream.parts = [chunk("a")]
    upstream.then = hang

    async def run():
        deltas = answer.stream_llm([], timeout=0.3)
        assert await anext(deltas) == "a"
        await asyncio.sleep(0.6)  # not cancelled: nothing is inside a timeout scope here
        with pytest.raises(answer.AnswerUnavailable):
            await anext(deltas)
        return upstream.body.closed

    assert asyncio.run(run()) is True


@pytest.mark.integration
def test_a_client_disconnect_closes_the_upstream_response_through_the_asgi_app(upstream):
    _sync()

    async def hang():
        await asyncio.sleep(30)

    upstream.parts = [chunk("one")]
    upstream.then = hang
    body = json.dumps({"course_id": CID, "question": "q", "top_k": 8}).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/ask/stream",
        "raw_path": b"/ask/stream",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"host", b"localhost")],
        "client": ("127.0.0.1", 1),
        "server": ("localhost", 80),
    }

    async def run():
        sent_body = False
        first_delta_sent = asyncio.Event()

        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await first_delta_sent.wait()
            await asyncio.sleep(0.1)
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                first_delta_sent.set()
                await asyncio.sleep(30)  # the send task is stuck in `send` when the client leaves

        await asyncio.wait_for(app(scope, receive, send), timeout=10)
        return upstream.body.closed

    assert asyncio.run(run()) is True


@pytest.mark.integration
def test_the_response_body_generator_closes_the_model_call_when_closed(upstream):
    """Closing the body iterator closes the model call. (Starlette does not call aclose() itself
    on a disconnect; the ASGI test above covers that through `_ClosingStreamingResponse`.)"""
    _sync()
    upstream.reply("one", "two", "three")

    async def run():
        from app.main import ask_stream
        from app.schemas import AskRequest

        user = auth_db.UserRecord(id=db.LOCAL_USER_ID, email="student@ucsd.edu", name="Test")
        response = await ask_stream(AskRequest(course_id=CID, question="q", top_k=8), user)
        body = response.body_iterator
        assert (await anext(body)).startswith("event: delta")
        await body.aclose()
        return upstream.body.closed

    assert asyncio.run(run()) is True


def test_the_network_guard_covers_async_clients():
    async def go():
        async with httpx.AsyncClient() as http:
            await http.get("https://openrouter.ai/")

    with pytest.raises(NetworkBlocked):
        asyncio.run(go())
