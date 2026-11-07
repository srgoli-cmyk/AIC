# Chrome Web Store listing (draft)

Copy these into the developer dashboard. Edit freely; keep the data answers in sync with
`extension/privacy.html`.

## Store listing

**Name:** Lecture Search for UCSD

**Summary (132 chars max):**
Search your UCSD lecture recordings and ask questions. Answers cite the lecture and jump to the exact moment.

**Category:** Education (Tools as a second choice)

**Language:** English

**Description:**

Missed what the professor said about eigenvalues in week 3? Lecture Search finds it.

Open a Canvas course and press Sync. The extension reads the lecture recordings you already have
access to, from the course's media gallery and from podcast.ucsd.edu (all lecture sections, not
just yours). Then ask a question in plain English. You get a short answer with citations like
[Lecture 8, 14:02], and each citation opens the recording at that moment.

- One click syncs every lecture of a course. Daily sync is optional.
- Search runs on your computer. Your lectures are stored in this browser only.
- Math in answers is rendered properly.
- Remove a course, or everything, at any time.

Made by UCSD students. Not affiliated with or endorsed by UC San Diego.

**Screenshots (1280x800 or 640x400, 1 to 5):**
1. A course page with an answer and its citations.
2. The popup during a sync.
3. A citation opening the podcast at the cited time.
4. The course list.

Use a demo or test course. No real student names, emails or IDs in screenshots.

**Small promo tile (440x280):** icon plus "Lecture Search" on the dark background.

## Privacy tab

**Single purpose:**
Let a UCSD student search and ask questions about the lecture recordings of their own Canvas
courses.

**Permission justifications:**

| Permission | Why |
| --- | --- |
| `scripting` | Runs the sync code in the Canvas, Kaltura and podcast pages the student opened, to read the lecture list and captions with the student's own session. |
| `storage` | Saves settings, sync status and the per-install id. |
| `unlimitedStorage` | Captions and search vectors for a full course can pass the default 10 MB limit. They stay in IndexedDB on the device. |
| `alarms` | The optional daily sync. |
| `idle` | Daily sync only runs while someone is using the computer. |
| `offscreen` | The on-device search model (WebAssembly) runs in an offscreen document, because service workers cannot run it. |
| Host `canvas.ucsd.edu` | Course name, code, term and lecture list. |
| Host `canvaskaf.ucsd.edu` | The course media gallery (Kaltura). |
| Host `podcast.ucsd.edu` | Lecture podcasts for every section of the course. |
| Host `cdnapisec.kaltura.com` | Caption files for the recordings. |
| Host (answer service) | Sends the question and matching excerpts to get a written answer. |

**Remote code:** No. All code and the model ship in the package. WebAssembly is used for the
on-device model (`wasm-unsafe-eval`), loaded from the package only.

**Data usage (check these boxes):**
- Website content: yes (lecture titles and captions, read from UCSD sites).
- Personally identifiable information, health, financial, authentication, personal communications,
  location, web history, user activity: no.

**Certify:**
- Not sold to third parties.
- Not used or transferred for purposes unrelated to the single purpose.
- Not used or transferred to determine creditworthiness or for lending.

**Privacy policy URL:** a public copy of `extension/privacy.html` (see CHECKLIST.md).

## Distribution

Start as **Unlisted** (only people with the link can install), for the team and a few testers.
Switch to Public after feedback and after UCSD Educational Technology Services replies.
