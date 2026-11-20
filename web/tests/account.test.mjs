import { test } from "node:test";
import assert from "node:assert/strict";
import { deleteState } from "../site/account.js";

test("the delete button shows only when it can be used", () => {
  assert.equal(deleteState("signed_in", { email: "a@ucsd.edu" }).button, true);
  assert.equal(deleteState("signed_out").button, false);
  assert.equal(deleteState("deleted").button, false);
  assert.equal(deleteState("deleting").busy, true);
});

test("the signed-in text names the account, and odd values don't break it", () => {
  assert.match(deleteState("signed_in", { email: "a@ucsd.edu" }).text, /a@ucsd\.edu/);
  assert.match(deleteState("signed_in", { email: 5 }).text, /your UCSD account/);
  assert.match(deleteState("signed_in", null).text, /your UCSD account/);
});

test("an unknown state is an error with a retry", () => {
  assert.deepEqual(deleteState("whatever"), { text: "Something went wrong. Try again in a moment.", button: true });
});
