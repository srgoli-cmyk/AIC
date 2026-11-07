#!/usr/bin/env node
// Runs the answer worker on http://localhost:8787 with plain Node, for trying the extension before
// the worker is deployed. Reads OPENROUTER_API_KEY (and OPENROUTER_MODEL) from the repo's .env
// without printing them. No rate limit, any origin.
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { handle } from "./src/worker.js";

const env = {};
try {
  const text = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", ".env"), "utf8");
  for (const line of text.split("\n")) {
    const m = /^\s*(OPENROUTER_API_KEY|OPENROUTER_MODEL)\s*=\s*(.*?)\s*$/.exec(line);
    if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
} catch {
  // No .env: answers report "not configured".
}
if (!env.OPENROUTER_API_KEY) console.log("proxy-dev: no OPENROUTER_API_KEY in .env, answers will fail");

const PORT = 8787;
createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const request = new Request(`http://localhost:${PORT}${req.url}`, {
    method: req.method,
    headers: Object.fromEntries(Object.entries(req.headers).filter(([, v]) => typeof v === "string")),
    body: req.method === "GET" || req.method === "HEAD" ? undefined : body,
  });
  const response = await handle(request, env);
  res.writeHead(response.status, Object.fromEntries(response.headers));
  res.end(Buffer.from(await response.arrayBuffer()));
}).listen(PORT, "127.0.0.1", () => console.log(`proxy-dev: http://localhost:${PORT}/v1/answer`));
