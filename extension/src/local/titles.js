// Fields derived from a lecture title like "Lecture 17 - Topic - LE [002-000-LE]", ported from
// backend/app/titles.py.

const LECTURE_NO = /\bLecture\s+(\d+)/i;
const FIRST_BRACKET = /\[([^\]]*)\]/;
const LEADING_DIGITS = /^\s*(\d+)/;
const INT_MAX = 2 ** 31 - 1;

/** { lectureNo, section }; each is null when the title does not have it. */
export function parseTitle(title) {
  let lectureNo = null;
  const m = LECTURE_NO.exec(title);
  if (m && Number(m[1]) <= INT_MAX) lectureNo = Number(m[1]);
  let section = null;
  const b = FIRST_BRACKET.exec(title);
  if (b) {
    const d = LEADING_DIGITS.exec(b[1]);
    section = d ? d[1] : null;
  }
  return { lectureNo, section };
}
