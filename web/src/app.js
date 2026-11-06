import * as api from "./api.js";
import { coverageNote, day, duration, lectureSections, prettyCode, prettyName, schedule, sectionsOf, termRange, topic } from "./course.js";
import { answerHtml, renderMath, safeUrl, sourceLabel } from "./render.js";

const $ = (id) => document.getElementById(id);

/** A DOM element with text children only: never parses HTML. */
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c);
  return node;
}

const state = { courses: [], course: null, section: "all", lecture: null, busy: false };

// Links keep ?demo and ?backend so moving around never drops them.
function href(params) {
  const keep = new URLSearchParams(location.search);
  const out = new URLSearchParams();
  for (const k of ["demo", "backend"]) if (keep.has(k)) out.set(k, keep.get(k));
  for (const [k, v] of Object.entries(params)) if (v) out.set(k, v);
  const q = out.toString().replace(/demo=(&|$)/, "demo$1");
  return `${location.pathname}${q ? `?${q}` : ""}`;
}

function go(params, replace = false) {
  history[replace ? "replaceState" : "pushState"]({}, "", href(params));
  route();
}

function setHealth(s, text) {
  $("health").dataset.state = s;
  $("health").querySelector("span").textContent = text;
}

function notice(title, lines, action) {
  const n = $("notice");
  n.hidden = false;
  n.replaceChildren(
    el("h2", {}, title),
    ...lines.map((l) => el("p", {}, l)),
    action ? el("button", { class: "link", on: { click: action.run } }, action.label) : null
  );
}

function show(view) {
  $("home").hidden = view !== "home";
  $("course").hidden = view !== "course";
}

// ---------- Home ----------

function renderHome() {
  document.title = "Lecture Search";
  if (!state.courses.length) {
    show(null);
    notice("No courses yet", [
      "Load the extension from the repo's extension/ folder, open a course on canvas.ucsd.edu and click Sync this course.",
      "It'll show up here once it's synced.",
    ]);
    return;
  }
  show("home");
  $("course-grid").replaceChildren(
    ...state.courses.map((c, i) =>
      el(
        "a",
        {
          class: "course-card",
          href: href({ course: c.course_id }),
          style: `animation-delay:${i * 60}ms`,
          on: { click: (e) => (e.preventDefault(), go({ course: c.course_id })) },
        },
        el("span", { class: "card-code" }, prettyCode(c.code)),
        el("span", { class: "card-name" }, prettyName(c.name, c.code)),
        el(
          "span",
          { class: "card-meta" },
          el("i", { class: c.indexed ? "ok" : "pending" }),
          `${c.lectures} lecture${c.lectures === 1 ? "" : "s"}${c.indexed ? "" : " · indexing"}`
        ),
        el("span", { class: "card-arrow", "aria-hidden": "true" }, "→")
      )
    )
  );
}

// ---------- Course ----------

function visibleLectures() {
  const all = state.course.lectures;
  return state.section === "all" ? all : all.filter((l) => l.section === state.section);
}

function renderCourseHead() {
  const c = state.course;
  document.title = `${prettyCode(c.code)} · Lecture Search`;
  $("c-term").textContent = [c.term, termRange(c)].filter(Boolean).join(" · ");
  $("c-code").textContent = prettyCode(c.code);
  $("c-name").textContent = prettyName(c.name, c.code);

  const facts = [];
  if (c.instructors.length) facts.push([c.instructors.length > 1 ? "Instructors" : "Instructor", c.instructors.join(", ")]);
  // Each lecture section meets on its own days, so mixing them would give a wrong schedule.
  const secs = sectionsOf(c.lectures);
  const lecSecs = lectureSections(c);
  // With one recorded section among several, its schedule is that section's, not the course's.
  const only = lecSecs.length > 1 && lecSecs.filter((s) => s.lectures).length === 1 ? lecSecs.find((s) => s.lectures).code : null;
  for (const sec of secs.length ? secs : [null]) {
    const sched = schedule(sec ? c.lectures.filter((l) => l.section === sec) : c.lectures);
    const label = sec || only;
    if (sched) facts.push([label ? `Section ${label} meets` : "Usually meets", `${sched.days.join(" · ")}, ${sched.time}`]);
  }
  facts.push(["Lectures", `${c.lectures.length}${c.indexed ? "" : " · indexing"}`]);
  if (lecSecs.length) {
    facts.push([
      lecSecs.length > 1 ? "Lecture sections" : "Lecture section",
      el(
        "span",
        { class: "sec-list" },
        lecSecs.map((s) =>
          el("span", { class: `sec${s.lectures ? " has" : ""}`, title: s.lectures ? `${s.lectures} recordings` : "No recordings on Canvas" }, el("i"), s.code)
        )
      ),
    ]);
  } else if (c.sections.length) {
    facts.push(["Sections", c.sections.map((s) => s.replace(`${c.code} - `, "")).join(", ")]);
  }
  $("c-facts").replaceChildren(...facts.map(([k, v]) => el("li", {}, el("span", { class: "fact-k" }, k), el("span", { class: "fact-v" }, v))));
  const note = coverageNote(lecSecs);
  $("c-note").hidden = !note;
  $("c-note").textContent = note;
}

function renderSections() {
  const secs = sectionsOf(state.course.lectures);
  if (state.section !== "all" && !secs.includes(state.section)) state.section = "all";
  const chip = (value, label) =>
    el(
      "button",
      {
        type: "button",
        class: "chip",
        role: "radio",
        "aria-checked": String(state.section === value),
        on: {
          click: () => {
            state.section = value;
            if (state.lecture && value !== "all" && state.lecture.section !== value) selectLecture(null);
            else {
              renderSections();
              renderLectures();
            }
          },
        },
      },
      label
    );
  $("sections").replaceChildren(...(secs.length ? [chip("all", "All"), ...secs.map((s) => chip(s, `Section ${s}`))] : []));
}

function renderLectures() {
  const list = visibleLectures();
  const multi = sectionsOf(state.course.lectures).length > 0;
  $("l-count").textContent = `${list.length} lecture${list.length === 1 ? "" : "s"}`;
  $("lecture-list").replaceChildren(
    ...list.map((l, i) => {
      const selected = state.lecture?.entry_id === l.entry_id;
      return el(
        "li",
        {},
        el(
          "button",
          {
            type: "button",
            class: `lecture${selected ? " selected" : ""}`,
            "aria-pressed": String(selected),
            style: `animation-delay:${Math.min(i, 12) * 30}ms`,
            on: { click: () => selectLecture(selected ? null : l) },
          },
          el("span", { class: "l-no" }, l.lecture_no != null ? String(l.lecture_no).padStart(2, "0") : "–"),
          el(
            "span",
            { class: "l-main" },
            el("span", { class: "l-title" }, topic(l)),
            el("span", { class: "l-meta" }, [day(l), duration(l.duration_sec), multi && l.section ? `Sec ${l.section}` : ""].filter(Boolean).join(" · "))
          ),
          l.indexed ? null : el("i", { class: "l-pending", title: "Still indexing" })
        )
      );
    })
  );
}

function renderScope() {
  const l = state.lecture;
  if (!l) {
    $("scope").replaceChildren(
      el("div", { class: "eyebrow" }, "Ask"),
      el("h2", { class: "scope-title" }, `Anything from all ${state.course.lectures.length} lectures.`),
      el("p", { class: "scope-sub" }, "Or pick a lecture to focus on just that one.")
    );
    $("q").placeholder = "Ask about anything from class";
    return;
  }
  const url = safeUrl(l.media_url);
  $("scope").replaceChildren(
    el(
      "div",
      { class: "scope-row" },
      el("div", { class: "eyebrow" }, l.lecture_no != null ? `Lecture ${l.lecture_no}` : "Lecture"),
      el("button", { type: "button", class: "clear", on: { click: () => selectLecture(null) } }, "All lectures ×")
    ),
    // A title that's just "Lecture N" repeats the eyebrow, so the date leads instead.
    el("h2", { class: "scope-title" }, topic(l) === `Lecture ${l.lecture_no}` && day(l) ? day(l) : topic(l)),
    el(
      "div",
      { class: "scope-meta" },
      el("span", {}, [day(l), duration(l.duration_sec)].filter(Boolean).join(" · ")),
      url ? el("a", { class: "watch", href: url, target: "_blank", rel: "noopener noreferrer" }, "Watch ↗") : null
    )
  );
  $("q").placeholder = `Ask about ${topic(l)}`;
}

function selectLecture(l) {
  $("result").hidden = true;
  go({ course: state.course.course_id, lecture: l?.entry_id }, true);
  if (l && matchMedia("(max-width: 820px)").matches) $("scope").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderCourse() {
  show("course");
  renderCourseHead();
  renderSections();
  renderLectures();
  renderScope();
}

// ---------- Ask ----------

function showSources(sources) {
  $("sources").replaceChildren(
    ...sources.map((s, i) => {
      const url = safeUrl(s.url);
      return el(
        "li",
        { id: `source-${i}` },
        el(
          url ? "a" : "div",
          url ? { class: "source-head", href: url, target: "_blank", rel: "noopener noreferrer" } : { class: "source-head" },
          el("span", { class: "time" }, s.timestamp),
          el("span", { class: "source-title" }, sourceLabel(s).replace(/, [\d:]+$/, "")),
          url ? el("span", { class: "open" }, "Open ↗") : null
        ),
        el("p", {}, `“${s.snippet}”`)
      );
    })
  );
  $("sources").parentElement.hidden = sources.length === 0;
}

async function submit(e) {
  e.preventDefault();
  const question = $("q").value.trim();
  if (state.busy || !question || !state.course) return;
  state.busy = true;
  $("go").disabled = true;
  $("result").hidden = false;
  $("asked").textContent = question;
  $("answer").replaceChildren(el("div", { class: "skeleton" }, el("i"), el("i"), el("i")));
  $("sources").replaceChildren();
  $("sources").parentElement.hidden = true;
  try {
    const res = await api.ask(state.course.course_id, question, state.lecture?.entry_id);
    $("answer").innerHTML = answerHtml(res.answer, res.sources); // fully escaped in render.js
    renderMath($("answer"));
    showSources(res.sources);
  } catch (err) {
    $("answer").replaceChildren(el("p", { class: "error" }, err instanceof api.BackendError ? err.message : "Something went wrong."));
  } finally {
    state.busy = false;
    $("go").disabled = false;
  }
}

// ---------- Routing ----------

async function route() {
  $("notice").hidden = true;
  const params = new URLSearchParams(location.search);
  const courseId = params.get("course");
  if (!courseId) {
    state.course = null;
    try {
      state.courses = await api.courses();
    } catch (err) {
      show(null);
      notice("Couldn't load courses", [err.message]);
      return;
    }
    renderHome();
    return;
  }
  if (state.course?.course_id !== courseId) {
    try {
      state.course = await api.course(courseId);
    } catch (err) {
      state.course = null;
      show(null);
      const missing = err.message === "course not synced";
      notice(
        missing ? "This course isn't synced yet" : "Couldn't load the course",
        [missing ? "Open it on Canvas and click Sync this course in the extension." : err.message],
        { label: "See all courses", run: () => go({}) }
      );
      return;
    }
    state.section = "all";
    $("result").hidden = true;
  }
  const lectureId = params.get("lecture");
  state.lecture = state.course.lectures.find((l) => l.entry_id === lectureId) || null;
  renderCourse();
}

async function start() {
  $("mode").hidden = !api.demo;
  setHealth("wait", "Checking");
  if (!(await api.health())) {
    setHealth("down", "Backend offline");
    show(null);
    notice("Can't reach the backend", [`Start it with make start in the repo, then reload. It should answer at ${api.BACKEND}.`], {
      label: "Preview with demo data instead",
      run: () => {
        api.useDemo();
        const p = new URLSearchParams(location.search);
        p.set("demo", "");
        history.replaceState({}, "", `${location.pathname}?${p.toString().replace(/demo=(&|$)/, "demo$1")}`);
        start();
      },
    });
    return;
  }
  setHealth("up", api.demo ? "Demo" : "Connected");
  route();
}

$("ask").addEventListener("submit", submit);
$("q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("ask").requestSubmit();
  }
});
$("q").addEventListener("input", () => {
  $("q").style.height = "auto";
  $("q").style.height = `${$("q").scrollHeight}px`;
});
$("answer").addEventListener("click", (e) => {
  const cite = e.target.closest(".cite");
  if (!cite) return;
  const li = $(`source-${cite.dataset.source}`);
  if (!li) return;
  li.scrollIntoView({ behavior: "smooth", block: "nearest" });
  li.classList.remove("flash");
  void li.offsetWidth;
  li.classList.add("flash");
});
$("home-link").addEventListener("click", (e) => {
  e.preventDefault();
  go({});
});
window.addEventListener("popstate", route);

start();
