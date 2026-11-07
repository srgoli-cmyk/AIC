// Standalone (the Chrome Web Store build): lectures are stored and searched in this browser, and
// only the answer step calls out, to ASK_URL. scripts/package_extension.mjs replaces ASK_URL with
// the deployed proxy's address (and the manifest's matching host permission) when it builds the
// store zip; this localhost value is for `make proxy-dev`.
export const ASK_URL = "http://localhost:8787/v1/answer";
// The search page inside the extension; ?course=<course_id> opens that course.
export const APP_PAGE = "app.html";

// The optional team backend (backend/) answers /health with this title.
export const BACKEND_TITLE = "AI Lecture Search";

export const CANVAS_HOST = "canvas.ucsd.edu";
export const CANVAS_ORIGIN = `https://${CANVAS_HOST}`;
export const KAF_HOST = "canvaskaf.ucsd.edu";
export const PODCAST_HOST = "podcast.ucsd.edu";
export const PODCAST_ORIGIN = `https://${PODCAST_HOST}`;

// First 4 characters of a Kaltura v2 session: base64 of "v2|". Nothing here reads a player, so this
// is assumed rather than read from a page. If lecture pages show sessions with another start,
// change this one value (the frame functions and the pre-send guard both use it).
export const KS_PREFIX = "djJ8";

export const MAX_CANVAS_PAGES = 20;
export const MAX_GALLERY_PAGES = 20;
export const MAX_FILES = 500;
export const MAX_INSTRUCTORS = 20;
export const MAX_SECTIONS = 50;
export const MAX_LECTURES = 200;
export const CATEGORY_WAIT_MS = 30000;
export const CATEGORY_POLL_MS = 1000;
export const MAX_FRAME_ERRORS_IN_ROW = 3;
// podcast.ucsd.edu: how long the tab may take to land on the site (a UCSD login can redirect).
export const PODCAST_WAIT_MS = 30000;
export const LIST_CALL_MS = 60000;

// Time limits. Requests give up after FETCH_TIMEOUT_MS; the sync POST may carry many captions.
export const FETCH_TIMEOUT_MS = 25000;
export const SYNC_POST_TIMEOUT_MS = 60000;
// One injected call must come back within these limits or it counts as failed.
export const CATEGORY_PROBE_MS = 10000;
export const GALLERY_CALL_MS = 120000;
export const ENTRY_CALL_MS = 120000;
