# Approval request: branch `Allen`

For: Igor (maintainer), or an agent reviewing on his behalf.
From: Allen.
Base: `main` at `c2bd624` ("revise README"). The branch is up to date with `main`.

This branch changes two things that need maintainer approval under the repo's rules:

1. **The sync contract** (`contracts/`): `contracts/api.md` says only the maintainer changes it, after approval.
2. **Extension permissions** (`extension/manifest.json`): `scripts/check_manifest.py` says permissions may only grow with an approved change.

Everything else on the branch (the web course page, tests, popup) needs only a normal review.

## Instructions for the reviewing agent

Work through the steps in order. Stop and report to Igor if any step fails or anything below doesn't match the code.

1. Check out the branch and confirm the base:
   ```bash
   git fetch origin
   git checkout Allen
   git merge-base --is-ancestor origin/main HEAD && echo "up to date with main"
   ```
2. Run every check. Postgres must be running for the backend tests (`make db`):
   ```bash
   make check
   ```
   Expected: `ALL CHECKS PASSED`, `manifest: ok`, and no failing tests.
3. Read the diffs that need approval:
   ```bash
   git diff origin/main -- contracts/ backend/app/schemas.py backend/db/init.sql extension/manifest.json scripts/check_manifest.py
   ```
4. Check each item in "What needs approval" below against that diff. Every change should be listed, and every listed change should be in the diff.
5. Check the security properties in "What to verify" with the commands given.
6. Report to Igor: approve, or the specific items that are wrong. Don't merge to `main` yourself unless Igor says so.

## What needs approval

### A. Contract: `contracts/sync_payload.schema.json`, `backend/app/schemas.py`

All new fields are optional, so existing payloads stay valid. `additionalProperties: false` / `extra="forbid"` is unchanged.

| Field | Type and limits | Why |
|---|---|---|
| `course.term_start`, `course.term_end` | date | The course page shows the quarter's dates. |
| `course.instructors` | up to 20 strings, 1 to 200 chars | Teacher display names only, for the course page. No ids, emails or avatars. |
| `course.sections` | up to 50 strings, 1 to 100 chars | Canvas section names, so the page can show which sections have recordings. |
| `lectures[].recorded_start` | date-time | When Kaltura created the recording, used to show meeting days and times. |
| `lectures[].media_url` pattern | now also accepts `https://podcast.ucsd.edu/watch/<term>/<section>/<n>` | Lectures synced from podcast.ucsd.edu link to their own lecture page. The canvaskaf form is unchanged. |

The JSON Schema and the pydantic pattern are kept identical, and `test_pydantic_media_url_pattern_matches_the_json_schema` checks it. The new podcast pattern rejects queries, fragments, section-only pages, `/kaltura` pages, `..` paths and lookalike hosts (`backend/tests/test_contract.py`).

### B. API: `contracts/api.md`, `backend/app/main.py`

- **New `GET /courses/{course_id}`:** course details plus the stored lectures, without captions. It is read-only and returns 404 for an unknown course.
- **`POST /ask` takes an optional `entry_id`:** searches one lecture instead of the whole course. It is validated against the entry id pattern.
- **`sources[].url` for a podcast lecture:** uses `#<seconds>` instead of `?st=<seconds>`, because the podcast player reads its start time from the fragment.
- **`/app/` serves `web/` as static files:** the extension popup links to a course page there.

### C. Database: `backend/db/init.sql`

These are additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements only, for the fields in A. Nothing is dropped or rewritten.

### D. Extension permissions: `extension/manifest.json`, `scripts/check_manifest.py`

| Permission | Why | Install warning |
|---|---|---|
| `alarms` | Wakes the worker every 15 minutes, so each tracked class syncs once a day, the first time the computer is used after 5 AM. | None |
| `idle` | Syncs only while someone is at the computer. | None |
| host `https://podcast.ucsd.edu/*` | "Sync all my classes": reads the student's own My Podcasts list and lecture pages with their existing UCSD login. | Adds podcast.ucsd.edu to the "read and change your data on" list |

`<all_urls>` is still not used, and no other host was added.

## How the podcast sync works (for judging D)

- **Opening the site:** the extension opens one background tab on `https://podcast.ucsd.edu/MyCourses.aspx`. A tab that lands on the UCSD login page counts as "not logged in". The extension never sees or types a password.
- **Finding classes:** `/MyCourses.aspx` lists only the student's enrolled classes, but every lecture section of each one. Each class is matched to the student's Canvas course by code (`DSC010_FA26_001` matches `fa26/dsc010_*`) and stored under that Canvas course. A class with no Canvas match is skipped with a note.
- **Reading captions:** captions are read exactly like the Canvas sync. The Kaltura session found on the lecture page is used only inside that page's own context (`readEntryCaptions` in `extension/src/frame.js`). Only caption text, title, duration and the session's length leave the page, and a result containing a session is refused.
- **Before sending:** `postSync` checks the payload for anything shaped like a session.
- **Skipping:** lectures already stored with captions are skipped before any Kaltura call.
- **Code:** `extension/src/podcast.js` (matching), `extension/src/podcastFrame.js` (page readers), `extension/src/syncAll.js` (the run).

## What to verify

```bash
# No secrets or real course content committed
git diff origin/main --stat
git ls-files | grep -E '\.env$|\.srt$|\.vtt$|sync_[0-9]+\.json|real_' || echo "none"

# Payload allowlist: no raw objects spread into the payload. Expect only the two string-array
# copies of instructors and sections, plus the comment that forbids object spreads.
grep -n '\.\.\.' extension/src/payload.js

# Session guard on every frame result and before every POST
grep -n "KS_IN_OUTPUT\|KS_IN_PAYLOAD\|looksLikeSession" extension/src/*.js
```

Tests that cover the security properties:

- `extension/tests/podcast.test.mjs`: no session in any podcast result or payload; a redirect to the UCSD login counts as logged out; malformed lecture paths are refused; stored lectures are skipped before Kaltura is called.
- `extension/tests/hygiene.test.mjs`: network calls go only to hosts in the manifest.
- `backend/tests/test_contract.py`: bad `media_url` values are rejected with 422 and nothing is saved.

## Tested on real data (Allen's account, 2026-11-05)

- **Canvas sync:** MATH 018, 12 lectures, indexed. `/ask` gave cited answers.
- **Sync all my classes:**
  - DSC 010: 70 lectures across sections 001 to 004, including both instructors' sections.
  - MATH 018: 13 lectures.
  - 2 lectures skipped because Kaltura has no captions for them yet.
  - `/ask` on DSC 010 cited section 001 and section 002 lectures, with working `#seconds` links.
- **First run failed partway:** it read 1 lecture, then 3 caption calls failed in a row. The second run had no errors. The likeliest cause is Chrome suspending the background tab. The sync now reloads the tab after an error and moves on to the next class instead of stopping. Error codes are now specific (`TIMEOUT`, `INJECT_FAILED`, `NO_RESULT`, `WRONG_HOST`), so a repeat would show the cause.
- **Not yet observed:** the daily auto-sync firing on its own in real Chrome. It is covered by unit tests only.

## Open question for Igor

All synced classes go into one shared backend. That's fine while each person runs the backend locally. If the backend is ever shared, we need a rule for who can search which course, since podcast.ucsd.edu only shows a student their own enrolled classes.
