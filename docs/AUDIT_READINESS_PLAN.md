# Audit Readiness Plan (Big 4 + IT Audit)

Drafted 2026-06-12. Goal: pass a Big 4 financial audit and the accompanying
ITGC (IT general controls) review with no significant findings, for the
Oshinei recon app + dashboard (Fly.io, SQLite on /data volume).

Status legend: ☐ not started · ◐ in progress · ☑ done

---

## Phase 1 — Critical (do first; these are automatic findings if absent)

### AUD-1 ☐ Authentication hardening
- `OSHINEI_AUTH=1` permanently on in production (Fly secrets), never open.
- Individual passwords per user; kill the shared seed `Oshinei@2026`:
  force password change on first login.
- Password policy (length ≥ 10), lockout after N failed attempts,
  session expiry (e.g. 12h), log success/failed logins to `recon_audit_log`.
- Files: `backend/auth.py`, recon login flow.

### AUD-2 ☐ Backup + restore evidence
- Nightly off-site copy of `/data/oshinei.db` (OneDrive or S3), retention ≥ 90 days
  plus 12 monthly archives.
- Run ONE documented restore test (download backup, open, row-count checks) and
  keep the evidence note — auditors ask for the restore test, not the backup.
- Attachments dir (`/data/attachments`) included in backup.

### AUD-3 ☐ Original document retention (no code)
- Keep "All recon data" organized branch/month in OneDrive ≥ 5 years
  (Thai Revenue Code; some firms ask 7).
- The SHA-256 column in the working-paper export ties each archived file to the
  ingested data — only useful while the original file exists.

## Phase 2 — High (small code changes, classic findings)

### AUD-4 ☐ Segregation of duties
- Enforce preparer ≠ approver on month close (`recon_sessions`): the user who
  created/ran the session cannot approve it. One check in the approve endpoint.

### AUD-5 ☐ Immutable close snapshot
- On ปิดเดือน: auto-generate the working-paper xlsx and archive it
  (store on volume + row in a small `recon_close_snapshot` table with hash).
- "What management signed" stays frozen even if data is touched later.

### AUD-6 ☐ Audit log integrity
- Log login/logout/failed-login events (part of AUD-1).
- Weekly export of `recon_audit_log` off-box (rides the AUD-2 backup).
- Optional later: hash-chain entries for tamper evidence.

## Phase 3 — Medium (governance + disclosure)

### AUD-7 ☐ Tolerance disclosure
- Working-paper Summary: add "variance absorbed within tolerance this month"
  (sum of |variance| over matched lanes). Owner signs off the thresholds
  (฿100 lane, ฿10 cash slip) once, in writing (note in `recon_method_config.notes`).

### AUD-8 ☐ Acknowledged-variance governance
- Minimum meaningful reason on รับทราบ (length/quality rule).
- Monthly listing of all accepts across branches for owner sign-off
  (can be a sheet in the export or a simple endpoint).

### AUD-9 ☐ Documentation pack (no code)
- One-page SOPs: change management (Git/PR/merge/deploy rights, emergency path),
  user access provisioning/de-provisioning, backup/restore, month-close procedure.
- Credentials register: MSSQL read-only creds, write token, cookie signing key —
  where stored, who holds, rotation on staff exit. Confirm `.env` gitignored.

## Phase 4 — Nice to have

### AUD-10 ☐ Access review cadence
- Quarterly user-access review (Management exports user list, marks keep/remove).

### AUD-11 ☐ Log retention & monitoring
- Alert on sync failure ("Oshinei Auto Sync" task) and on repeated failed logins.

---

Order of execution: AUD-1 → AUD-2 → AUD-3 (same week), then AUD-4/5/6 as one
branch, then Phase 3. Most items are ≤ half a day each; AUD-1 is ~1 day.
