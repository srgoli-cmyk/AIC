// Errors the page shows or acts on. Their messages are safe to show: fixed text or the backend's
// fixed `detail`, never a response body.

/** A failure with a message for the student. */
export class BackendError extends Error {}

/** The stream could not be used at all (old backend, proxy, broken connection): ask /ask instead. */
export class StreamUnavailable extends Error {}

/** The student pressed Stop. */
export class Stopped extends Error {}
