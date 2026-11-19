// POST /ask/stream (contracts/api.md): Server-Sent Events with `delta`, then one `final` or `error`.
// No DOM and no globals, so it runs under node tests: fetch is passed in.

import { BackendError, Stopped, StreamUnavailable } from "./errors.js";

// An event bigger than this is not something the backend sends.
const MAX_BUFFER = 1_000_000;

/** Turns text chunks into whole events. Lines may arrive split across chunks, in any line ending. */
export class SSEParser {
  constructor() {
    this.buffer = "";
    this.event = "";
    this.data = [];
  }

  /** Adds a chunk of text and returns the events it completed, as `{ event, data }`. */
  push(chunk) {
    this.buffer += chunk;
    const events = [];
    for (;;) {
      const end = this.buffer.search(/\r\n|\r|\n/);
      if (end === -1) break;
      // A \r at the very end may be the first half of \r\n: wait for the next chunk.
      if (this.buffer[end] === "\r" && end === this.buffer.length - 1) break;
      const width = this.buffer.startsWith("\r\n", end) ? 2 : 1;
      const line = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + width);
      this.#line(line, events);
    }
    // Only what is left over after whole lines counts: one big chunk of many small events is fine.
    if (this.buffer.length > MAX_BUFFER) throw new StreamUnavailable("line too long");
    return events;
  }

  /** The stream ended: a held-back \r was a line end after all. Returns the last events. */
  finish() {
    return this.buffer.endsWith("\r") ? this.push("\n") : [];
  }

  #line(line, events) {
    if (line === "") {
      if (this.data.length) events.push({ event: this.event || "message", data: this.data.join("\n") });
      this.event = "";
      this.data = [];
      return;
    }
    if (line.startsWith(":")) return; // a comment: keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.event = value;
    else if (field === "data") this.data.push(value);
  }
}

/** Calls onEvent for every event in a fetch Response body. */
export async function readEvents(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = new SSEParser();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      const text = done ? decoder.decode() : decoder.decode(value, { stream: true });
      for (const e of [...parser.push(text), ...(done ? parser.finish() : [])]) {
        if (onEvent(e) === false) {
          await reader.cancel().catch(() => {}); // done reading: close the connection
          return;
        }
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock?.();
  }
}

function json(data) {
  try {
    return JSON.parse(data);
  } catch {
    throw new BackendError("The backend sent an answer this page cannot read.");
  }
}

function isAnswer(value) {
  return typeof value?.answer === "string" && Array.isArray(value.sources);
}

/**
 * Asks over the stream. Calls onDelta(text) for each piece and resolves with the final answer, in
 * the shape /ask returns. Throws BackendError with a message to show, StreamUnavailable when /ask
 * should be tried instead, or Stopped when `signal` aborted.
 */
export async function streamAnswer(fetchFn, url, body, { onDelta, signal } = {}) {
  let res;
  try {
    res = await fetchFn(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      credentials: "include",
      signal,
    });
  } catch (e) {
    if (signal?.aborted) throw new Stopped("stopped");
    throw new StreamUnavailable(e?.name || "network");
  }
  if (!res.ok) {
    let detail;
    try {
      detail = (await res.json())?.detail;
    } catch {
      detail = undefined;
    }
    // The backend's own errors are final: /ask would say the same. Anything else (no such route on an
    // older backend, a proxy page) means the stream itself is the problem.
    if (signal?.aborted) throw new Stopped("stopped");
    const known = typeof detail === "string" && detail !== "Not Found" && detail !== "Method Not Allowed";
    if (known) throw new BackendError(detail);
    throw new StreamUnavailable(`status ${res.status}`);
  }
  if (!/^text\/event-stream\b/i.test(res.headers.get("content-type") || "") || !res.body) {
    throw new StreamUnavailable("not a stream");
  }

  let final = null;
  let failure = null;
  // Once anything has arrived, the model has been called: asking /ask again would call it twice.
  let started = false;
  const lost = () => new BackendError("The connection to the backend was lost. Try again.");
  try {
    await readEvents(res.body, ({ event, data }) => {
      started = true;
      if (event === "delta") {
        const text = json(data)?.text;
        if (typeof text === "string" && text) onDelta?.(text);
        return true;
      }
      if (event === "final") {
        const value = json(data);
        if (!isAnswer(value)) throw new BackendError("The backend sent an answer this page cannot read.");
        final = value;
        return false;
      }
      if (event === "error") {
        const detail = json(data)?.detail;
        failure = new BackendError(typeof detail === "string" ? detail : "answer service unavailable");
        return false;
      }
      return true; // an event this page doesn't know: skip it
    });
  } catch (e) {
    if (signal?.aborted) throw new Stopped("stopped");
    if (e instanceof BackendError) throw e;
    if (started) throw lost();
    if (e instanceof StreamUnavailable) throw e;
    throw new StreamUnavailable(e?.name || "read");
  }
  if (failure) throw failure;
  if (signal?.aborted) throw new Stopped("stopped");
  // The connection closed before the answer was finished.
  if (!final) throw started ? lost() : new StreamUnavailable("ended early");
  return final;
}
