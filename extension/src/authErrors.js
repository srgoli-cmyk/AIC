// The sign-in error keys that can reach storage.authError, in one place.
// The backend sends only LOGIN_FAILED and UCSD_ONLY (/auth/extension/done?error=); the extension
// sets the other three itself.
export const AUTH_ERR = Object.freeze({
  LOGIN_FAILED: "login_failed",
  UCSD_ONLY: "ucsd_only",
  EXCHANGE_FAILED: "exchange_failed",
  NETWORK_ERROR: "network_error",
  SESSION_EXPIRED: "session_expired",
});

/** The only keys accepted from the backend's error= parameter. */
export const BACKEND_AUTH_ERRORS = Object.freeze([AUTH_ERR.LOGIN_FAILED, AUTH_ERR.UCSD_ONLY]);

export const AUTH_ERROR_TEXT = Object.freeze({
  [AUTH_ERR.LOGIN_FAILED]: "Sign in failed. Please try again.",
  [AUTH_ERR.UCSD_ONLY]: "Only verified @ucsd.edu Google accounts can sign in.",
  [AUTH_ERR.EXCHANGE_FAILED]: "Sign in could not be completed. Please try again.",
  [AUTH_ERR.NETWORK_ERROR]: "Cannot reach the backend. Check that make start is running.",
  [AUTH_ERR.SESSION_EXPIRED]: "Your session ended. Sign in again.",
});

/** Text for a stored key; an unknown key gets the generic login-failed text. */
export function authErrorText(key) {
  return Object.hasOwn(AUTH_ERROR_TEXT, key) ? AUTH_ERROR_TEXT[key] : AUTH_ERROR_TEXT[AUTH_ERR.LOGIN_FAILED];
}
