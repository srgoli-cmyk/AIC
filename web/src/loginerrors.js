// The sign-in screen's messages. `?auth_error=` comes from the backend's Google callback, which sends
// only login_failed or ucsd_only (contracts/api.md); `expired` is the page's own, after a 401.

export const LOGIN_ERRORS = {
  login_failed: "Sign in failed. Please try again.",
  ucsd_only: "Only verified @ucsd.edu Google accounts can sign in. Please use your UCSD account.",
  expired: "Your session ended. Please sign in again.",
};

/** The text for a key; anything unknown (a hand-edited URL) gets the generic one. */
export function loginErrorText(key) {
  return Object.hasOwn(LOGIN_ERRORS, key) ? LOGIN_ERRORS[key] : LOGIN_ERRORS.login_failed;
}
