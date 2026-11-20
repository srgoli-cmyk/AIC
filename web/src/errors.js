// Errors the page shows or acts on. Their messages are safe to show: fixed text or the backend's
// fixed `detail`, never a response body.

/** A failure with a message for the student. `status` is the HTTP status when there was one. */
export class BackendError extends Error {}

// Called when any backend call answers 401: the session ended, so the page shows the sign-in screen.
const signedOutListeners = new Set();

export function onSignedOut(fn) {
  signedOutListeners.add(fn);
  return () => signedOutListeners.delete(fn);
}

/** Tells every listener the session ended. A listener that throws doesn't stop the others. */
export function signedOut() {
  for (const fn of signedOutListeners) {
    try {
      fn();
    } catch {
      // one broken listener must not keep the sign-in screen from showing
    }
  }
}

/** The stream could not be used at all (old backend, proxy, broken connection): ask /ask instead. */
export class StreamUnavailable extends Error {}

/** The student pressed Stop. */
export class Stopped extends Error {}
