export const BACKEND_URL = "http://localhost:8000";
export const BACKEND_TITLE = "AI Lecture Search";

export const CANVAS_HOST = "canvas.ucsd.edu";
export const CANVAS_ORIGIN = `https://${CANVAS_HOST}`;
export const KAF_HOST = "canvaskaf.ucsd.edu";

// First 4 characters of a Kaltura v2 session: base64 of "v2|". Nothing here reads a player, so this
// is assumed rather than read from a page. If lecture pages show sessions with another start,
// change this one value (the frame functions and the pre-send guard both use it).
export const KS_PREFIX = "djJ8";

export const MAX_CANVAS_PAGES = 20;
export const MAX_GALLERY_PAGES = 20;
export const MAX_FILES = 500;
export const MAX_LECTURES = 200;
export const CATEGORY_WAIT_MS = 30000;
export const CATEGORY_POLL_MS = 1000;
export const MAX_FRAME_ERRORS_IN_ROW = 3;

// Time limits. Requests give up after FETCH_TIMEOUT_MS; the sync POST may carry many captions.
export const FETCH_TIMEOUT_MS = 25000;
export const SYNC_POST_TIMEOUT_MS = 60000;
// One injected call must come back within these limits or it counts as failed.
export const CATEGORY_PROBE_MS = 10000;
export const GALLERY_CALL_MS = 120000;
export const ENTRY_CALL_MS = 120000;
