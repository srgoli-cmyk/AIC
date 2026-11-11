// Study tools for one lecture: a short lesson, flashcards and practice test questions.
// Each is made on request from the lecture's captions (api.study) and kept for the visit, so
// switching tabs or lectures does not lose a deck or a half-finished test.
import * as api from "./api.js";
import { topic } from "./course.js";
import { $, el } from "./dom.js";
import { answerHtml, renderMath, safeUrl, sourceLabel } from "./render.js";

export const STUDY = {
  teach: {
    tab: "Teach me",
    title: (t) => `Learn ${t}`,
    blurb: "A short lesson made from this lecture, with links to the moments it comes from.",
    go: "Teach me this lecture",
    wait: "Reading the lecture and writing your lesson",
    pick: "teach it to you",
    again: "Write it again",
  },
  flashcards: {
    tab: "Flashcards",
    title: (t) => `Flashcards for ${t}`,
    blurb: "About 8 cards on the main ideas. Flip each one, then say if you knew it.",
    go: "Make flashcards",
    wait: "Making your flashcards",
    pick: "make flashcards from it",
    again: "Make new cards",
  },
  quiz: {
    tab: "Test me",
    title: (t) => `Test yourself on ${t}`,
    blurb: "5 multiple-choice questions. Each one tells you why, and where the lecture says it.",
    go: "Write my test",
    wait: "Writing your test questions",
    pick: "write test questions from it",
    again: "Write new questions",
  },
};

const sets = new Map(); // "<entry_id>:<kind>" -> { status, data, message, ...progress }
let view = null; // { kind, lecture, onMode } currently on screen

const key = (lecture, kind) => `${lecture.entry_id}:${kind}`;
const root = () => $("study");

/** Text from the model shown with math and bold: fully escaped by render.js. */
function rich(text, cls) {
  const node = el("div", { class: cls });
  node.innerHTML = answerHtml(text, []);
  renderMath(node);
  return node;
}

function sourceLink(source, label = "Play this part of the lecture ↗") {
  const url = source && safeUrl(source.url);
  if (!url) return null;
  return el("a", { class: "study-source", href: url, target: "_blank", rel: "noopener noreferrer" }, el("span", { class: "time" }, source.timestamp), label);
}

function draw(focus, ...nodes) {
  root().replaceChildren(...nodes.flat().filter(Boolean));
  if (focus) root().querySelector("[data-focus]")?.focus();
}

function intro(kind, lecture, set) {
  const copy = STUDY[kind];
  const busy = set?.status === "loading";
  return el(
    "div",
    { class: "study-card" },
    el("h3", {}, copy.title(topic(lecture))),
    el("p", {}, set?.status === "error" ? set.message : copy.blurb),
    busy
      ? el("div", { class: "study-wait", role: "status" }, el("div", { class: "skeleton" }, el("i"), el("i"), el("i")), el("p", {}, `${copy.wait}. This takes a few seconds.`))
      : el("button", { type: "button", class: "cta", "data-focus": "", on: { click: () => start(kind, lecture) } }, set?.status === "error" ? "Try again" : copy.go)
  );
}

async function start(kind, lecture) {
  const k = key(lecture, kind);
  sets.set(k, { status: "loading" });
  render(true);
  let set;
  try {
    const data = await api.study(lecture.entry_id, kind);
    if (data.empty) set = { status: "error", message: "This lecture is still being indexed. Give it a minute, then try again." };
    else if (kind === "flashcards") set = { status: "ready", data, queue: data.cards.map((_, i) => i), at: 0, flipped: false, missed: [], known: 0, total: data.cards.length };
    else if (kind === "quiz") set = { status: "ready", data, at: 0, picked: null, score: 0 };
    else set = { status: "ready", data };
  } catch (err) {
    set = { status: "error", message: err instanceof api.BackendError ? err.message : "Something went wrong. Try again." };
  }
  sets.set(k, set);
  if (view && key(view.lecture, view.kind) === k) render(true);
}

// ---------- Lesson ----------

function lesson(set, lecture) {
  const { answer, sources } = set.data;
  const body = el("div", { class: "answer-body" });
  body.innerHTML = answerHtml(answer, sources); // fully escaped in render.js
  renderMath(body);
  body.addEventListener("click", (e) => {
    const cite = e.target.closest(".cite");
    const li = cite && $(`lesson-source-${cite.dataset.source}`);
    if (!li) return;
    li.scrollIntoView({ behavior: "smooth", block: "nearest" });
    li.classList.remove("flash");
    void li.offsetWidth;
    li.classList.add("flash");
  });
  return [
    el("article", { class: "answer" }, el("div", { class: "eyebrow asked" }, `Lesson · ${topic(lecture)}`), body),
    sources.length
      ? el(
          "div",
          { class: "sources" },
          el("div", { class: "eyebrow" }, "Where the lecture says it"),
          el(
            "ol",
            {},
            sources.map((s, i) => {
              const url = safeUrl(s.url);
              return el(
                "li",
                { id: `lesson-source-${i}` },
                el(
                  url ? "a" : "div",
                  url ? { class: "source-head", href: url, target: "_blank", rel: "noopener noreferrer" } : { class: "source-head" },
                  el("span", { class: "time" }, s.timestamp),
                  el("span", { class: "source-title" }, sourceLabel(s).replace(/, [\d:]+$/, "")),
                  url ? el("span", { class: "open" }, "Play from here ↗") : null
                ),
                el("p", {}, `“${s.snippet}”`)
              );
            })
          )
        )
      : null,
    el(
      "div",
      { class: "study-next" },
      el("span", {}, "Ready to practice?"),
      el("button", { type: "button", class: "ghost", on: { click: () => view.onMode("flashcards") } }, "Make flashcards"),
      el("button", { type: "button", class: "ghost", on: { click: () => view.onMode("quiz") } }, "Test me on this"),
      el("button", { type: "button", class: "link", on: { click: () => start("teach", lecture) } }, STUDY.teach.again)
    ),
  ];
}

// ---------- Flashcards ----------

function grade(set, knew) {
  const card = set.queue[set.at];
  if (knew) set.known++;
  else set.missed.push(card);
  set.at++;
  set.flipped = false;
  render(true);
}

function flashcards(set, lecture) {
  const cards = set.data.cards;
  if (set.at >= set.queue.length) {
    const missed = set.missed.length;
    return el(
      "div",
      { class: "study-card done" },
      el("h3", {}, missed ? `You knew ${set.known} of ${set.total}.` : `All ${set.total} cards down. Nice work.`),
      el("p", {}, missed ? `${missed} to go. One more pass usually does it.` : "Come back tomorrow and see how many stick."),
      el(
        "div",
        { class: "study-actions" },
        missed
          ? el(
              "button",
              {
                type: "button",
                class: "cta",
                "data-focus": "",
                on: {
                  click: () => {
                    Object.assign(set, { queue: set.missed, missed: [], at: 0, flipped: false });
                    render(true);
                  },
                },
              },
              `Go again with the ${missed} you missed`
            )
          : null,
        el(
          "button",
          {
            type: "button",
            class: missed ? "ghost" : "cta",
            "data-focus": missed ? null : "",
            on: {
              click: () => {
                Object.assign(set, { queue: cards.map((_, i) => i), missed: [], at: 0, flipped: false, known: 0 });
                render(true);
              },
            },
          },
          "Start over"
        ),
        el("button", { type: "button", class: "link", on: { click: () => start("flashcards", lecture) } }, STUDY.flashcards.again)
      )
    );
  }
  const card = cards[set.queue[set.at]];
  const flip = () => {
    set.flipped = !set.flipped;
    render(true);
  };
  return [
    el(
      "div",
      { class: "study-top" },
      el("span", { class: "eyebrow" }, `Card ${set.at + 1} of ${set.queue.length}`),
      el("div", { class: "pips", "aria-hidden": "true" }, set.queue.map((_, i) => el("i", { class: i < set.at ? "past" : i === set.at ? "now" : "" })))
    ),
    el(
      "button",
      { type: "button", class: `flash-card${set.flipped ? " flipped" : ""}`, "aria-label": set.flipped ? "Answer. Press to see the question again." : "Question. Press to see the answer.", "data-focus": set.flipped ? null : "", on: { click: flip } },
      el("span", { class: "flash-inner" }, el("span", { class: "face front", "aria-hidden": String(set.flipped) }, rich(card.front, "face-text")), el("span", { class: "face back", "aria-hidden": String(!set.flipped) }, rich(card.back, "face-text")))
    ),
    el("p", { class: "sr", role: "status" }, set.flipped ? `Answer: ${card.back}` : `Question: ${card.front}`),
    set.flipped
      ? el(
          "div",
          { class: "study-actions" },
          el("button", { type: "button", class: "ghost", on: { click: () => grade(set, false) } }, el("kbd", {}, "1"), "Missed it"),
          el("button", { type: "button", class: "cta", "data-focus": "", on: { click: () => grade(set, true) } }, el("kbd", {}, "2"), "Got it"),
          sourceLink(card.source)
        )
      : el("p", { class: "keys" }, el("span", {}, el("kbd", {}, "Space"), " flips the card")),
  ];
}

// ---------- Quiz ----------

function quiz(set, lecture) {
  const questions = set.data.questions;
  if (set.at >= questions.length) {
    const all = set.score === questions.length;
    return el(
      "div",
      { class: "study-card done" },
      el("h3", {}, `${set.score} of ${questions.length} correct.`),
      el("p", {}, all ? "Clean sweep. You know this lecture." : set.score >= questions.length / 2 ? "Solid. The ones you missed are worth a second look." : "That's what practice is for. The lesson tab walks through it again."),
      el(
        "div",
        { class: "study-actions" },
        el(
          "button",
          {
            type: "button",
            class: "cta",
            "data-focus": "",
            on: {
              click: () => {
                Object.assign(set, { at: 0, picked: null, score: 0 });
                render(true);
              },
            },
          },
          "Try these again"
        ),
        all ? null : el("button", { type: "button", class: "ghost", on: { click: () => view.onMode("teach") } }, "Teach me this lecture"),
        el("button", { type: "button", class: "link", on: { click: () => start("quiz", lecture) } }, STUDY.quiz.again)
      )
    );
  }
  const q = questions[set.at];
  const done = set.picked !== null;
  const pick = (i) => {
    if (set.picked !== null) return;
    set.picked = i;
    if (i === q.answer) set.score++;
    render(true);
  };
  const next = () => {
    set.at++;
    set.picked = null;
    render(true);
  };
  const last = set.at === questions.length - 1;
  return [
    el(
      "div",
      { class: "study-top" },
      el("span", { class: "eyebrow" }, `Question ${set.at + 1} of ${questions.length}`),
      el("div", { class: "pips", "aria-hidden": "true" }, questions.map((_, i) => el("i", { class: i < set.at ? "past" : i === set.at ? "now" : "" })))
    ),
    rich(q.question, "quiz-q"),
    el(
      "div",
      { class: "choices", role: "group", "aria-label": "Choices" },
      q.choices.map((c, i) =>
        el(
          "button",
          {
            type: "button",
            class: `choice${done && i === q.answer ? " right" : ""}${done && i === set.picked && i !== q.answer ? " wrong" : ""}`,
            disabled: done,
            "data-focus": !done && i === 0 ? "" : null,
            on: { click: () => pick(i) },
          },
          el("kbd", {}, String(i + 1)),
          rich(c, "choice-text"),
          done && i === q.answer ? el("span", { class: "mark" }, "Correct") : done && i === set.picked ? el("span", { class: "mark" }, "Your pick") : null
        )
      )
    ),
    done
      ? el(
          "div",
          { class: `why ${set.picked === q.answer ? "right" : "wrong"}`, role: "status" },
          el("strong", {}, set.picked === q.answer ? "That's right." : `Not quite. It's choice ${q.answer + 1}.`),
          q.why ? rich(q.why, "why-text") : null,
          el("div", { class: "study-actions" }, el("button", { type: "button", class: "cta", "data-focus": "", on: { click: next } }, last ? "See my score" : "Next question"), sourceLink(q.source))
        )
      : el("p", { class: "keys" }, el("span", {}, el("kbd", {}, "1"), " ", el("kbd", {}, "4"), " pick a choice")),
  ];
}

// ---------- Entry points ----------

function render(focus = false) {
  if (!view) return;
  const { kind, lecture } = view;
  const copy = STUDY[kind];
  if (!lecture) {
    return draw(focus, el("div", { class: "study-card" }, el("h3", {}, "Pick a lecture first"), el("p", {}, `Choose one from the list on the left, or click a bar up top, and I'll ${copy.pick}.`)));
  }
  if (!lecture.indexed) {
    return draw(focus, el("div", { class: "study-card" }, el("h3", {}, copy.title(topic(lecture))), el("p", {}, "This lecture is still being indexed. Give it a minute, then come back.")));
  }
  const set = sets.get(key(lecture, kind));
  if (set?.status !== "ready") return draw(focus, intro(kind, lecture, set));
  if (kind === "teach") return draw(focus, lesson(set, lecture));
  if (kind === "flashcards") return draw(focus, flashcards(set, lecture));
  return draw(focus, quiz(set, lecture));
}

/** Shows one study tool for the lecture (or a prompt to pick one). `onMode(kind)` switches tabs. */
export function showStudy(kind, lecture, onMode) {
  view = { kind, lecture, onMode };
  render();
}

export function hideStudy() {
  view = null;
}

// Number keys answer and grade; Space or Enter on the card flips it (it is a button).
document.addEventListener("keydown", (e) => {
  if (!view?.lecture || root().hidden || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.closest?.("textarea, input, [contenteditable]")) return;
  const set = sets.get(key(view.lecture, view.kind));
  if (set?.status !== "ready") return;
  const n = Number(e.key);
  if (view.kind === "quiz" && set.at < set.data.questions.length && set.picked === null && n >= 1 && n <= 4) {
    e.preventDefault();
    root().querySelectorAll(".choice")[n - 1]?.click();
  } else if (view.kind === "flashcards" && set.at < set.queue.length && set.flipped && (n === 1 || n === 2)) {
    e.preventDefault();
    grade(set, n === 2);
  }
});
