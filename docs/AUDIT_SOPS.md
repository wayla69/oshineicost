# Oshinei — Operating Procedures for Audit (AUD-9)

One-page SOPs the Big 4 / ITGC reviewer will ask for. Owner reviews and signs
each section annually; update the date when anything changes.

Last reviewed: 2026-06-13

---

## 1. Change management (code → production)
- All code lives in GitHub `wayla69/oshineicost`. Changes are made on a branch,
  reviewed, then merged to `main`.
- **Who can merge to `main`:** the owner / lead engineer only.
- **Who can deploy:** the same person, via `flyctl deploy` (authenticated Fly
  account `kchirakul@gmail.com`). Code deploy and data refresh are SEPARATE
  actions — a committed fix is not live until deployed.
- **Tests gate:** `recon/tests/` and `backend/test_*.py` must pass before deploy.
- **Emergency change:** hotfix branch → deploy → backfill the PR + note the same
  day. No direct edits on the server.

## 2. User access provisioning / de-provisioning
- Auth is enforced in production (`OSHINEI_AUTH=1`). 6 roles × 16 modules
  (`backend/auth.py`). Each person has an individual account.
- **New user:** Management creates the account in Settings → ผู้ใช้งาน with a
  temporary password (policy: ≥ 10 chars, not the shared default). The user is
  forced to change it on first login (`must_change`).
- **Role change / leaver:** Management changes the role or deactivates the
  account in Settings; it takes effect immediately (next request). Never share
  accounts. The last active Management account cannot be removed (guarded).
- **Review cadence (AUD-10):** quarterly — Management exports the user list and
  marks keep/remove.

## 3. Backup & restore (AUD-2)
- One SQLite file (`/data/oshinei.db`) holds all dashboard + recon data.
- **Backup:** `backend/backup_db.py backup` runs daily (Windows Task Scheduler),
  writing a verified copy + manifest (row counts + SHA-256) to a OneDrive folder.
  Retention: ≥ 90 daily + 12 monthly copies. Include `/data/attachments`.
- **Restore test (evidence):** at least quarterly, pull the live DB
  (`flyctl ssh sftp get /data/oshinei.db …`) and run
  `python backend/backup_db.py verify --backup <file>`. Keep the PASS output as
  evidence — auditors ask for the restore test, not just the backup.

## 4. Month-close procedure
- Staff reconcile each branch/month in the recon app; upload documents (bulk
  upload supported), match/exclude, and acknowledge residual variances with a
  written reason (≥ 10 chars, AUD-8).
- **Approval gate:** a month cannot be approved while open discrepancies remain.
- **Segregation of duties (AUD-4):** the preparer cannot approve their own month
  — a different person (Management) approves with the manager token.
- **Working paper:** export the month-pack XLSX (`/api/sessions/{id}/export.xlsx`)
  — Summary now shows variance-absorbed-within-tolerance + an Acknowledged
  variances sheet. Archive the export and keep the original source documents
  (branch/month folder in OneDrive) ≥ 5 years (AUD-3).
- **Audit trail:** every upload/match/exclude/accept is logged in
  `recon_audit_log`, which is append-only (DB triggers, AUD-6).

## 5. Credentials register (AUD-9)
Confirm `.env` is gitignored everywhere. Rotate on staff exit.

| Secret | Where it lives | Who holds it | Rotation |
|---|---|---|---|
| MSSQL read-only creds (Easy POS) | `backend/.env` (gitignored) | owner | on staff exit |
| Write/seed token (`OSHINEI_SEED_TOKEN`) | Fly secret + `backend/.seed_token.txt` (gitignored) | owner | on staff exit |
| Auth signing key (`OSHINEI_AUTH_SECRET`) | Fly secret | owner | only if leaked (rotating logs everyone out) |
| Manager close token (`OSHINEI_MANAGER_TOKEN`) | Fly secret | owner + managers | on manager change |
| Fly.io account | `kchirakul@gmail.com` | owner | 2FA on |

## 6. Monitoring (AUD-11)
- Daily MSSQL sync runs via Windows Task "Oshinei Auto Sync" 06:00 — check it
  succeeded (the dashboard shows the latest data date).
- Login lockout after 5 failed attempts / 5 min (`backend/auth.py`).
- TODO (operational): alert on sync failure + repeated failed logins.
