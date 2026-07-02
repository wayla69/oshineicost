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
The `e2e` job in `.github/workflows/test.yml` runs on **every push/PR**: it
seeds the fixture DB, starts the dashboard locally, and runs the smoke against
it — so a frontend/API regression blocks the merge instead of being found on
prod. A manual dispatch (Actions → Run workflow) targets a live deployment
when the `E2E_BASE_URL` repo variable is set.

The backend Python tests (`backend/`) and the recon suite (`recon/tests/`)
also run automatically on every push/PR.
