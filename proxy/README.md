# Answer service (Cloudflare Worker)

The standalone extension stores and searches lectures in the browser. Only the last step, writing
the answer, needs a model, and the OpenRouter key cannot ship inside an extension. This worker holds
the key and does that one step.

It receives the question plus up to 20 short excerpts, builds the same prompt as
`backend/app/answer.py`, calls OpenRouter and returns `{ "answer": "..." }`. It stores nothing and
logs only status codes and counts.

## Run locally

```bash
make proxy-dev   # http://localhost:8787, reads OPENROUTER_API_KEY from ../.env
```

The unpacked extension already points at `http://localhost:8787/v1/answer`.

## Deploy (one time, about 10 minutes)

Needs a free Cloudflare account. The free plan covers 100k requests a day.

```bash
cd proxy
npx wrangler login
npx wrangler secret put OPENROUTER_API_KEY   # paste the key when asked; it is never in git
npx wrangler deploy                          # prints https://lecture-search-answer.<account>.workers.dev
```

Then build the store zip against that address:

```bash
ASK_PROXY_URL=https://lecture-search-answer.<account>.workers.dev make package
```

## Lock it to the extension

`ALLOWED_ORIGINS` in `wrangler.toml` is empty at first, which accepts any origin. After the first
upload to the Chrome Web Store the extension has a fixed id. Set
`ALLOWED_ORIGINS = "chrome-extension://<id>"` (comma-separate several, e.g. the unpacked dev id too)
and deploy again. Requests from anywhere else get 403.

## Limits and cost

- 20 questions per minute per install (`[[ratelimits]]`), keyed by a random install id.
- Requests over 80 KB, more than 20 excerpts, or unknown fields get 422 before any model call.
- gpt-4o-mini with about 12 excerpts is roughly $0.0005 to $0.001 per question. Set a monthly credit
  limit on the OpenRouter key so a bug or abuse cannot run up a bill.

## Endpoints

| Method | Path | Answer |
| --- | --- | --- |
| GET | `/health` | `{"status":"ok"}` |
| POST | `/v1/answer` | `{"answer": "..."}`, or for a study task (`"task": "teach"`, `"flashcards"`, `"quiz"` instead of a question) a lesson, `{"cards": [...]}` or `{"questions": [...]}`; 403 origin, 413 too big, 422 invalid, 429 rate limit, 502 model failed, 503 no key |

Tests: `node --test proxy/tests/*.test.mjs` (no network).
