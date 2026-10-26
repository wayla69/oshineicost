# Frontend E2E smoke (Playwright)

Walks **every** sidebar tab and asserts each renders content with **zero
console errors**, then checks the API serves real data and the dashboard
renders non-zero figures — the automated version of the manual pre-deploy
smoke.

## Run locally
```bash
cd tests/e2e
npm install
npx playwright install chromium

# hermetic: seed the fixture DB (same dataset as the backend pytest suite)
# and serve the dashboard from it
python seed_db.py .e2e-fixture.db
OSHINEI_NO_SWAP=1 OSHINEI_DB=$PWD/.e2e-fixture.db \
  python -m uvicorn backend.main:app --port 8010 &   # from the repo root

BASE_URL=http://localhost:8010 npx playwright test

# or against the live app (has data, no local DB needed)
BASE_URL=https://oshinei-dashboard.fly.dev npx playwright test
```

## CI
The `e2e` job in `.github/workflows/test.yml` runs on **every pull request**
(enabled 2026-10-07; it was manual-dispatch only from 2026-09-04): it seeds the
fixture DB, starts the dashboard locally, and runs the smoke against it — so a
frontend/API regression (a page-level JS error, a tab that no longer renders)
blocks the merge instead of being found on prod. The Playwright browser is
cached between runs. A manual dispatch (Actions → Run workflow) targets a live deployment
when the `E2E_BASE_URL` repo variable is set.

The backend Python tests (`backend/`) and the recon suite (`recon/tests/`)
also run automatically on every push/PR.

## Tailwind CSS (prebuilt)
This package also pins the Tailwind CLI (`tailwindcss` 3.4.17, the version the old in-browser runtime
`vendor/tailwind.js` was). The app links the prebuilt `vendor/tailwind.build.css`; after adding a Tailwind
class to `index.html`, `tabs/` or `js/`, run `npm run build:css` here and commit the sheet. CI runs
`npm run check:css` in the e2e job and fails when the committed sheet is out of date. Config:
`tailwind.config.js` (repo root); build: `scripts/build-tailwind.js`.
