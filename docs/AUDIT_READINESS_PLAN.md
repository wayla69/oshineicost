# Audit Readiness Plan (Big 4 + IT Audit)

Drafted 2026-06-12. Status updated 2026-06-13 after the first implementation pass.
Goal: pass a Big 4 financial audit + ITGC review with no significant findings,
for the Oshinei recon app + dashboard (Fly.io, SQLite on /data volume).

Status legend: ☐ not started · ◐ in progress · ☑ done

---

## Phase 1 — Critical

### AUD-1 ◐ Authentication hardening
- ☑ Forced password change on first login (`must_change`, seeded + admin-created).
- ☑ Login lockout (5 fails / 5 min) and 12h session expiry — already in `auth.py`.
- ☑ Fail-closed signing secret (no dev key when `OSHINEI_AUTH=1`).
- ☑ **Password policy: ≥ 10 chars + not the shared default**, enforced on create /
  admin-reset / self-service change (`auth.check_password_policy`, this pass).
- ☑ Persistent login-event log (success / fail / logout / password change) →
  recon_audit_log (see AUD-6).
- ☐ Operational: set `OSHINEI_AUTH=1` permanently in Fly secrets; give each
  person their own account.

### AUD-2 ◐ Backup + restore evidence
- ☑ Script delivered: `backend/db_backup.py` (verified copy + manifest;
  `verify` = restore test with PASS/FAIL). See SOP §3.
- ☐ Operational: schedule daily to OneDrive, retention ≥ 90 daily + 12 monthly,
  run one `verify` and keep the evidence. Include `/data/attachments`.

### AUD-3 ☐ Original document retention (operational, no code)
- Keep "All recon data" branch/month in OneDrive ≥ 5 years. The export's SHA-256
  ties each archived file to the ingested data.

## Phase 2 — High

### AUD-4 ☑ Segregation of duties
- Preparer ≠ approver enforced on session approve (`created_by` ≠ `approved_by`),
  with a test. Has teeth because the session captures `created_by`.

### AUD-5 ☑ Immutable close snapshot
- On approve/close the working-paper XLSX is frozen to the volume + a
  `recon_close_snapshot` row (SHA-256). List/download endpoints added. Best-effort
  so a snapshot hiccup never blocks the close. Tested + deployed.

### AUD-6 ◐ Audit log integrity
- ☑ `recon_audit_log` is **append-only** (BEFORE UPDATE/DELETE triggers RAISE
  ABORT), with a test.
- ☑ Persistent login-event logging — login / login_fail / logout / password_change
  written to recon_audit_log (`backend/main.py _log_auth_event`), tested.
- ☐ Operational: weekly off-box export (rides AUD-2). Optional: hash-chain.

## Phase 3 — Medium

### AUD-7 ☑ Tolerance disclosure
- Working-paper Summary now shows "Variance absorbed within tolerance" (Σ|var|
  over matched lanes). Owner still signs off the thresholds once in writing.

### AUD-8 ☑ Acknowledged-variance governance
- รับทราบ requires a meaningful reason (≥ 10 chars). New "Acknowledged variances"
  sheet in the export lists each accept (category/bank/reason/by/at) for sign-off.

### AUD-9 ☑ Documentation pack
- `docs/AUDIT_SOPS.md`: change management, access provisioning/de-provisioning,
  backup/restore, month-close, credentials register, monitoring. Owner reviews+signs.

## Phase 4 — Nice to have

### AUD-10 ◐ Access review cadence
- Mechanism exists (Management can export the user list); SOP §2 sets quarterly
  cadence. ☐ Operational: actually run it and keep evidence.

### AUD-11 ☐ Log retention & monitoring
- ☐ Alert on sync failure + repeated failed logins (operational). Lockout already
  in place.

---

## What shipped this pass (code, tested, deploy with the recon app)
AUD-1 password policy · AUD-4 SoD · AUD-6 append-only audit log · AUD-7 tolerance
disclosure · AUD-8 accept-reason + listing — plus AUD-2 backup script & AUD-9 SOPs.

## What's left
- **Code: none — all AUD code items are now done** (AUD-1,4,5,6,7,8 shipped +
  AUD-5 close snapshot + login-event logging in this pass). Optional code extras:
  audit-log hash-chain (AUD-6), close-snapshot UI button.
- Operational (owner): turn AUD-2 schedule on + one restore test; AUD-3 retention;
  set `OSHINEI_AUTH=1` in Fly; quarterly access review (AUD-10); monitoring (AUD-11).
