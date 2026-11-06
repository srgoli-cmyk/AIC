// Every message shown to the user is one of these fixed strings. Errors never carry response
// bodies, URLs or session values; the optional detail is an HTTP status or a call name.
import { KS_PREFIX } from "./config.js";

export const MESSAGES = {
  BUSY: "A sync is already running.",
  NOT_COURSE_PAGE: "Open a Canvas course page (canvas.ucsd.edu/courses/<id>) and try again.",
  BACKEND_DOWN: "Cannot reach the backend on localhost:8000. Start it and try again.",
  BACKEND_OTHER: "Port 8000 is answered by another app, not AI Lecture Search. Stop it and start the backend.",
  BACKEND_REJECTED: "The backend rejected the sync.",
  CANVAS_FAILED: "Canvas request failed.",
  CANVAS_LOGIN: "Could not read the course from Canvas. Log in to Canvas and try again.",
  COURSE_INCOMPLETE: "Canvas returned a course without a code or name.",
  NO_MEDIA_GALLERY: "This course has no Media Gallery tab.",
  TAB_FAILED: "Could not open the Media Gallery tab.",
  NO_KAF_FRAME:
    "The Media Gallery did not load in the background tab. Check that you are logged in to Canvas and that the course's Media Gallery tab opens, then sync again.",
  KAF_FRAME_NO_CATEGORY:
    "The Media Gallery loaded but its lecture category could not be found. Open the course's Media Gallery tab in a normal tab, then sync again. If this repeats, the gallery may be empty.",
  NO_ENTRIES: "No lectures were found in the Media Gallery.",
  NO_CAPTIONS_READ: "No captions could be read for any lecture, so nothing was sent.",
  NO_SESSION_FOUND: `No Kaltura session was found on any lecture page. The assumed session prefix is "${KS_PREFIX}"; check it against a real lecture page. Nothing was sent.`,
  KS_IN_PAYLOAD: "Sync stopped: the data to send contained a Kaltura session. Nothing was sent.",
  INTERRUPTED: "The last sync was interrupted.",
  PODCAST_LOGIN: "Could not open podcast.ucsd.edu. Log in there with your UCSD account, then sync again.",
  PODCAST_NONE: "podcast.ucsd.edu lists no recordings for your classes this quarter.",
  NO_CANVAS_MATCH: "None of your podcast classes matched a Canvas course, so nothing was sent.",
  ALL_FAILED: "No class could be synced.",
  UNKNOWN: "Sync failed.",
};

export class SyncError extends Error {
  constructor(code, detail) {
    super(MESSAGES[code] + (detail ? ` (${detail})` : ""));
    this.name = "SyncError";
    this.code = code;
    this.detail = detail;
  }
}

export function messageFor(e) {
  return e instanceof SyncError ? e.message : MESSAGES.UNKNOWN;
}
