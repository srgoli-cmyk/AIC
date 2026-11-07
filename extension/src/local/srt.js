// SRT parsing, ported from backend/app/srt.py. Pure functions, no I/O.
//
// Tolerant on purpose: captions come from a third party, and one bad cue must not lose a lecture.
// The file is split on timing lines ("... --> ..."), not on blank lines, so missing separators and
// odd whitespace do not merge or lose cues.

// 00:01:02,345 --> 00:01:05.678. Hours are limited to 5 digits; milliseconds use "," or ".".
const T = String.raw`(?<!\d)(\d{1,5}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})(?!\d)`;
const TIMING = new RegExp(String.raw`${T}\s*-->\s*${T}`);
const ARROW = "-->"; // any line with this is a timing line; if it is not valid, its cue is dropped
const CUE_NUMBER = /^\s*\d+\s*$/;
// Only known caption markup is stripped, so "if x <y and y> z" survives.
const TAG = /<\/?(?:[ibuc](?:\.[^>\s]*)?|font(?:\s[^>]*)?)>|<v(?:\.[^>\s]*)?\s[^>]*>|<\/v>|\{\\[^}]*\}/gi;
const SPACE = /\s+/g;
// Python's str.splitlines boundaries.
const LINE_BREAK = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;

const NAMED = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", ndash: "–", mdash: "—", hellip: "…", lsquo: "‘", rsquo: "’", ldquo: "“", rdquo: "”", eacute: "é", deg: "°", times: "×", divide: "÷", plusmn: "±" };

/** HTML entity decoding for caption text: named entities above and every numeric one. */
export function unescapeHtml(s) {
  return s.replace(/&(#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z]+);/g, (whole, g) => {
    if (g[0] === "#") {
      const code = g[1] === "x" || g[1] === "X" ? parseInt(g.slice(2), 16) : parseInt(g.slice(1), 10);
      if (!Number.isFinite(code) || code <= 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return "�";
      return String.fromCodePoint(code);
    }
    return Object.hasOwn(NAMED, g) ? NAMED[g] : whole;
  });
}

function seconds(h, m, s, ms) {
  // ".5" means 500 ms, not 5 ms, so pad on the right.
  return Number(h) * 3600 + Number(m) * 60 + Number(s) + Number(ms.padEnd(3, "0")) / 1000;
}

function clean(lines) {
  // Unescape after stripping tags, so an escaped "&lt;i&gt;" stays as visible text.
  return unescapeHtml(lines.join(" ").replace(TAG, "")).replace(SPACE, " ").trim();
}

/**
 * Returns { cues: [{ start, end, text }] sorted by start, dropped }. A cue is dropped when its
 * timing line is malformed, its end is before its start, or nothing is left of its text.
 * Never throws on malformed input. Cues with equal starts keep file order.
 */
export function parseSrtWithStats(raw) {
  let text = typeof raw === "string" ? raw : "";
  while (text.startsWith("﻿")) text = text.slice(1);
  const lines = text.split(LINE_BREAK);
  if (lines.length && lines.at(-1) === "") lines.pop(); // splitlines drops a final empty line
  const timingAt = [];
  lines.forEach((line, i) => {
    if (line.includes(ARROW)) timingAt.push(i);
  });
  const cues = [];
  let dropped = 0;
  timingAt.forEach((t, n) => {
    const stop = n + 1 < timingAt.length ? timingAt[n + 1] : lines.length;
    let body = lines.slice(t + 1, stop);
    // A number directly above the next timing line is that cue's number, not our text.
    if (n + 1 < timingAt.length && body.length && CUE_NUMBER.test(body.at(-1))) body = body.slice(0, -1);
    const m = TIMING.exec(lines[t]);
    if (!m) {
      dropped++;
      return;
    }
    const start = seconds(m[1], m[2], m[3], m[4]);
    const end = seconds(m[5], m[6], m[7], m[8]);
    const cueText = clean(body);
    if (end < start || !cueText) {
      dropped++;
      return;
    }
    cues.push({ start, end, text: cueText });
  });
  cues.sort((a, b) => a.start - b.start); // stable
  return { cues, dropped };
}

export function parseSrt(raw) {
  return parseSrtWithStats(raw).cues;
}
