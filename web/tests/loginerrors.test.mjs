import { test } from "node:test";
import assert from "node:assert/strict";
import { LOGIN_ERRORS, loginErrorText } from "../src/loginerrors.js";

test("exactly the backend's two keys and the page's own expired key have messages", () => {
  assert.deepEqual(Object.keys(LOGIN_ERRORS).sort(), ["expired", "login_failed", "ucsd_only"]);
  assert.match(loginErrorText("ucsd_only"), /@ucsd\.edu/);
  assert.match(loginErrorText("expired"), /ended/);
});

test("an unknown or odd key gets the generic message", () => {
  for (const key of ["google_failed", "unverified_email", "__proto__", "toString", "", undefined, "<b>x</b>"]) {
    assert.equal(loginErrorText(key), LOGIN_ERRORS.login_failed, String(key));
  }
});
