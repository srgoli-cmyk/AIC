// Turns an /ask answer into DOM. The answer can carry caption text, so it is untrusted: everything
// is escaped first, then a small Markdown subset (paragraphs, bold, italics, lists) is applied to
// the escaped text, citations become buttons, and KaTeX renders math with trust off.

const SOURCE_URL =
  /^https:\/\/(?:canvaskaf\.ucsd\.edu\/media\/t\/[0-9]_[a-z0-9]{8}(?:\/\d+)?(?:\?st=\d+)?|podcast\.ucsd\.edu\/watch\/[a-z]{2}[0-9]{2}\/[a-z0-9]{2,12}_[0-9]{3}-[0-9]{3}-[a-z]{2}\/[0-9]{1,3}(?:#\d+)?)$/;

export function safeUrl(url) {
  return SOURCE_URL.test(url) ? url : null;
}

export function sourceLabel(source) {
  const m = /\bLecture\s+(\d+)/i.exec(source.lecture_title);
  const name = m ? `Lecture ${Number(m[1])}` : source.lecture_title.replace(/[[\]]/g, " ").replace(/\s+/g, " ").trim();
  return `${name}, ${source.timestamp}`;
}

const escape = (s) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Math is pulled out before Markdown so `*` and `_` inside it are left alone.
const MATH = /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$(?!\s)[^$\n]+?(?<!\s)\$(?!\d)/g;

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
  let text = answer.replace(MATH, (m) => {
    math.push(m);
    return `\u0000${math.length - 1}\u0000`;
  });
  text = escape(text);

  const blocks = text.split(/\n\s*\n/).map((block) => {
    const lines = block.split("\n");
    if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
      return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*]\s+/, ""), labels)}</li>`).join("")}</ul>`;
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
      return `<ol${first > 1 ? ` start="${first}"` : ""}>${items.map((i) => `<li>${inline(i, labels)}</li>`).join("")}</ol>`;
    }
    return `<p>${inline(lines.join("<br>"), labels)}</p>`;
  });

  // Math goes back in escaped; KaTeX reads the text content, so &lt; still renders as <.
  return blocks.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => escape(math[Number(i)]));
}

export function renderMath(el) {
  if (!window.renderMathInElement) return;
  window.renderMathInElement(el, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "\\(", right: "\\)", display: false },
      { left: "$", right: "$", display: false },
    ],
    throwOnError: false,
    trust: false,
  });
}
