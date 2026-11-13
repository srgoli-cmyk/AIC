// Turns an /ask answer into HTML. The answer can carry caption text, so it is untrusted:
// math is taken out first, link and image syntax and bare URLs are removed from the rest, everything
// is escaped, and then a small Markdown subset (paragraphs, bold, italics, lists) is applied to the
// escaped text and citations become buttons. KaTeX renders math later, with trust off, and only
// inside the spans made here, so a `$` in plain text never becomes math.

const SOURCE_URL =
  /^https:\/\/(?:canvaskaf\.ucsd\.edu\/media\/t\/[0-9]_[a-z0-9]{8}(?:\/\d+)?(?:\?st=\d+)?|podcast\.ucsd\.edu\/watch\/[a-z]{2}[0-9]{2}\/[a-z0-9]{2,12}_[0-9]{3}-[0-9]{3}-[a-z]{2}\/[0-9]{1,3}(?:#\d+)?|canvas\.ucsd\.edu\/courses\/\d{1,15}\/files\/\d{1,16})$/;

/** A lecture page at a time, or a Canvas file of a course (slides). Anything else is not linked. */
export function safeUrl(url) {
  return SOURCE_URL.test(url) ? url : null;
}

/**
 * The link of a source. A slide source from the course's own website may be any https page:
 * its link comes from the backend's stored documents, never from the model's words.
 */
export function sourceUrl(source) {
  if (source?.kind !== "slides") return safeUrl(source?.url);
  try {
    const u = new URL(source.url);
    return u.protocol === "https:" && !u.username && !u.password ? u.href : null;
  } catch {
    return null;
  }
}

/** The words of a slide link. A file off Canvas names the site it opens, so the student sees
 * where the link goes before clicking it. */
export function slidesLinkText(url) {
  const host = new URL(url).hostname;
  return host === "canvas.ucsd.edu" ? "Open the slides ↗" : `Open on ${host} ↗`;
}

export function sourceLabel(source) {
  // A page of a document is cited as `Slides: <name>, p. <page>` (backend/app/answer.py).
  if (source.kind === "slides") return `Slides: ${source.lecture_title}, ${source.timestamp}`;
  const m = /\bLecture\s+(\d+)/i.exec(source.lecture_title);
  const name = m ? `Lecture ${Number(m[1])}` : source.lecture_title.replace(/[[\]]/g, " ").replace(/\s+/g, " ").trim();
  return `${name}, ${source.timestamp}`;
}

const escape = (s) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// --- math ---
// \( \) and \[ \] are always math. With dollars, money must stay text, so spaces just inside the
// dollars only count when the content is one token or has a math sign: `$ x $`, `$ x = a $`, but not
// "$ 5 for the $ amount". A `$` after a word never opens those, and one before a digit never closes
// them, as in "$ 5 and $ 6". Tight `$x$` closes anywhere except before a digit, so "$5 and $10" stays
// text.
const LOOKS_LIKE_MATH = String.raw`(?=[^$\n]*[\\=^_{<>+\-\[(])`;
const SPACED_END = String.raw`(?<!\\)\$(?![ \t]*\d)(?!\$)`;
const MATH = new RegExp(
  [
    String.raw`(?<!\\)\\\((?<inline>[\s\S]+?)(?<!\\)\\\)`,
    String.raw`(?<!\\)\\\[(?<display>[\s\S]+?)(?<!\\)\\\]`,
    String.raw`(?<![\\$])\$\$(?<block>[^$]+?)(?<!\\)\$\$(?!\$)`,
    String.raw`(?<![\\$])\$(?<tight>(?![\s$])[^$\n]*?)(?<![\s\\])\$(?![\d$])`,
    String.raw`(?<![\w\\$])\$[ \t]+(?<spaced>[^\s$]+[ \t]*|${LOOKS_LIKE_MATH}[^$\s][^$\n]*?)${SPACED_END}`,
    String.raw`(?<![\w\\$])\$(?<spacedEnd>(?![\s$\d])(?:[^\s$]+|${LOOKS_LIKE_MATH}[^$\n]*?)[ \t]+)${SPACED_END}`,
  ].join("|"),
  "g"
);

/** The TeX to render for one match, or null if it must stay text. */
function mathOf(groups, labels) {
  const explicit = groups.inline !== undefined || groups.display !== undefined;
  const display = groups.display !== undefined || groups.block !== undefined;
  let tex = groups.inline ?? groups.display ?? groups.block ?? groups.tight ?? groups.spaced ?? groups.spacedEnd;
  if ((explicit && tex.includes("$")) || labels.some((l) => tex.includes(l))) return null;
  if (!display) {
    // Inline math is one line, and KaTeX needs no spaces just inside it.
    tex = tex.split(/\s+/).filter(Boolean).join(" ");
    if (tex.endsWith("\\")) tex += " ";
  }
  return tex.trim() ? { tex, display } : null;
}

// --- links ---
// No link or image is ever built from the answer, so these only keep markup out of what's shown:
// `[text](url)` keeps its text, an image goes, and bare URLs go. The only links are the source
// list's own, from safeUrl().
const LINK_OR_IMAGE = /(!?)\[((?:[^[\]]|\[[^[\]]*\])*)\]\((?:[^()]|\([^()]*\))*\)/g;
const URL_FORMS = [
  /(?:[a-z][a-z0-9+.-]*:)?\/\/[^\s[\]\u0000]+/gi,
  /www\.[^\s[\]\u0000]*/gi,
  /[^\s[\]<>()\u0000]*@[^\s[\]<>()\u0000]+/g,
];

function withoutLinks(text, labels) {
  let previous;
  while (text !== previous) {
    previous = text;
    let linked;
    while (text !== linked) {
      linked = text;
      text = text.replace(LINK_OR_IMAGE, (_, bang, label) => (bang ? "" : labels.includes(label) ? `[${label}]` : label));
    }
    for (const pattern of URL_FORMS) text = text.replace(pattern, "");
  }
  return text;
}

// --- Markdown subset ---

function inline(text, labels) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)(.+?)(?<!\s)\*/g, "$1<em>$2</em>")
    .replace(/\[([^[\]\n]+)\]/g, (whole, label) => {
      const i = labels.indexOf(label);
      return i === -1 ? whole : `<button type="button" class="cite" data-source="${i}">${label}</button>`;
    });
}

export function answerHtml(answer, sources) {
  const labels = sources.map(sourceLabel);
  const math = [];
  // \u0000 marks where math goes, so the answer itself may not contain it.
  let text = String(answer)
    .replace(/\u0000/g, "")
    .replace(MATH, (whole, ...rest) => {
      const found = mathOf(rest.at(-1), labels);
      if (!found) return whole;
      math.push(found);
      return `\u0000${math.length - 1}\u0000`;
    });
  text = escape(withoutLinks(text, labels).replace(/\\\$/g, "$"));

  const escapedLabels = labels.map(escape);
  const blocks = text.split(/\n\s*\n/).map((block) => {
    const lines = block.split("\n");
    if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
      return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*]\s+/, ""), escapedLabels)}</li>`).join("")}</ul>`;
    }
    // Numbered items: answers often leave blank lines between them, so each block keeps its own
    // first number; indented lines after an item belong to it.
    if (/^\s*\d+[.)]\s+/.test(lines[0])) {
      const items = [];
      for (const l of lines) {
        if (/^\s*\d+[.)]\s+/.test(l)) items.push(l.replace(/^\s*\d+[.)]\s+/, ""));
        else items[items.length - 1] += `<br>${l.trim()}`;
      }
      const first = Math.min(Number(/^\s*(\d+)/.exec(lines[0])[1]) || 1, 999);
      return `<ol${first > 1 ? ` start="${first}"` : ""}>${items.map((i) => `<li>${inline(i, escapedLabels)}</li>`).join("")}</ol>`;
    }
    return `<p>${inline(lines.join("<br>"), escapedLabels)}</p>`;
  });

  // KaTeX reads the span's text, so escaped TeX such as &lt; still renders as <.
  return blocks.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => {
    const { tex, display } = math[Number(i)];
    return `<span class="math" data-display="${display ? 1 : 0}">${escape(tex)}</span>`;
  });
}

export function renderMath(el) {
  const katex = globalThis.window?.katex;
  if (!katex) return;
  for (const span of el.querySelectorAll("span.math")) {
    katex.render(span.textContent, span, {
      displayMode: span.dataset.display === "1",
      throwOnError: false,
      trust: false,
    });
  }
}
