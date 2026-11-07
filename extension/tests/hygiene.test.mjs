import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";

const root = new URL("../", import.meta.url);
// The worker, popup, offscreen document and everything under src/ (the app page's own files in
// app/ also talk to the optional team backend, so they are checked separately).
const sources = [
  ...readdirSync(root).filter((f) => f.endsWith(".js")),
  ...readdirSync(new URL("src/", root), { recursive: true })
    .filter((f) => f.endsWith(".js"))
    .map((f) => `src/${f}`),
];

test("extension code never logs", () => {
  assert.ok(sources.length >= 10);
  for (const f of sources) {
    const text = readFileSync(new URL(f, root), "utf8");
    assert.doesNotMatch(text, /\bconsole\s*\./, `${f} uses console`);
  }
});

test("only the payload module builds the sync body", () => {
  // validate.js only checks a body before it is stored.
  for (const f of sources.filter((f) => f !== "src/payload.js" && f !== "src/local/validate.js")) {
    assert.doesNotMatch(readFileSync(new URL(f, root), "utf8"), /schema_version/, f);
  }
});

test("no source starts requests in parallel", () => {
  for (const f of sources) assert.doesNotMatch(readFileSync(new URL(f, root), "utf8"), /Promise\.(all|allSettled|race|any)/, f);
});

test("network calls go only to hosts in the manifest", () => {
  const manifest = JSON.parse(readFileSync(new URL("manifest.json", root), "utf8"));
  const allowed = manifest.host_permissions.map((p) => new URL(p.replace("/*", "/")).host);
  for (const f of sources) {
    for (const m of readFileSync(new URL(f, root), "utf8").matchAll(/https?:\/\/([a-z0-9.:-]+)/gi)) {
      assert.ok(allowed.includes(m[1]), `${f}: ${m[1]} is not in host_permissions`);
    }
  }
});
