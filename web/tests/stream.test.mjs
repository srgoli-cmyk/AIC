import { test } from "node:test";
import assert from "node:assert/strict";
import { BackendError, Stopped, StreamUnavailable } from "../src/errors.js";
import { answerHtml, streamingHtml } from "../src/render.js";
import { SSEParser, readEvents, streamAnswer } from "../src/stream.js";

const FINAL = {
  answer: "Differentiate first [Lecture 8, 0:04].",
  sources: [{ lecture_title: "Lecture 8 - X", timestamp: "0:04", url: "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/111111?st=1", snippet: "s" }],
};

const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

function body(chunks) {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      for (const x of chunks) c.enqueue(typeof x === "string" ? enc.encode(x) : x);
      c.close();
    },
  });
}

function response(chunks, { status = 200, type = "text/event-stream; charset=utf-8", json } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => (k.toLowerCase() === "content-type" ? type : null) },
    body: chunks ? body(chunks) : null,
    json: async () => {
      if (json === undefined) throw new SyntaxError("no json");
      return json;
    },
  };
}

const fetchOf = (res) => async () => (typeof res === "function" ? res() : res);

// ---- the parser ----

test("parses events whole, whatever the chunking and line endings", () => {
  const text = `: keep-alive\n${frame("delta", { text: "A" })}event: delta\r\ndata: {"text":"B"}\r\n\r\n${frame("final", FINAL)}`;
  for (const size of [1, 2, 3, 7, text.length]) {
    const p = new SSEParser();
    const events = [];
    for (let i = 0; i < text.length; i += size) events.push(...p.push(text.slice(i, i + size)));
    assert.deepEqual(
      events.map((e) => [e.event, JSON.parse(e.data)]),
      [["delta", { text: "A" }], ["delta", { text: "B" }], ["final", FINAL]],
      `chunk size ${size}`
    );
  }
});

test("a lone \\r ends a line, comments and unknown fields are skipped, data lines join", () => {
  const p = new SSEParser();
  assert.deepEqual(p.push("event: x\rid: 4\rretry: 9\r: hi\rdata: a\rdata:b\r\r"), []);
  assert.deepEqual(p.finish(), [{ event: "x", data: "a\nb" }]);
});

test("an event without data is not emitted, and one missing its blank line waits", () => {
  const p = new SSEParser();
  assert.deepEqual(p.push("event: delta\n\n"), []);
  assert.deepEqual(p.push('event: delta\ndata: {"text":"x"}\n'), []);
  assert.deepEqual(p.push("\n"), [{ event: "delta", data: '{"text":"x"}' }]);
});

test("an event with no name is a message", () => {
  assert.deepEqual(new SSEParser().push("data: 1\n\n"), [{ event: "message", data: "1" }]);
});

test("a runaway event is refused", () => {
  assert.throws(() => new SSEParser().push("data: " + "x".repeat(1_000_001)), StreamUnavailable);
});

test("readEvents decodes UTF-8 split across chunks", async () => {
  const bytes = new TextEncoder().encode(frame("delta", { text: "≥ é" }));
  const events = [];
  await readEvents(body([bytes.slice(0, 22), bytes.slice(22, 23), bytes.slice(23)]), (e) => events.push(e));
  assert.deepEqual(JSON.parse(events[0].data), { text: "≥ é" });
});

// ---- the request ----

test("deltas arrive in order and the final answer is returned", async () => {
  const seen = [];
  const res = await streamAnswer(
    fetchOf(response([frame("delta", { text: "Differentiate " }), ": ping\n\n", frame("delta", { text: "first" }), frame("final", FINAL)])),
    "/ask/stream",
    { course_id: "c", question: "q" },
    { onDelta: (t) => seen.push(t) }
  );
  assert.deepEqual(seen, ["Differentiate ", "first"]);
  assert.deepEqual(res, FINAL);
});

test("the request is a POST of the question as JSON", async () => {
  let sent;
  await streamAnswer(
    async (url, init) => ((sent = { url, ...init }), response([frame("final", FINAL)])),
    "http://b/ask/stream",
    { course_id: "c", question: "q" }
  );
  assert.equal(sent.url, "http://b/ask/stream");
  assert.equal(sent.method, "POST");
  assert.deepEqual(JSON.parse(sent.body), { course_id: "c", question: "q" });
});

test("an error event is shown with its fixed message", async () => {
  const run = streamAnswer(fetchOf(response([frame("delta", { text: "x" }), frame("error", { status: 502, detail: "answer service unavailable" })])), "/s", {});
  await assert.rejects(run, (e) => e instanceof BackendError && e.message === "answer service unavailable");
});

test("the backend's own HTTP errors are shown, not retried", async () => {
  for (const [status, detail] of [[404, "course not synced"], [503, "answer service not configured"], [503, "search service unavailable"]]) {
    await assert.rejects(
      streamAnswer(fetchOf(response(null, { status, json: { detail } })), "/s", {}),
      (e) => e instanceof BackendError && e.message === detail
    );
  }
});

test("a missing stream falls back to /ask", async () => {
  const cases = [
    response(null, { status: 404, json: { detail: "Not Found" } }),
    response(null, { status: 405, json: { detail: "Method Not Allowed" } }),
    response(null, { status: 502 }),
    response([JSON.stringify(FINAL)], { type: "application/json" }),
    response([]), // closed before anything arrived
    () => {
      throw new TypeError("Failed to fetch");
    },
  ];
  for (const c of cases) await assert.rejects(streamAnswer(fetchOf(c), "/s", {}), StreamUnavailable);
});

test("a connection lost after the answer started is an error, not a second model call", async () => {
  let reads = 0;
  const cut = new ReadableStream({
    pull(c) {
      if (reads++ === 0) c.enqueue(new TextEncoder().encode(frame("delta", { text: "half" })));
      else c.error(new TypeError("network error"));
    },
  });
  const dropped = { ok: true, status: 200, headers: { get: () => "text/event-stream" }, body: cut };
  for (const res of [response([frame("delta", { text: "half" })]), dropped]) {
    await assert.rejects(
      streamAnswer(fetchOf(res), "/s", {}),
      (e) => e instanceof BackendError && e.message.startsWith("The connection to the backend was lost")
    );
  }
});

test("Stop pressed while an error response is read reports Stopped, not a fallback", async () => {
  const ctrl = new AbortController();
  const res = response(null, { status: 404, json: { detail: "Not Found" } });
  res.json = async () => (ctrl.abort(), { detail: "Not Found" });
  await assert.rejects(streamAnswer(fetchOf(res), "/s", {}, { signal: ctrl.signal }), Stopped);
});

test("one chunk holding many events is fine; one endless line is refused", () => {
  const many = frame("delta", { text: "x".repeat(1000) }).repeat(2000);
  assert.equal(new SSEParser().push(many).length, 2000);
});

test("reading stops and the connection closes once the final event is in", async () => {
  let cancelled = false;
  const b = new ReadableStream({
    start(c) {
      c.enqueue(new TextEncoder().encode(frame("final", FINAL)));
    },
    cancel() {
      cancelled = true;
    },
  });
  const res = { ok: true, status: 200, headers: { get: () => "text/event-stream" }, body: b };
  assert.deepEqual(await streamAnswer(fetchOf(res), "/s", {}), FINAL);
  assert.ok(cancelled);
});

test("a final event this page cannot read is an error, not a blank answer", async () => {
  await assert.rejects(streamAnswer(fetchOf(response([frame("final", { answer: 1 })])), "/s", {}), BackendError);
  await assert.rejects(streamAnswer(fetchOf(response(["event: final\ndata: {nope\n\n"])), "/s", {}), BackendError);
});

test("Stop aborts the request and reports Stopped", async () => {
  const ctrl = new AbortController();
  const fetchFn = async (_url, init) => ({
      ok: true,
      status: 200,
      headers: { get: () => "text/event-stream" },
      body: new ReadableStream({
        start(c) {
          c.enqueue(new TextEncoder().encode(frame("delta", { text: "part" })));
          init.signal.addEventListener("abort", () => c.error(new DOMException("aborted", "AbortError")));
        },
      }),
    });
  const seen = [];
  const run = streamAnswer(fetchFn, "/s", {}, { signal: ctrl.signal, onDelta: (t) => (seen.push(t), ctrl.abort()) });
  await assert.rejects(run, Stopped);
  assert.deepEqual(seen, ["part"]);
});

test("Stop before the response arrives reports Stopped", async () => {
  const ctrl = new AbortController();
  ctrl.abort();
  const fetchFn = async () => {
    throw new DOMException("aborted", "AbortError");
  };
  await assert.rejects(streamAnswer(fetchFn, "/s", {}, { signal: ctrl.signal }), Stopped);
});

// ---- escape while streaming ----

test("streamed text is escaped plain text: no HTML, Markdown, math or citations", () => {
  const text = '<img src=x onerror=alert(1)> **bold** $x$ [Lecture 8, 0:04] [a](https://evil.example) "q" &';
  const out = streamingHtml(text);
  assert.equal(
    out,
    '<p class="streaming">&lt;img src=x onerror=alert(1)&gt; **bold** $x$ [Lecture 8, 0:04] [a](https://evil.example) &quot;q&quot; &amp;' +
      '<span class="cursor" aria-hidden="true"></span></p>'
  );
  assert.ok(!/<(img|strong|em|button|a)\b/.test(out));
  assert.ok(!out.includes('class="math"'));
});

test("the cursor can be left off, and the math marker character is dropped", () => {
  assert.equal(streamingHtml("a\u0000b", { cursor: false }), '<p class="streaming">ab</p>');
});

test("the final answer still renders with citations and math", () => {
  const out = answerHtml("Use $x$ here [Lecture 8, 0:04].", FINAL.sources);
  assert.match(out, /class="math"/);
  assert.match(out, /class="cite"/);
});
