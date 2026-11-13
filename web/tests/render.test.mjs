import { test } from "node:test";
import assert from "node:assert/strict";
import { answerHtml, renderMath, safeUrl, sourceLabel } from "../src/render.js";

const SOURCE = {
  lecture_title: "Lecture 8 - Product Rule [A00]",
  timestamp: "0:04",
  url: "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/111111?st=1",
  snippet: "s",
};
const NUMBERLESS = { ...SOURCE, lecture_title: "Review  [part 2]", timestamp: "1:15" };

const html = (answer, sources = []) => answerHtml(answer, sources);
const mathSpans = (out) => [...out.matchAll(/<span class="math" data-display="(\d)">([\s\S]*?)<\/span>/g)].map((m) => [m[2], m[1]]);

// ---- escaping ----

test("HTML in the answer is escaped, never markup", () => {
  const out = html(`<img src=x onerror=alert(1)> <a href="javascript:x">y</a> <script>z</script> & "q" 'r'`);
  assert.ok(!/<(img|a|script)\b/i.test(out), out);
  assert.match(out, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(out, /&amp; &quot;q&quot; &#39;r&#39;/);
});

test("HTML inside math is escaped too", () => {
  const out = html("$<img src=x>$");
  assert.ok(!out.includes("<img"));
  assert.deepEqual(mathSpans(out), [["&lt;img src=x&gt;", "0"]]);
});

test("the math marker character in an answer can't inject or crash", () => {
  const out = html("a \u00000\u0000 b \u00009\u0000 $x$");
  assert.deepEqual(mathSpans(out), [["x", "0"]]);
  assert.ok(!out.includes("\u0000"));
});

test("bold, italics, lists and paragraphs still work", () => {
  assert.equal(html("**Step 1** and *why*"), "<p><strong>Step 1</strong> and <em>why</em></p>");
  assert.equal(html("- a\n- b"), "<ul><li>a</li><li>b</li></ul>");
  assert.equal(html("2. two\n3. three"), '<ol start="2"><li>two</li><li>three</li></ol>');
  assert.equal(html("one\n\ntwo"), "<p>one</p><p>two</p>");
});

// ---- citation buttons ----

test("a citation of a source becomes a button that points at it", () => {
  const out = html("See [Lecture 8, 0:04] and [Review part 2, 1:15].", [SOURCE, NUMBERLESS]);
  assert.match(out, /<button type="button" class="cite" data-source="0">Lecture 8, 0:04<\/button>/);
  assert.match(out, /<button type="button" class="cite" data-source="1">Review part 2, 1:15<\/button>/);
});

test("a citation of nothing stays text", () => {
  const out = html("See [Lecture 9, 1:00].", [SOURCE]);
  assert.ok(!out.includes("<button"));
  assert.match(out, /\[Lecture 9, 1:00\]/);
});

test("a citation with a forged link target still becomes the button", () => {
  const out = html("See [Lecture 8, 0:04](https://evil.example).", [SOURCE]);
  assert.ok(!out.includes("evil"));
  assert.match(out, /data-source="0">Lecture 8, 0:04<\/button>/);
});

test("a citation is never swallowed by math", () => {
  for (const answer of [
    "It cost $ 5 [Lecture 8, 0:04] and the $ rate rose.",
    "See $AAPL [Lecture 8, 0:04] and $TSLA now.",
    "A $ x = [Lecture 8, 0:04] $ here.",
    String.raw`A \( [Lecture 8, 0:04] \) here.`,
  ]) {
    assert.match(html(answer, [SOURCE]), /data-source="0"/, answer);
  }
});

test("source labels match the backend's citation form", () => {
  assert.equal(sourceLabel(SOURCE), "Lecture 8, 0:04");
  assert.equal(sourceLabel({ ...SOURCE, lecture_title: "lecture 08 x" }), "Lecture 8, 0:04");
  assert.equal(sourceLabel(NUMBERLESS), "Review part 2, 1:15");
});

// ---- math ----

const CASES = [
  ["Let $ x = a $ here.", [["x = a", "0"]]],
  ["Let $x = a $ here.", [["x = a", "0"]]],
  ["Let $ x = a$ here.", [["x = a", "0"]]],
  [String.raw`So $ x \geq a $ holds.`, [[String.raw`x \geq a`, "0"]]],
  [String.raw`So \( x \geq a \) holds.`, [[String.raw`x \geq a`, "0"]]],
  [String.raw`So \[ x \geq a \] holds.`, [[String.raw` x \geq a `, "1"]]],
  ["$$\n\\int_a^b f\n$$", [["\n\\int_a^b f\n", "1"]]],
  ["\\( x\n+ y \\)", [["x + y", "0"]]],
  ["On $[a, b]$ and $ [a, b] $.", [["[a, b]", "0"], ["[a, b]", "0"]]],
  ["$ x < y $", [["x &lt; y", "0"]]],
  [String.raw`Then \( x \) 5 times, and \(x\) 5 times.`, [["x", "0"], ["x", "0"]]],
  [String.raw`\(a\)\(b\) and \(x\)2`, [["a", "0"], ["b", "0"], ["x", "0"]]],
  [String.raw`So 2$\pi r$ and f$x$ and x$^2$.`, [[String.raw`\pi r`, "0"], ["x", "0"], ["^2", "0"]]],
  ["Use the $ sign for $x$ here.", [["x", "0"]]],
  ["Costs $a for $x$ here.", [["x", "0"]]],
  [String.raw`$ x\ $`, [[String.raw`x\ `, "0"]]],
  ["$a//b$", [["a//b", "0"]]],
];

test("math is found, trimmed and kept as written", () => {
  for (const [answer, expected] of CASES) assert.deepEqual(mathSpans(html(answer)), expected, answer);
});

test("money and other plain dollars stay text", () => {
  for (const answer of [
    "It costs $5.",
    "From $5 to $10.",
    "Paid $5 and $ 6.",
    "Paid $ 5 and $ 6.",
    "It was $5, then $ more.",
    "US$ and CA$ rates.",
    "Paid $ 5 for the $ amount.",
    "A $ sign and a $ sign.",
    "It costs 5 $ and 6 $.",
    "That is 10 $, and the other is 20 $.",
    "Compare $AAPL and $TSLA today.",
    "The vars $HOME and $PATH are set.",
  ]) {
    const out = html(answer);
    assert.deepEqual(mathSpans(out), [], answer);
    assert.ok(out.includes("$"), answer);
  }
});

test("an escaped dollar shows as a dollar", () => {
  assert.equal(html(String.raw`It costs \$5.`), "<p>It costs $5.</p>");
});

test("explicit delimiters holding a dollar stay text", () => {
  assert.deepEqual(mathSpans(html(String.raw`\( \$5 + x \)`)), []);
});

test("renderMath renders only the math spans, with trust off", () => {
  const calls = [];
  globalThis.window = { katex: { render: (tex, el, opts) => calls.push([tex, el.dataset.display, opts]) } };
  const spans = [
    { textContent: "x", dataset: { display: "0" } },
    { textContent: "y", dataset: { display: "1" } },
  ];
  const el = { querySelectorAll: (sel) => (sel === "span.math" ? spans : []) };
  try {
    renderMath(el);
  } finally {
    delete globalThis.window;
  }
  assert.deepEqual(
    calls.map(([tex, , o]) => [tex, o.displayMode, o.trust, o.throwOnError]),
    [
      ["x", false, false, false],
      ["y", true, false, false],
    ]
  );
});

test("renderMath does nothing without KaTeX", () => {
  assert.doesNotThrow(() => renderMath({ querySelectorAll: () => [] }));
});

// ---- links, images and unsafe URLs ----

test("no form of link or image survives, and only its text stays", () => {
  for (const [answer, shown] of [
    ["[click](https://evil.example)", "click"],
    ["[click](https://evil.example 'title')", "click"],
    ["[click](javascript:alert(1))", "click"],
    ["![img](https://evil.example/x.png)", ""],
    ["[![img](https://evil.example/x.png)](https://evil.example)", ""],
    ["<https://evil.example>", "&lt;"],
    ["see https://evil.example/a_b now", "see  now"],
    ["see //evil.example/a now", "see  now"],
    ["see www.evil.example now", "see  now"],
    ["mail a@evil.example now", "mail  now"],
  ]) {
    const out = html(answer);
    assert.ok(!out.includes("evil"), `${answer} -> ${out}`);
    assert.ok(!out.includes("javascript"), answer);
    assert.equal(out, `<p>${shown}</p>`, answer);
  }
});

test("safeUrl accepts lecture pages only", () => {
  for (const url of [
    "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/111111",
    "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/111111?st=127",
    "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa?st=0",
    "https://podcast.ucsd.edu/watch/fa26/math20a_001-000-le/3",
    "https://podcast.ucsd.edu/watch/fa26/math20a_001-000-le/3#57",
  ]) {
    assert.equal(safeUrl(url), url, url);
  }
  for (const url of [
    "https://evil.example/media/t/1_aaaaaaaa/111111?st=5",
    "https://canvaskaf.ucsd.edu.evil.example/media/t/1_aaaaaaaa?st=5",
    "http://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/111111",
    "https://canvaskaf.ucsd.edu/media/other",
    "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/111111?st=5&x=1",
    "https://canvaskaf.ucsd.edu/media/t/1_aaaaaaaa/111111?st=5\n",
    "javascript:alert(1)",
    "https://podcast.ucsd.edu/watch/fa26/math20a_001-000-le/3?x=1",
    "",
  ]) {
    assert.equal(safeUrl(url), null, url);
  }
});

test("a slide source is labelled and cited by document name and page", () => {
  const slide = { kind: "slides", lecture_title: "Lecture 5 slides", entry_id: "canvas-file-7", start_sec: 12, end_sec: 12, timestamp: "p. 12", url: "https://canvas.ucsd.edu/courses/999999/files/7", snippet: "s", page: 12 };
  assert.equal(sourceLabel(slide), "Slides: Lecture 5 slides, p. 12", "not mistaken for Lecture 5");
  const html = answerHtml("Keep one factor [Slides: Lecture 5 slides, p. 12]. Not given [Slides: Other, p. 1].", [slide]);
  assert.match(html, /<button[^>]*data-source="0"[^>]*>/);
  assert.equal((html.match(/<button/g) || []).length, 1);
  assert.equal(safeUrl(slide.url), slide.url);
  for (const bad of ["https://canvas.ucsd.edu/courses/1/files/7/download", "https://canvas.ucsd.edu/login", "https://canvas.ucsd.edu.evil.example/courses/1/files/7", "https://canvas.ucsd.edu/courses/1/files/7?verifier=x"]) {
    assert.equal(safeUrl(bad), null, bad);
  }
});
