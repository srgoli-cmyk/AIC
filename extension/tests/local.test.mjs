// The standalone extension's lecture store, indexing and answer flow, on an in-memory IndexedDB.
// Everything is synthetic.
import { test } from "node:test";
import assert from "node:assert/strict";
import { IDBFactory } from "fake-indexeddb";
import { answerQuestion, NOT_COVERED, NOTHING_INDEXED } from "../src/local/answer.js";
import { answerClient, studyClient } from "../src/local/client.js";
import {
  dueCourseIds,
  dueLectureIds,
  deleteCourse,
  getCourse,
  lectureHits,
  lectureStatuses,
  listCourses,
  openDb,
  replaceChunks,
  saveSync,
  searchChunks,
} from "../src/local/db.js";
import { ingestCourse, ModelLoadError } from "../src/local/ingest.js";
import { localStore } from "../src/local/store.js";
import { spread, studyLecture } from "../src/local/study.js";
import { validatePayload } from "../src/local/validate.js";

const A = "1_aaaaaaaa";
const B = "1_bbbbbbbb";
const srt = (words, n = 6) =>
  Array.from({ length: n }, (_, i) => `${i + 1}\n00:00:${String(i * 10).padStart(2, "0")},000 --> 00:00:${String(i * 10 + 9).padStart(2, "0")},000\n${words} ${i}.`).join("\n\n");

function payload({ course = 77, lectures = [], term, ...rest } = {}) {
  return {
    schema_version: "1",
    synced_at: "2026-11-07T10:00:00.000Z",
    course: { canvas_course_id: course, code: "MATH018_FA26_001", name: "Linear Algebra", ...(term ? { term } : {}) },
    lectures,
    files: [],
    ...rest,
  };
}

function lecture(entry_id, title = `Lecture 1 [004]`, captions = srt("eigen values")) {
  return {
    source: "kaltura",
    entry_id,
    title,
    duration_sec: 60,
    media_url: `https://podcast.ucsd.edu/watch/fa26/math018_004-000-le/1`,
    captions_srt: captions,
  };
}

const fresh = () => openDb(new IDBFactory());

// A fake embedder: a word-count vector over a tiny vocabulary, normalized. Deterministic.
const VOCAB = ["eigen", "values", "kernel", "image", "basis", "span", "represent"];
function fakeEmbed(texts) {
  return texts.map((t) => {
    const v = VOCAB.map((w) => (t.toLowerCase().match(new RegExp(w, "g")) || []).length + 0.01);
    const n = Math.hypot(...v);
    const out = new Float32Array(384); // the real model's size; the rest stays 0
    v.forEach((x, i) => (out[i] = x / n));
    return out;
  });
}

test("a sync is stored and listed; optional fields never erase stored values", async () => {
  const db = await fresh();
  const r = await saveSync(db, payload({ term: "FA26", lectures: [lecture(A), lecture(B, "Lecture 2 [004]")] }));
  assert.deepEqual(r, { courseId: "canvas-77", skipped: 0 });
  await saveSync(db, payload({ lectures: [] })); // no term this time
  const c = await getCourse(db, "canvas-77");
  assert.equal(c.term, "FA26");
  assert.equal(c.lectures.length, 2, "nothing is deleted by a sync without those lectures");
  assert.deepEqual(
    c.lectures.map((l) => [l.lecture_no, l.section, l.indexed]),
    [
      [1, "004", false],
      [2, "004", false],
    ],
  );
  assert.deepEqual(await listCourses(db), [{ course_id: "canvas-77", code: "MATH018_FA26_001", name: "Linear Algebra", lectures: 2, indexed: false }]);
  assert.deepEqual(await lectureStatuses(db, "canvas-77"), [
    { entry_id: A, has_captions: true },
    { entry_id: B, has_captions: true },
  ]);
});

test("a lecture stored under one course is never moved to another", async () => {
  const db = await fresh();
  await saveSync(db, payload({ course: 1, lectures: [lecture(A, "Lecture 1")] }));
  const r = await saveSync(db, payload({ course: 2, lectures: [lecture(A, "Changed")] }));
  assert.equal(r.skipped, 1);
  const c1 = await getCourse(db, "canvas-1");
  assert.equal(c1.lectures[0].title, "Lecture 1");
  assert.equal((await getCourse(db, "canvas-2")).lectures.length, 0);
});

test("ingestion indexes due lectures, then search finds the closest chunk", async () => {
  const db = await fresh();
  await saveSync(db, payload({ lectures: [lecture(A, "Lecture 1", srt("eigen values")), lecture(B, "Lecture 2", srt("kernel image"))] }));
  assert.deepEqual(await dueCourseIds(db), ["canvas-77"]);
  const progress = [];
  const out = await ingestCourse(db, "canvas-77", fakeEmbed, { onProgress: (p) => progress.push(p.done) });
  assert.deepEqual(out, { done: 2, failed: 0 });
  assert.deepEqual(progress, [0, 1, 2]);
  assert.deepEqual(await dueLectureIds(db, "canvas-77"), []);
  assert.equal((await listCourses(db))[0].indexed, true);
  const [q] = fakeEmbed(["kernel image"]);
  const hits = await searchChunks(db, "canvas-77", q, 3);
  assert.equal(hits[0].entry_id, B);
  assert.ok(hits[0].distance <= hits.at(-1).distance);
  const onlyA = await searchChunks(db, "canvas-77", q, 3, A);
  assert.ok(onlyA.every((h) => h.entry_id === A));
});

test("changed captions make a lecture due again; same captions do not", async () => {
  const db = await fresh();
  await saveSync(db, payload({ lectures: [lecture(A)] }));
  await ingestCourse(db, "canvas-77", fakeEmbed);
  await saveSync(db, payload({ lectures: [lecture(A)] }));
  assert.deepEqual(await dueLectureIds(db, "canvas-77"), []);
  assert.equal((await listCourses(db))[0].indexed, true);
  await saveSync(db, payload({ lectures: [lecture(A, "Lecture 1 [004]", srt("new words"))] }));
  assert.deepEqual(await dueLectureIds(db, "canvas-77"), [A]);
  assert.equal((await listCourses(db))[0].indexed, false);
});

test("chunks built from old captions are not written over new ones", async () => {
  const db = await fresh();
  await saveSync(db, payload({ lectures: [lecture(A)] }));
  const wrote = await replaceChunks(db, A, "not-the-current-hash", [{ start: 0, end: 1, text: "x" }], fakeEmbed(["x"]));
  assert.equal(wrote, false);
  assert.deepEqual(await dueLectureIds(db, "canvas-77"), [A]);
});

test("a model that cannot load stops indexing and leaves lectures due", async () => {
  const db = await fresh();
  await saveSync(db, payload({ lectures: [lecture(A), lecture(B)] }));
  const out = await ingestCourse(db, "canvas-77", async () => {
    throw new ModelLoadError("TypeError");
  });
  assert.deepEqual(out, { done: 0, failed: 2 });
  assert.deepEqual(await dueLectureIds(db, "canvas-77"), [A, B]);
});

test("removing a course removes its lectures and chunks", async () => {
  const db = await fresh();
  await saveSync(db, payload({ lectures: [lecture(A)] }));
  await ingestCourse(db, "canvas-77", fakeEmbed);
  await deleteCourse(db, "canvas-77");
  assert.deepEqual(await listCourses(db), []);
  assert.deepEqual(await searchChunks(db, "canvas-77", fakeEmbed(["eigen"])[0], 5), []);
});

test("the payload check refuses unknown fields and bad links, naming only the field", () => {
  assert.doesNotThrow(() => validatePayload(payload({ lectures: [lecture(A)] })));
  const bad = [
    payload({ lectures: [{ ...lecture(A), ks: "x" }] }),
    payload({ lectures: [{ ...lecture(A), media_url: "https://podcast.ucsd.edu/watch/fa26/math018_004-000-le/1?x=1" }] }),
    payload({ lectures: [{ ...lecture(A), media_url: "https://evil.example/watch/fa26/math018_004-000-le/1" }] }),
    payload({ lectures: [{ ...lecture(A), entry_id: "abc" }] }),
    payload({ lectures: [{ ...lecture(A), captions_srt: "" }] }),
    { ...payload(), extra: 1 },
    payload({ course: 0 }),
  ];
  for (const p of bad) assert.throws(() => validatePayload(p), (e) => e.name === "PayloadError" && !e.message.includes("evil"));
});

test("the local store refuses a session-shaped payload and reports skipped lectures", async () => {
  const saved = [];
  const store = localStore({ open: fresh, onSaved: (id) => saved.push(id) });
  await store.confirm();
  await assert.rejects(store.save(payload({ lectures: [lecture(A, "Lecture djJ8" + "k".repeat(60))] })), (e) => e.code === "KS_IN_PAYLOAD");
  await assert.rejects(store.save({ ...payload(), token: "x" }), (e) => e.code === "STORE_REJECTED");
  assert.deepEqual(await store.save(payload({ lectures: [lecture(A)] })), { skippedOtherCourse: 0 });
  assert.deepEqual(saved, ["canvas-77"]);
  assert.deepEqual(await store.lectures(77), [{ entry_id: A, has_captions: true }]);
  assert.deepEqual(await store.lectures(99), []);
});

test("a store that cannot open fails with a fixed message", async () => {
  const store = localStore({ open: async () => Promise.reject(new Error("quota")) });
  await assert.rejects(store.confirm(), (e) => e.code === "STORE_FAILED" && !e.message.includes("quota"));
});

test("answers keep only grounded citations and link to the lecture moment", async () => {
  const db = await fresh();
  await saveSync(db, payload({ lectures: [lecture(A, "Lecture 1", srt("eigen values")), lecture(B, "Lecture 2", srt("kernel image"))] }));
  await ingestCourse(db, "canvas-77", fakeEmbed);
  const deps = (reply) => ({
    embedQuery: async (q) => fakeEmbed([q])[0],
    search: (v, limit) => searchChunks(db, "canvas-77", v, limit),
    writeAnswer: async (question, passages) => {
      assert.ok(passages.length > 0 && passages.every((p) => typeof p.citation === "string" && typeof p.text === "string"));
      return reply(passages);
    },
  });
  const res = await answerQuestion("what is a kernel image", deps((p) => `A kernel is... [${p[0].citation}] and [Lecture 9, 0:00].`));
  assert.equal(res.sources.length, 1);
  assert.match(res.sources[0].url, /^https:\/\/podcast\.ucsd\.edu\/watch\/fa26\/math018_004-000-le\/1#\d+$/);
  assert.doesNotMatch(res.answer, /Lecture 9/);
  assert.deepEqual(await answerQuestion("q", deps(() => "No citations at all.")), { answer: NOT_COVERED, sources: [] });

  const empty = await fresh();
  const none = await answerQuestion("q", { embedQuery: async () => fakeEmbed(["q"])[0], search: (v, l) => searchChunks(empty, "canvas-77", v, l), writeAnswer: async () => "x" });
  assert.deepEqual(none, { answer: NOTHING_INDEXED, sources: [] });
});

test("the answer client sends only the question, the excerpts and an install id", async () => {
  const sent = [];
  const write = answerClient("http://localhost:8787/v1/answer", {
    getInstallId: async () => "00000000-0000-4000-8000-000000000000",
    fetchFn: async (url, init) => {
      sent.push({ url, init });
      return { ok: true, status: 200, json: async () => ({ answer: "ok [Lecture 1, 0:00]" }) };
    },
  });
  assert.equal(await write("q?", [{ citation: "Lecture 1, 0:00", text: "t" }]), "ok [Lecture 1, 0:00]");
  assert.deepEqual(JSON.parse(sent[0].init.body), { question: "q?", passages: [{ citation: "Lecture 1, 0:00", text: "t" }] });
  assert.equal(sent[0].init.credentials, "omit");
  assert.equal(sent[0].init.headers["X-Install-Id"], "00000000-0000-4000-8000-000000000000");

  const limited = answerClient("u", { getInstallId: async () => "x", fetchFn: async () => ({ ok: false, status: 429, json: async () => ({}) }) });
  await assert.rejects(limited("q", []), (e) => e.name === "AnswerUnavailable" && /Too many/.test(e.message));
  const down = answerClient("u", { getInstallId: async () => "x", fetchFn: async () => Promise.reject(new TypeError("fail")) });
  await assert.rejects(down("q", []), (e) => e.name === "AnswerUnavailable");
});

test("study sets come from one lecture's own chunks and link back to the moment", async () => {
  const db = await fresh();
  await saveSync(db, payload({ lectures: [lecture(A, "Lecture 1", srt("eigen values")), lecture(B, "Lecture 2", srt("kernel image"))] }));
  await ingestCourse(db, "canvas-77", fakeEmbed);
  const hits = () => lectureHits(db, B);
  assert.ok((await hits()).every((h) => h.entry_id === B));
  const sent = [];
  const reply = (make) => async (task, passages) => {
    sent.push({ task, passages });
    return make(passages);
  };

  const cards = await studyLecture("flashcards", { hits, writeStudy: reply((p) => ({ cards: [{ front: "F", back: "B", citation: p[0].citation }, { front: "G", back: "H", citation: "Lecture 9, 0:00" }, { front: "", back: "x" }] })) });
  assert.equal(cards.cards.length, 2);
  assert.match(cards.cards[0].source.url, /^https:\/\/podcast\.ucsd\.edu\/watch\/fa26\/math018_004-000-le\/1#\d+$/);
  assert.equal(cards.cards[1].source, null);
  assert.equal(sent[0].task, "flashcards");
  assert.ok(sent[0].passages.every((p) => /kernel image/.test(p.text)));

  const quiz = await studyLecture("quiz", {
    hits,
    random: () => 0,
    writeStudy: reply((p) => ({ questions: [{ question: "Q?", choices: ["right", "w1", "w2", "w3"], answer: 0, why: "y", citation: p[0].citation }] })),
  });
  const q = quiz.questions[0];
  assert.equal(q.choices[q.answer], "right", "the right choice is tracked through the shuffle");
  assert.notEqual(q.answer, 0);

  const lesson = await studyLecture("teach", { hits, writeStudy: reply((p) => ({ answer: `**Kernels**\nIt is... [${p[0].citation}] and [Lecture 9, 0:00].` })) });
  assert.equal(lesson.sources.length, 1);
  assert.doesNotMatch(lesson.answer, /Lecture 9/);

  assert.deepEqual(await studyLecture("teach", { hits: async () => [], writeStudy: async () => ({}) }), { empty: NOTHING_INDEXED });
  await assert.rejects(studyLecture("flashcards", { hits, writeStudy: async () => ({ cards: [] }) }), (e) => e.name === "AnswerUnavailable");
  await assert.rejects(studyLecture("essay", { hits, writeStudy: async () => ({}) }));
});

test("long lectures send an even sample, never more than the service accepts", () => {
  const items = Array.from({ length: 53 }, (_, i) => i);
  const picked = spread(items, 20);
  assert.equal(picked.length, 20);
  assert.equal(picked[0], 0);
  assert.equal(picked.at(-1), 52);
  assert.deepEqual(spread([1, 2, 3], 20), [1, 2, 3]);
});

test("the study client sends the task and the excerpts, nothing else", async () => {
  const sent = [];
  const write = studyClient("u", { getInstallId: async () => "x", fetchFn: async (url, init) => (sent.push(JSON.parse(init.body)), { ok: true, status: 200, json: async () => ({ cards: [] }) }) });
  assert.deepEqual(await write("flashcards", [{ citation: "Lecture 1, 0:00", text: "t" }]), { cards: [] });
  assert.deepEqual(sent[0], { task: "flashcards", passages: [{ citation: "Lecture 1, 0:00", text: "t" }] });
});
