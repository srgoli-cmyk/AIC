// Study tools for one lecture: a short lesson, flashcards and practice test questions. Like
// answers, only caption excerpts leave the browser: up to MAX_PASSAGES of the lecture's chunks,
// spread evenly from start to end, go to the answer service, which writes the study set.
import { AnswerUnavailable, citationLabel, citedHits, NOTHING_INDEXED, toSource } from "./answer.js";

export const STUDY_KINDS = ["teach", "flashcards", "quiz"];
export const MAX_PASSAGES = 20;
// The service refuses a passage over 2500 characters; chunks are far shorter, this is a guard.
const MAX_TEXT = 2400;

/** At most `max` items spread evenly across the list, always keeping the first and the last. */
export function spread(items, max = MAX_PASSAGES) {
  if (items.length <= max) return items;
  const out = [];
  for (let i = 0; i < max; i++) out.push(items[Math.round((i * (items.length - 1)) / (max - 1))]);
  return out;
}

/** Fisher-Yates with an injectable random, so tests can fix the order. */
function shuffled(list, random) {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/**
 * Deps: hits() -> the lecture's chunks in order, writeStudy(task, passages) -> the service reply.
 * Returns { answer, sources } for "teach", { cards: [{ front, back, source }] } for "flashcards",
 * { questions: [{ question, choices, answer, why, source }] } for "quiz". `source` is where the
 * lecture says it (same shape as an answer's source), or null.
 */
export async function studyLecture(kind, { hits, writeStudy, random = Math.random }) {
  if (!STUDY_KINDS.includes(kind)) throw new Error("unknown study kind");
  const all = await hits();
  if (!all.length) return { empty: NOTHING_INDEXED };
  const given = spread(all);
  const byLabel = new Map(given.map((h) => [citationLabel(h), h]));
  const reply = await writeStudy(
    kind,
    given.map((h) => ({ citation: citationLabel(h), text: h.text.slice(0, MAX_TEXT) })),
  );
  const source = (label) => (byLabel.has(label) ? toSource(byLabel.get(label)) : null);

  if (kind === "teach") {
    if (typeof reply.answer !== "string" || !reply.answer.trim()) throw new AnswerUnavailable();
    const [cited, cleaned] = citedHits(reply.answer, given);
    return { answer: cleaned.trim(), sources: cited.map(toSource) };
  }
  if (kind === "flashcards") {
    const cards = (Array.isArray(reply.cards) ? reply.cards : [])
      .filter((c) => typeof c?.front === "string" && typeof c?.back === "string" && c.front && c.back)
      .map((c) => ({ front: c.front, back: c.back, source: source(c.citation) }));
    if (!cards.length) throw new AnswerUnavailable();
    return { cards };
  }
  const questions = (Array.isArray(reply.questions) ? reply.questions : [])
    .filter((q) => typeof q?.question === "string" && Array.isArray(q.choices) && q.choices.length === 4 && q.choices.every((c) => typeof c === "string" && c) && [0, 1, 2, 3].includes(q.answer))
    .map((q) => {
      // Models tend to put the right choice first, so the order is mixed here.
      const order = shuffled([0, 1, 2, 3], random);
      return { question: q.question, choices: order.map((i) => q.choices[i]), answer: order.indexOf(q.answer), why: typeof q.why === "string" ? q.why : "", source: source(q.citation) };
    });
  if (!questions.length) throw new AnswerUnavailable();
  return { questions };
}
