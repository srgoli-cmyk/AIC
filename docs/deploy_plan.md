# Deploy plan: usable by teammates and other students by Nov 16

Nothing here is built yet. Today everything runs on one laptop, with no login. Prices were checked
on Nov 3, 2026, mostly from third-party comparison pages, because several vendors' own pricing pages
didn't show up in search. Confirm each one before paying.

## Recommendation

**One 4 GB VPS running Docker Compose (backend, UI, Postgres, Caddy for HTTPS), Google login limited
to `ucsd.edu`, a hand-kept list of who can use which course, and a capped OpenRouter key.** About
$6-10 a month plus a domain, and 3-4 working days. Have the demo-only option ready first as a
fallback; it takes half a day.

Before any of it, ask the instructor or the university whether storing captions on a rented server
is allowed (see "Decisions needed").

## What we measured

| Item | Value | So |
|---|---|---|
| Backend memory with the model loaded, after 4 questions (macOS) | 682 MB | 512 MB plans can't run it; 1 GB is tight while indexing; plan on 2 GB for the backend |
| UI memory, one open page | 71 MB | Any plan fits, but it grows with each open session |
| Database for one course (24 lectures, 1,457 chunks) | 19 MB | About 25 courses in a 500 MB free database |
| Python environment on disk | 1.5 GB (macOS) | The image is large; a CPU-only torch build should help on Linux |
| Answer length | 269-338 tokens | With about 2,100 input tokens (12 passages of about 636 characters each) |
| One `/ask`, excluding model load | 4-5 s | Fine with no GPU |

Not measured: memory and time while indexing a whole course on a small server, and memory on Linux.
Measure both on the first deploy. Indexing runs inside the backend process, so a large sync slows
answers for everyone while it runs.

## Hosting options

The backend needs an always-on process with about 2 GB. Sleeping plans are a poor fit, because each
wake-up reloads the model.

| Option | Backend + UI | Postgres + pgvector | About per month | Notes |
|---|---|---|---|---|
| **A. One VPS** (Hetzner or similar, 4 GB) | Same box, Compose | Same box, the image we already use | **$6-10** | Cheapest and closest to what we run now. We own updates, backups and the firewall. Hetzner raised prices twice in 2026, and one report says some small plans couldn't be created in the autumn, so check availability and a US location |
| **B. Render** | Standard 2 GB $25, plus Starter $7 for the UI | Basic $6-19, or Neon/Supabase free | **$32-51** | Least server work; HTTPS is included. Confirm that its Postgres offers pgvector |
| **C. Fly.io** | 2 GB about $13, plus about $3 for the UI | Neon or Supabase free | **$16+** | No free tier for new accounts. Volumes and IPs cost extra |
| **D. Free tiers only** | Render Free has 512 MB and sleeps | Neon 0.5 GB, Supabase 500 MB | $0 | **Doesn't fit today:** 682 MB won't run in 512 MB. Free databases also suspend or expire: sources say Render's lasts 30 days |

A hosted database adds a network hop and another company holding captions. It only makes sense with
options B or C.

Replacing torch with an ONNX runtime for the same model could cut memory enough for smaller plans,
but vectors may differ slightly, so everything would need re-indexing. Leave it until after Nov 16.

## Access control

Caption text must not be public, and today every endpoint is open. This is the largest part of the
work and it must ship with the deploy, not after it.

**Login.** Google sign-in on both clients:
- UI: Streamlit 1.66 has `st.login` (OIDC), which needs `authlib` added to the UI's requirements.
- Extension: `chrome.identity.launchWebAuthFlow` gets a Google ID token. It needs the `identity`
  permission and a stable extension ID (a `key` in the manifest), since the redirect URL is built
  from that ID.
- Backend: verifies each token's signature, `aud`, `exp` and `iss`, then checks `email_verified` and
  that `hd` equals exactly `ucsd.edu`. The `hd` request parameter only steers the account picker;
  **the check on the verified token is the real restriction**, and Google's libraries don't do it
  for you.
- Passing the UI's identity on: Streamlit can expose the ID token (`expose_tokens`) for the backend
  to verify like any other. Test this first. The fallback is a shared secret between the UI and the
  backend on the private Compose network, with the email in a header.
- Risk: UCSD may restrict third-party Google apps. **Try one real login on day 1.** If it's blocked,
  use Cloudflare Access's email one-time PIN limited to `@ucsd.edu` for the UI, and a personal token
  pasted into the extension.

**Per-course access. A `ucsd.edu` login alone must not open a course.** A new
`course_members(course_id, email)` table gates `/courses` (only your courses), `/courses/{id}/lectures`,
`/ask` and `/sync`.

Don't grant membership because someone synced a course. The server can't check Canvas enrollment: the
extension won't send Canvas credentials, and a payload can claim any course ID. Anyone logged in could
post a near-empty sync for a course ID and then read all its captions. Today's upsert and
`lectures_skipped_other_course` rules also let a forged course claim real entry IDs, which would make
later real syncs skip them.

For Nov 16:
- The maintainer adds members by hand, with a small script.
- Only listed emails can sync, and only their own courses.
- Later: per-course invite codes.

Citation links go to `canvaskaf.ucsd.edu`, which needs the student's own login, so they expose nothing
extra.

## The extension with a hosted backend

- `manifest.json`: replace `http://localhost:8000/*` in `host_permissions` with
  `https://<our-domain>/*`, and add `identity`. Update `ALLOWED_HOSTS` and `ALLOWED_PERMISSIONS` in
  `scripts/check_manifest.py` in the same change.
- `src/config.js`: `BACKEND_URL`. There is no build step, so keep a separate local dev copy of these
  two values or a short script that switches them.
- Send the ID token on every backend call, and make sure the KS output guard and the no-secrets test
  cover the new header. The Google token goes only to our backend.
- CORS: the worker's requests with `host_permissions` aren't subject to it, so CORS can be tight. Set
  `CORS_ORIGIN_REGEX` to our one extension ID and UI origin. The default allows any extension and
  localhost, which is wrong for production.
- Contract changes, for approval: `401` and `403` on the existing endpoints, `429`, `/courses` showing
  only your courses, and a decision on keeping `/openapi.json` public for the identity check (the
  title is all the extension needs, so a small `/` or `/health` field could replace it).
- Distribution: a zip that people load unpacked. The Chrome Web Store needs a developer account and a
  review of unknown length for these permissions, so don't plan on it by Nov 16.
- Prerequisite: the extension's flow has never run live. It needs a good live test on the local
  backend first.

## OpenRouter budget

Rough cost: about 2,100 input and 340 output tokens per question. At gpt-4o-mini's listed $0.15 and
$0.60 per million, that's about **$0.0005 per question, or 2,000 questions per dollar**. This assumes
that model; check the value in the production settings. A cap is what protects us, not the low price.

1. **Hard cap outside our code:** a dedicated production key with its own credit limit and a weekly
   reset, say $5. OpenRouter enforces it, so it holds even if our code is wrong. Never reuse the dev
   key. Our backend already turns the rejected call into a `502`, but the message should say the
   weekly limit was reached.
2. **Per-user limit:** a counter in Postgres, say 30 questions per user per day, returning `429`.
   Postgres rather than memory, so it survives restarts.
3. **Global daily limit:** say 500 questions, so that many users can't drain the week's cap in one
   evening.
4. Already in place: 1,024 max tokens, a 2,000-character question limit, one attempt with no retries.
5. Also limit syncs per user, and the `/sync` body size in Caddy. Indexing uses the server's CPU, not
   OpenRouter, but it can still crowd out answers.

Passages go to OpenRouter and its upstream provider, so look at OpenRouter's data-collection setting
for providers and choose one that doesn't train on requests.

## Secrets

- A root-only env file on the server (`chmod 600`), read by Compose. Never in the image or the repo.
- New values in production: OpenRouter key, Postgres password, Google client secret, Streamlit cookie
  secret. **The local `aic:aic` database password must not go to a server.**
- Never expose Postgres. **`docker-compose.yml` publishes port 5432, which on a public server would
  open the database to the internet,** and Docker's published ports can bypass `ufw`. Use a separate
  production Compose file with no `ports` on `db`.
- The logging rules stay the same: IDs and numbers only. Add email addresses to what isn't logged,
  apart from a hashed form for rate-limit debugging.

## HTTPS and the server

- Caddy in front gets and renews certificates by itself, given a domain (about $10 a year) pointing
  at the server. It serves the backend and UI on one domain or two subdomains, and it proxies
  Streamlit's WebSocket.
- Only ports 22, 80 and 443 open. SSH keys only.
- Production differences: no `--reload`, no `/docs`, a real `BACKEND_URL` on the private network.
  Streamlit binds inside the Compose network, reachable only through Caddy and only with a login.
  This is the one exception to the localhost-only rule, so it needs your OK.
- Backups: nightly `pg_dump`, stored encrypted off the server. Captions can be re-synced, but
  memberships can't. Decide how long to keep them, and delete them when the project ends.

## Demo-only option

For showing the project without hosting student data. **About half a day, and free.**

- Everything stays on the laptop. You sync; nobody else does, and the extension stays local.
- For a live demo: share your screen. No exposure at all, and no work.
- For teammates to try it: a Cloudflare Tunnel to the Streamlit page behind Cloudflare Access, with an
  email one-time PIN limited to `@ucsd.edu` or a list of names (free up to 50 users; needs a domain on
  Cloudflare). The backend and Postgres are never exposed. It works only while the laptop is awake.
- Still set the capped OpenRouter key. Everyone would see the same courses, so sync only courses all
  your testers are enrolled in.

## Effort

For one person who knows this code. Estimates, not measurements.

| Work | Time |
|---|---|
| Extension live test and fixes (needed either way) | 0.5-1 day |
| Demo-only fallback | 0.5 day |
| Production Compose, Caddy, server, domain, first deploy | 0.5-1 day |
| Google login on backend, UI and extension | 1-1.5 days |
| `course_members`, scoped endpoints, tests that one user can't reach another's course | 0.5-1 day |
| Rate limits, capped key, friendly limit messages | 0.5 day |
| Backups, a run with 2-3 teammates, fixes | 0.5 day |
| **Total** | **4-6 days** (3-4 of them for the deploy itself) |

Suggested order, with Nov 16 as the deadline:

1. Nov 4-5: get the permission answer, try one UCSD Google login, do the extension's live test.
2. Nov 6: demo-only fallback ready.
3. Nov 7-12: server, access control, limits. **The server gets no real captions until access control
   works.**
4. Nov 13-15: teammates try it; fix what they find.

## Decisions needed

1. **Is this allowed?** Lecture captions are the instructor's material and can include students'
   voices. A private local tool is one thing; a server used by other students is another. Ask the
   instructor, and possibly UCSD, before uploading real captions. If the answer is no or slow, the
   demo-only option is what's left.
2. Who's in the first group, and who adds members? I'd keep it to teammates plus a few students.
3. Who pays, and which account owns the domain, the server and the OpenRouter key? It shouldn't rest
   on one student's card with no cap.

## Sources

- [Render pricing overview](https://livemy.app/blog/render-pricing), [Render plans](https://costbench.com/software/developer-tools/render/)
- [Fly.io pricing docs](https://fly.io/docs/about/pricing/), [Fly.io cost breakdown](https://servercompass.app/blog/flyio-pricing-breakdown-vs-vps)
- [Hetzner 2026 price changes](https://agentdeals.dev/hetzner-pricing-2026), [June 2026 increase](https://webhosting.today/2026/06/18/hetzners-price-increases-reached-209-the-30-headline-applied-to-a-different-tier/)
- [Neon and Supabase comparison](https://www.bytebase.com/blog/neon-vs-supabase.md), [vector database pricing](https://agentdeals.dev/vector-database-pricing)
- [OpenRouter limits](https://openrouter.ai/docs/api-reference/limits), [OpenRouter spend controls](https://openrouter.ai/blog/tutorials/team-spend-controls-setup/)
- [gpt-4o-mini price on OpenRouter (third party)](https://www.llmreference.com/provider/openrouter/gpt-4o-mini)
- [Verifying Google ID tokens](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token), [chrome.identity](https://developer.chrome.com/docs/extensions/reference/api/identity)
- [Streamlit authentication](https://docs.streamlit.io/develop/concepts/connections/authentication), [st.login](https://docs.streamlit.io/develop/api-reference/user/st.login)
- [Cloudflare free plan (50 users)](https://blog.cloudflare.com/teams-plans), [Cloudflare plans](https://www.cloudflare.com/plans/sase-zero-trust.md)
