import { test } from "node:test";
import assert from "node:assert/strict";
import { AUTH_ERR, AUTH_ERROR_TEXT, BACKEND_AUTH_ERRORS, authErrorText } from "../src/authErrors.js";

test("every key has its text, and the five keys are the only ones", () => {
  assert.deepEqual(Object.values(AUTH_ERR).sort(), ["exchange_failed", "login_failed", "network_error", "session_expired", "ucsd_only"]);
  for (const k of Object.values(AUTH_ERR)) {
    assert.equal(typeof AUTH_ERROR_TEXT[k], "string");
    assert.equal(authErrorText(k), AUTH_ERROR_TEXT[k]);
  }
  assert.equal(authErrorText("session_expired"), "Your session ended. Sign in again.");
  assert.match(authErrorText("network_error"), /make start/);
});

test("an unknown key gets the generic text", () => {
  for (const k of ["unverified_email", "google_failed", "auth_cancelled", "", undefined, "constructor", "__proto__"]) {
    assert.equal(authErrorText(k), "Sign in failed. Please try again.");
  }
});

test("the only backend-sent keys handled are login_failed and ucsd_only", () => {
  assert.deepEqual([...BACKEND_AUTH_ERRORS].sort(), ["login_failed", "ucsd_only"]);
});
