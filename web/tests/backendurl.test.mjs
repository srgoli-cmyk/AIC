import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_BACKEND, backendFor, localBackend } from "../src/backendurl.js";

const at = (url) => new URL(url);
const q = (s) => new URLSearchParams(s);

test("served by the backend, the page uses its own origin", () => {
  assert.equal(backendFor(at("http://localhost:8000/app/?course=x"), q("course=x")), "http://localhost:8000");
});

test("served on its own, the page uses the default local backend", () => {
  assert.equal(backendFor(at("http://127.0.0.1:5173/"), q("")), DEFAULT_BACKEND);
});

test("?backend= may point at another local port", () => {
  for (const value of ["http://localhost:8010", "http://127.0.0.1:9000/", "http://localhost"]) {
    assert.equal(backendFor(at("http://localhost:8000/app/"), q(`backend=${encodeURIComponent(value)}`)), new URL(value).origin, value);
  }
});

test("?backend= pointing anywhere else is ignored", () => {
  for (const value of [
    "https://evil.example",
    "http://evil.example",
    "https://localhost:8000",
    "http://localhost.evil.example",
    "http://127.0.0.1.evil.example",
    "http://user:pass@localhost:8000",
    "http://localhost:8000/other/path",
    "http://localhost:8000/?x=1",
    "javascript:alert(1)",
    "//evil.example",
    "",
  ]) {
    assert.equal(localBackend(value), null, value);
    assert.equal(backendFor(at("http://localhost:8000/app/"), q(`backend=${encodeURIComponent(value)}`)), "http://localhost:8000", value);
  }
});
