# Publishing checklist

Nothing here is done yet. Work top to bottom.

## Before (team)

- [ ] Team approves shipping as a standalone extension (this branch, `chrome-web-store`).
- [ ] Igor approves the manifest changes: new permissions `unlimitedStorage`, `offscreen`, CSP with
      `wasm-unsafe-eval`, answer-service host; `tabs` removed. Gate: `scripts/check_manifest.py`.
- [ ] Reply from UCSD ETS (podcast@ucsd.edu) about reading podcast captions for a student's own
      use. Keep the listing Unlisted until then.
- [ ] Pick a contact email for the listing and put it in `extension/privacy.html` (`#contact`).

## Build

- [ ] `make vendor` (model, runtime, KaTeX, fonts; checksums verified).
- [ ] `make check` passes.
- [ ] Deploy the answer service (see `proxy/README.md`), set an OpenRouter credit limit.
- [ ] `ASK_PROXY_URL=https://... make package` gives `dist/lecture-search-<version>.zip`.

## Test the zip in real Chrome

- [ ] Unzip `dist/build`, load it at `chrome://extensions` (Developer mode, Load unpacked).
- [ ] Sync one course from Canvas. The popup shows progress, then "Indexing x/y" on the course page.
- [ ] Ask a question. Citations open the recording at the right time.
- [ ] Remove the course. It disappears and the search finds nothing.
- [ ] Restart Chrome mid-sync. The next open shows the interrupted sync and a retry works.

## Developer account

- [ ] Register at https://chrome.google.com/webstore/devconsole ($5 one time). Use a shared team
      Google account so the listing does not depend on one person.
- [ ] Turn on 2-step verification (required to publish).

## Submit

- [ ] Upload the zip. Note the extension id it gets.
- [ ] Set `ALLOWED_ORIGINS = "chrome-extension://<id>"` in `proxy/wrangler.toml`, `npx wrangler
      deploy`.
- [ ] Publish `extension/privacy.html` at a public URL (GitHub Pages on a public repo, or a public
      Google Doc with the same text) and paste the URL in the Privacy tab.
- [ ] Fill the listing and Privacy tab from `store/LISTING.md`. Upload screenshots and the 128 px
      icon (`extension/icons/icon128.png`).
- [ ] Visibility: Unlisted. Submit for review.

Review usually takes 1 to 3 days, longer for new accounts and broad host permissions. Common
rejections: a permission without a clear reason, a vague single purpose, or a privacy policy that
does not match the data form.

## Each update

- [ ] Bump `version` in `extension/manifest.json`.
- [ ] `make check`, `make package`, upload, submit.
