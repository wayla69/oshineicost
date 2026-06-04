# Frontend E2E smoke (Playwright)

Walks **every** sidebar tab and asserts each renders content with **zero
console errors** — the automated version of the manual pre-deploy smoke.

## Run locally
```bash
cd tests/e2e
npm install
npx playwright install chromium

# against your local backend (python "Invisible ERP/..." or the preview on :8010)
BASE_URL=http://localhost:8010 npx playwright test

# or against the live app (has data, no local DB needed)
BASE_URL=https://oshinei-dashboard.fly.dev npx playwright test
```

## CI
The `e2e` job in `.github/workflows/test.yml` is **manual** (Actions → Run
workflow) because it needs a populated DB. It defaults to the live URL; override
with the `E2E_BASE_URL` repo variable.

The backend Python tests (`backend/`) run automatically on every push/PR.
