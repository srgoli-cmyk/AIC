// Builds the Chrome Web Store zip: dist/lecture-search-<version>.zip.
//
//   ASK_PROXY_URL=https://answer.example.workers.dev node scripts/package_extension.mjs
//
// The zip holds only what the extension runs: no tests, node_modules or package files. The answer
// address is switched from localhost to ASK_PROXY_URL in config.js and in the manifest, and the
// build stops if anything still points at localhost or a vendored file is missing (run
// `make vendor` first).
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EXT = join(ROOT, "extension");
const DIST = join(ROOT, "dist");
const BUILD = join(DIST, "build");
const LOCAL = "http://localhost:8787";

// What ships. Everything else in extension/ stays out.
const INCLUDE = ["manifest.json", "background.js", "popup.html", "popup.js", "offscreen.html", "offscreen.js", "app.html", "privacy.html", "app", "src", "icons", "vendor"];
const REQUIRED_VENDOR = [
  "vendor/transformers/transformers.min.js",
  "vendor/transformers/ort-wasm-simd-threaded.jsep.mjs",
  "vendor/transformers/ort-wasm-simd-threaded.jsep.wasm",
  "vendor/models/Xenova/bge-small-en-v1.5/onnx/model_quantized.onnx",
  "vendor/models/Xenova/bge-small-en-v1.5/tokenizer.json",
  "vendor/models/Xenova/bge-small-en-v1.5/config.json",
  "vendor/katex/katex.min.js",
  "vendor/katex/katex.min.css",
  "vendor/fonts/fonts.css",
];

function fail(msg) {
  process.stderr.write(`package: ${msg}\n`);
  process.exit(1);
}

function proxyOrigin() {
  const raw = process.env.ASK_PROXY_URL;
  if (!raw) fail("set ASK_PROXY_URL to the deployed answer service, e.g. https://lecture-search-answer.<you>.workers.dev");
  let u;
  try {
    u = new URL(raw);
  } catch {
    fail("ASK_PROXY_URL is not a URL");
  }
  if (u.protocol !== "https:") fail("ASK_PROXY_URL must use https");
  if (u.username || u.password || u.search || u.hash) fail("ASK_PROXY_URL must be a plain origin");
  if (/^(localhost|127\.|\[::1\])/.test(u.hostname)) fail("ASK_PROXY_URL must not be a local address");
  return u.origin;
}

function walk(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const origin = proxyOrigin();
for (const f of REQUIRED_VENDOR) if (!existsSync(join(EXT, f))) fail(`missing ${f}: run make vendor`);

rmSync(BUILD, { recursive: true, force: true });
mkdirSync(BUILD, { recursive: true });
for (const f of INCLUDE) {
  if (!existsSync(join(EXT, f))) fail(`missing extension/${f}`);
  cpSync(join(EXT, f), join(BUILD, f), { recursive: true, filter: (src) => !src.endsWith(".DS_Store") });
}

// The answer address.
const configPath = join(BUILD, "src/config.js");
const config = readFileSync(configPath, "utf8");
const askLine = `export const ASK_URL = "${LOCAL}/v1/answer";`;
if (!config.includes(askLine)) fail("ASK_URL line in src/config.js changed; update this script");
writeFileSync(configPath, config.replace(askLine, `export const ASK_URL = "${origin}/v1/answer";`));

const manifestPath = join(BUILD, "manifest.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
manifest.host_permissions = manifest.host_permissions.map((h) => (h === `${LOCAL}/*` ? `${origin}/*` : h));
writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");

// Nothing may still use the local answer service, and the manifest may not reach any local address.
// (app/api.js keeps a localhost:8000 fallback for the optional team backend; the store build never
// takes that path, since it always runs as a chrome-extension:// page.)
if (/localhost|127\.0\.0\.1/.test(JSON.stringify(manifest))) fail("manifest still points at localhost");
for (const p of walk(BUILD)) {
  const rel = relative(BUILD, p);
  if (rel.startsWith("vendor/") || !/\.(js|mjs|html|json|css)$/.test(rel)) continue;
  if (/(localhost|127\.0\.0\.1):8787/.test(readFileSync(p, "utf8"))) fail(`${rel} still uses the local answer service`);
}

const zip = join(DIST, `lecture-search-${manifest.version}.zip`);
rmSync(zip, { force: true });
execFileSync("zip", ["-qr", "-X", zip, "."], { cwd: BUILD });
const mb = (statSync(zip).size / 1e6).toFixed(1);
process.stdout.write(`package: ${relative(ROOT, zip)} (${mb} MB), answers from ${origin}\n`);
