// The JavaScript ports give exactly what the backend's Python code gives, on the generated cases in
// fixtures/parity.json (scripts/parity_fixtures.py; no real captions).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { citationLabel, citedHits, dedupe, sourceUrl } from "../src/local/answer.js";
import { chunkCues } from "../src/local/chunking.js";
import { parseSrtWithStats } from "../src/local/srt.js";
import { parseTitle } from "../src/local/titles.js";
import { SYSTEM_PROMPT } from "../../proxy/src/worker.js";

const fixture = JSON.parse(readFileSync(new URL("fixtures/parity.json", import.meta.url), "utf8"));

const near = (a, b) => Math.abs(a - b) < 1e-9;

test("SRT parsing matches the backend", () => {
  assert.ok(fixture.srt.length >= 30);
  for (const [i, c] of fixture.srt.entries()) {
    const { cues, dropped } = parseSrtWithStats(c.srt);
    assert.equal(dropped, c.dropped, `case ${i} dropped`);
    assert.equal(cues.length, c.cues.length, `case ${i} cue count`);
    cues.forEach((cue, k) => {
      const [s, e, t] = c.cues[k];
      assert.ok(near(cue.start, s) && near(cue.end, e), `case ${i} cue ${k} times`);
      assert.equal(cue.text, t, `case ${i} cue ${k} text`);
    });
  }
});

test("chunking matches the backend", () => {
  for (const [i, c] of fixture.srt.entries()) {
    const cues = c.cues.map(([start, end, text]) => ({ start, end, text }));
    const chunks = chunkCues(cues);
    assert.equal(chunks.length, c.chunks.length, `case ${i} chunk count`);
    chunks.forEach((ch, k) => {
      const [s, e, t] = c.chunks[k];
      assert.ok(near(ch.start, s) && near(ch.end, e), `case ${i} chunk ${k} times`);
      assert.equal(ch.text, t, `case ${i} chunk ${k} text`);
    });
  }
});

test("title parsing matches the backend", () => {
  for (const c of fixture.titles) {
    const { lectureNo, section } = parseTitle(c.title);
    assert.deepEqual([lectureNo, section], c.parsed, c.title);
  }
});

test("citations, de-duplication and source links match the backend", () => {
  for (const c of fixture.answer) {
    const hits = c.hits.map(([entry_id, lecture_no, lecture_title, start_sec, end_sec, text, media_url]) => ({
      entry_id,
      lecture_no,
      lecture_title,
      start_sec,
      end_sec,
      text,
      media_url,
    }));
    assert.deepEqual(hits.map(citationLabel), c.labels);
    assert.deepEqual(dedupe(hits, 3).map((h) => hits.indexOf(h)), c.dedupe);
    assert.deepEqual(hits.map((h) => sourceUrl(h.media_url, h.start_sec)), c.urls);
    for (const r of c.replies) {
      const [cited, cleaned] = citedHits(r.reply, hits);
      assert.deepEqual(cited.map((h) => hits.indexOf(h)), r.cited, r.reply);
      assert.equal(cleaned, r.cleaned, r.reply);
    }
  }
});

test("the answer service uses the backend's exact instructions", () => {
  assert.equal(SYSTEM_PROMPT, fixture.system_prompt);
});
