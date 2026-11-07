// Answers a question from a course's chunks: retrieve here, ask the answer service (proxy/) to
// write the reply, then turn the citations in it into sources. Ported from backend/app/answer.py;
// the prompt itself is built by the proxy, so the model always gets the same fixed instructions.
//
// Nothing here logs the question, the passages or the reply.

export const SNIPPET_CHARS = 300;
export const NOTHING_INDEXED = "Nothing from this course has been indexed yet. Please try again in a minute.";
export const NOT_COVERED = "The lectures don't cover this.";

export class AnswerUnavailable extends Error {
  constructor(message = "The answer service is unavailable. Try again in a minute.") {
    super(message);
    this.name = "AnswerUnavailable";
  }
}

// --- retrieval helpers (pure) ---

/** m:ss, or h:mm:ss from one hour; the start of the chunk, rounded down. */
export function formatTimestamp(seconds) {
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** `Lecture N`, or the title (brackets removed, whitespace collapsed) when it has no number. */
export function citationName(hit) {
  if (hit.lecture_no !== null && hit.lecture_no !== undefined) return `Lecture ${hit.lecture_no}`;
  return hit.lecture_title.replace(/[[\]]/g, " ").split(/\s+/).filter(Boolean).join(" ");
}

export function citationLabel(hit) {
  return `${citationName(hit)}, ${formatTimestamp(hit.start_sec)}`;
}

function overlap(a, b) {
  if (a.start_sec === a.end_sec || b.start_sec === b.end_sec) return a.start_sec <= b.end_sec && b.start_sec <= a.end_sec;
  return a.start_sec < b.end_sec && b.start_sec < a.end_sec; // touching is not overlapping
}

const lectureKey = (hit) => (hit.lecture_no !== null && hit.lecture_no !== undefined ? `no:${hit.lecture_no}` : `id:${hit.entry_id}`);

/** At most topK chunks, closest first, minus repeats of the same lecture over the same time. */
export function dedupe(hits, topK) {
  const kept = [];
  for (const hit of hits) {
    if (!kept.some((k) => lectureKey(k) === lectureKey(hit) && overlap(hit, k))) {
      kept.push(hit);
      if (kept.length === topK) break;
    }
  }
  return kept;
}

// --- citations ---

const BRACKETS = /\[([^[\]]*)\]|\(([^()]*)\)/g;
const TIME = /(?<![\d:])(\d+):(\d{2})(?::(\d{2}))?(?![\d:])/g;
const RANGE_DASHES = ["-", "–", "—"];
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function toSeconds(first, second, third) {
  if (third === undefined) return Number(first) * 60 + Number(second);
  return Number(first) * 3600 + Number(second) * 60 + Number(third);
}

/** The (label, seconds) citations inside one pair of brackets, and how many times had no label. */
function scan(inner, hits) {
  const events = []; // [start, end, label | null, seconds]
  const taken = [];
  const clash = (s, e) => taken.some(([ts, te]) => s < te && ts < e);
  for (const hit of [...hits].sort((a, b) => citationLabel(b).length - citationLabel(a).length)) {
    const re = new RegExp(`(?<!\\w)${escapeRe(citationLabel(hit))}(?![\\d:])`, "gi");
    for (const m of inner.matchAll(re)) {
      const s = m.index;
      const e = s + m[0].length;
      if (!clash(s, e)) {
        taken.push([s, e]);
        events.push([s, e, citationName(hit), Math.floor(hit.start_sec)]);
      }
    }
  }
  for (const m of inner.matchAll(TIME)) {
    const s = m.index;
    const e = s + m[0].length;
    if (!clash(s, e)) events.push([s, e, null, toSeconds(m[1], m[2], m[3])]);
  }
  events.sort((a, b) => a[0] - b[0] || a[1] - b[1]);

  const found = [];
  let unlabelled = 0;
  let label = null;
  let previousEnd = 0;
  for (const [start, end, knownLabel, seconds] of events) {
    const gap = inner.slice(previousEnd, start);
    previousEnd = end;
    if (knownLabel !== null) {
      label = knownLabel;
      found.push([label, seconds]);
      continue;
    }
    const core = gap.replace(/[\s,;&]/g, "");
    if (RANGE_DASHES.includes(core)) continue; // the end of a range such as 0:04-0:10
    const text = gap.replace(/^[\s,;&]*(?:and\s+)?/i, "").replace(/[ ,;:]+$/, "");
    if (text) label = text.split(/\s+/).filter(Boolean).join(" ");
    if (label) found.push([label, seconds]);
    else unlabelled++;
  }
  return [found, unlabelled];
}

function matching(label, seconds, hits) {
  const same = hits.map((h, i) => [h, i]).filter(([h]) => citationName(h).toLowerCase() === label.toLowerCase());
  const exact = same.filter(([h]) => Math.floor(h.start_sec) === seconds).map(([, i]) => i);
  if (exact.length) return exact;
  return same.filter(([h]) => Math.floor(h.start_sec) <= seconds && seconds <= h.end_sec).map(([, i]) => i);
}

/** [cited hits in `hits` order, answer with unfounded square-bracket citations removed]. */
export function citedHits(answer, hits) {
  const cited = new Set();
  const removals = [];
  for (const m of answer.matchAll(BRACKETS)) {
    const square = m[1] !== undefined;
    const [found, unlabelled] = scan(square ? m[1] : m[2], hits);
    const matched = found.flatMap(([label, sec]) => matching(label, sec, hits));
    matched.forEach((i) => cited.add(i));
    if (square && (found.length || unlabelled) && !matched.length) removals.push([m.index, m.index + m[0].length]);
  }
  let cleaned = answer;
  for (const [s, e] of removals.reverse()) {
    // Also drop the spaces before the group, so no gap is left in the sentence.
    const start = cleaned.slice(0, s).replace(/[ \t]+$/, "").length;
    cleaned = cleaned.slice(0, start) + cleaned.slice(e);
  }
  return [hits.filter((_, i) => cited.has(i)), cleaned];
}

export function snippet(text) {
  if (text.length <= SNIPPET_CHARS) return text;
  const cut = text.slice(0, SNIPPET_CHARS);
  const space = cut.lastIndexOf(" ");
  return (space > 0 ? cut.slice(0, space) : cut).trimEnd();
}

/** The lecture page opened 3 s before the passage: `?st=<s>` on canvaskaf, `#<s>` on podcast. */
export function sourceUrl(mediaUrl, startSec) {
  const u = new URL(mediaUrl);
  const start = Math.max(0, Math.floor(startSec) - 3);
  if (u.hostname === "podcast.ucsd.edu") {
    u.hash = String(start);
    return u.toString();
  }
  u.searchParams.delete("st");
  u.searchParams.append("st", String(start));
  return u.toString();
}

const toSource = (hit) => ({
  lecture_title: hit.lecture_title,
  entry_id: hit.entry_id,
  start_sec: hit.start_sec,
  end_sec: hit.end_sec,
  timestamp: formatTimestamp(hit.start_sec),
  url: sourceUrl(hit.media_url, hit.start_sec),
  snippet: snippet(hit.text),
});

/**
 * { answer, sources }. Deps: search(queryVector, limit) -> hits closest first,
 * embedQuery(question) -> vector, writeAnswer(question, passages) -> reply text.
 */
export async function answerQuestion(question, { topK = 12, search, embedQuery, writeAnswer }) {
  const vector = await embedQuery(question);
  // Over-fetch: de-duplication removes rows, and the closest rows are often neighbours.
  const hits = await search(vector, Math.max(topK * 4, 40));
  if (!hits.length) return { answer: NOTHING_INDEXED, sources: [] };
  const given = dedupe(hits, topK);
  const reply = await writeAnswer(
    question,
    given.map((h) => ({ citation: citationLabel(h), text: h.text })),
  );
  const [cited, cleaned] = citedHits(reply, given);
  // No valid citation: whatever the model wrote is not grounded in the passages.
  if (!cited.length) return { answer: NOT_COVERED, sources: [] };
  return { answer: cleaned.trim(), sources: cited.map(toSource) };
}
