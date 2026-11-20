import { test } from "node:test";
import assert from "node:assert/strict";
import { BackendError, onSignedOut, signedOut } from "../src/errors.js";
import { streamAnswer } from "../src/stream.js";

test("every listener hears that the session ended, even if one throws", () => {
  const heard = [];
  const off1 = onSignedOut(() => {
    throw new Error("broken");
  });
  const off2 = onSignedOut(() => heard.push("page"));
  signedOut();
  off1();
  off2();
  signedOut(); // removed listeners hear nothing more
  assert.deepEqual(heard, ["page"]);
});

test("a 401 from the answer stream ends the session and is not retried through /ask", async () => {
  let ended = 0;
  const off = onSignedOut(() => ended++);
  const res = {
    ok: false,
    status: 401,
    headers: { get: () => "application/json" },
    body: null,
    json: async () => ({ detail: "not authenticated" }),
  };
  try {
    await assert.rejects(
      streamAnswer(async () => res, "/ask/stream", {}),
      (e) => e instanceof BackendError && e.status === 401 && e.message === "not authenticated"
    );
  } finally {
    off();
  }
  assert.equal(ended, 1);
});

test("other errors from the stream do not end the session", async () => {
  let ended = 0;
  const off = onSignedOut(() => ended++);
  const res = { ok: false, status: 404, headers: { get: () => "application/json" }, body: null, json: async () => ({ detail: "course not synced" }) };
  try {
    await assert.rejects(streamAnswer(async () => res, "/ask/stream", {}), BackendError);
  } finally {
    off();
  }
  assert.equal(ended, 0);
});
