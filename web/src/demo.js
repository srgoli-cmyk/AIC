// Synthetic data for previewing the page without a backend (?demo, or the button shown when the
// backend is down). Same shapes as contracts/api.md. Not real course content.

const TOPICS = [
  "Limits and Continuity",
  "The Derivative",
  "Derivative Rules",
  "Trig Derivatives",
  "Product and Quotient Rules",
  "Chain Rule",
  "Implicit Differentiation",
  "Related Rates",
  "Linear Approximation",
  "Extreme Values",
  "Mean Value Theorem",
  "Curve Sketching",
];

// Mon/Wed/Fri at 10:00 Pacific (17:00 UTC) for section A00, Tue/Thu 14:00 (21:00 UTC) for B00.
function lectures(prefix, section, firstDay, days, utcHour) {
  const out = [];
  let d = new Date(Date.UTC(2026, 8, firstDay, utcHour, 0, 12));
  let n = 1;
  while (n <= TOPICS.length) {
    if (days.includes(d.getUTCDay())) {
      out.push({
        entry_id: `1_${prefix}${String(n).padStart(4, "0")}`,
        title: `Lecture ${n} - ${TOPICS[n - 1]} [${section}-000-LE]`,
        lecture_no: n,
        section,
        recorded_at: d.toISOString().slice(0, 10),
        recorded_start: d.toISOString(),
        duration_sec: 2900 + ((n * 137) % 300),
        media_url: `https://canvaskaf.ucsd.edu/media/t/1_${prefix}${String(n).padStart(4, "0")}/111111`,
        indexed: true,
      });
      n++;
    }
    d = new Date(d.getTime() + 86400000);
  }
  return out;
}

const MATH = {
  course_id: "canvas-999999",
  canvas_course_id: 999999,
  code: "MATH 20A",
  name: "Calculus for Science and Engineering",
  term: "FA26",
  term_start: "2026-09-24",
  term_end: "2026-12-12",
  instructors: ["Synthetic Instructor"],
  sections: ["MATH-020A - 001-000-LE", "MATH-020A - 001-001-DI", "MATH-020A - 002-000-LE", "MATH-020A - 003-000-LE"],
  synced_at: "2026-11-03T12:00:00Z",
  indexed: true,
  lectures: [...lectures("dema", "001", 25, [1, 3, 5], 17), ...lectures("demb", "002", 24, [2, 4], 21)].sort(
    (a, b) => a.lecture_no - b.lecture_no || a.recorded_start.localeCompare(b.recorded_start)
  ),
};

const DSC = {
  course_id: "canvas-888888",
  canvas_course_id: 888888,
  code: "DSC 10",
  name: "Principles of Data Science",
  term: "FA26",
  term_start: "2026-09-24",
  term_end: null,
  instructors: ["Another Instructor", "Second Instructor"],
  sections: [],
  synced_at: "2026-11-03T12:00:00Z",
  indexed: false,
  lectures: lectures("demc", "001", 24, [2, 4], 18).slice(0, 8).map((l, i) => ({ ...l, title: l.title.replace(/ - .*/, " - Data Science Topic"), indexed: i < 5 })),
};

export const DEMO_COURSES = [MATH, DSC].map((c) => ({
  course_id: c.course_id,
  code: c.code,
  name: c.name,
  lectures: c.lectures.length,
  indexed: c.indexed,
}));

export function demoCourse(id) {
  return [MATH, DSC].find((c) => c.course_id === id) || null;
}

const SOURCES = [
  {
    lecture_title: "Lecture 5 - Product and Quotient Rules [001-000-LE]",
    entry_id: "1_dema0005",
    start_sec: 2179,
    end_sec: 2231,
    timestamp: "36:19",
    url: "https://canvaskaf.ucsd.edu/media/t/1_dema0005/111111?st=2179",
    snippet: "So the derivative of f times g is not f prime times g prime. You keep one, differentiate the other, then swap.",
  },
  {
    lecture_title: "Lecture 5 - Product and Quotient Rules [001-000-LE]",
    entry_id: "1_dema0005",
    start_sec: 2412,
    end_sec: 2466,
    timestamp: "40:12",
    url: "https://canvaskaf.ucsd.edu/media/t/1_dema0005/111111?st=2412",
    snippet: "Let's do x squared sine x. First times derivative of the second, plus second times derivative of the first.",
  },
  {
    lecture_title: "Lecture 6 - Chain Rule [001-000-LE]",
    entry_id: "1_dema0006",
    start_sec: 95,
    end_sec: 148,
    timestamp: "1:35",
    url: "https://canvaskaf.ucsd.edu/media/t/1_dema0006/111111?st=95",
    snippet: "Last time we did products. Today, compositions, which is a different rule, don't mix them up.",
  },
];

export function demoAnswer(question, entryId) {
  const sources = entryId ? SOURCES.filter((s) => s.entry_id === entryId) : SOURCES;
  if (/product|f\s*g|times/i.test(question) && sources.length) {
    const cites = sources.map((s) => `[Lecture ${s.lecture_title.match(/\d+/)[0]}, ${s.timestamp}]`);
    return {
      answer:
        `Use the **product rule**: $(fg)' = f'g + fg'$. Differentiate one factor at a time and keep the other as is ${cites[0]}.\n\n` +
        "A common mistake is writing $f'g'$, which is wrong.\n\n" +
        `Worked example from class ${cites[1] || cites[0]}:\n\n` +
        "$$\\frac{d}{dx}\\left(x^2 \\sin x\\right) = 2x\\sin x + x^2\\cos x$$\n\n" +
        "- First factor's derivative times the second\n" +
        "- Plus the first factor times the second's derivative" +
        (cites[2] ? `\n\nDon't confuse this with the chain rule, which is for compositions like $\\sin(x^2)$ ${cites[2]}.` : ""),
      sources,
    };
  }
  return {
    answer: entryId
      ? "This lecture doesn't cover that. Try asking across all lectures."
      : "The synced lectures don't cover this. Try asking about something from class, like the product rule.",
    sources: [],
  };
}
