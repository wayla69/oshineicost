# Deploy & multi-session workflow

The dashboard + recon app share **one Fly app** (`oshinei-dashboard`) and **one `main`
branch**, and several Claude sessions work the repo at once. `flyctl deploy` ships the
**working tree** (not git), so deploying from the shared main checkout has bitten us:
shipped another session's half-done WIP, stash conflicts, a pruned worktree, and a
near-lost merge (all on 2026-06-15). These rules stop that.

## The one rule for deploying

**Never run `flyctl deploy` from the main working directory.** Always:

```bash
git push origin main                 # 1. make sure your code is committed + pushed
bash scripts/deploy-clean.sh         # 2. deploy origin/main from a clean isolated worktree
```

`scripts/deploy-clean.sh` (Windows: double-click **`Deploy to Fly (clean).bat`**) creates
a dedicated worktree at `~/oshinei_deploy_clean`, hard-resets + scrubs it to the exact ref
(default `origin/main`), and deploys from there. So **only committed, pushed code ships** —
no WIP leaks, and no other session's working tree is touched. Deploy a different ref with
`bash scripts/deploy-clean.sh <ref>`.

## Working alongside other sessions

1. **Each task works on its own branch.** The Claude Code harness already puts each
   session in its own git worktree/branch — keep it that way; don't hand-checkout `main`
   in a shared dir to do edits.
2. **Land via merge into `main`, then push.** Commit on your branch → merge to `main`
   (fast-forward if you can) → `git push origin main`.
3. **Don't stash/pop another session's WIP** to deploy. The clean-deploy script removes
   the reason anyone ever did that.
4. **Before you merge/deploy, `git fetch` first.** `main` may have moved (another session
   merged). Rebase/merge on top of the latest to avoid clobbering.
5. **Leave untracked files alone.** Backups, `.tmp_*`, data dirs, and other sessions'
   scratch files are not yours to commit or delete.

## Always confirm the deploy with a live probe

`deploy-clean.sh` prints `✓ deployed <sha>` — that is the sha it *pinned*, not proof of what
the app is serving. On 2026-09-02 it reported success while shipping the previous commit
(see below). After every deploy, grep the live site for a marker only your change contains:

```bash
curl -s https://oshinei-dashboard.fly.dev/ | grep -c '<marker-from-your-change>'
```

### Why a deploy can ship the wrong commit

This machine has **two checkouts of the same GitHub remote** — the OneDrive working copy and
`C:/Users/ASUS/Oshinei` (the migration target) — with **separate ref stores**. The deploy
worktree `~/oshinei_deploy_clean` is linked to exactly one of them, and refs resolve against
that **owner**, whichever checkout you launched the script from. The script used to fetch the
checkout it ran in, so `origin/main` in the owner could stay stale and the worktree got pinned
to an old commit — silently, with a green success line.

The script now fetches the **owner** (`git rev-parse --git-common-dir` on the worktree) and
**aborts** when the two ref stores disagree about the ref being deployed. If it ever does
abort, the fix it prints is the whole fix:

```bash
git --git-dir="C:/Users/ASUS/Oshinei/.git" fetch origin
```

### If flyctl says "no access token available"

That does **not** always mean the login expired — flyctl sometimes fails to read its own
config. The token in `~/.fly/config.yml` is usually still valid; pass it explicitly:

```bash
export FLY_API_TOKEN="$(sed -n 's/^access_token:[[:space:]]*//p' ~/.fly/config.yml)"
```

Only run `flyctl auth login` (interactive, needs a browser) if that still fails.

## Before you merge: the gate runs here, not on GitHub

GitHub Actions is **disabled** in the repo settings (since 2026-08-10) — the workflows in
`.github/workflows/` are correct but nothing runs them. Until it is switched back on, the
machine that merges is the only thing that runs the suites:

```bash
bash scripts/gate.sh            # pyflakes + pytest for backend AND recon + tsc --checkJs for js/; last line is the verdict
```

It gates on **exit codes**. `pytest -q | tail -1` shows the progress bar, not "N passed",
and a red suite was merged that way once (#108/#109). If Actions is ever re-enabled:
`tests` runs the same gate on every PR; the Playwright `e2e` job and `deploy` are manual
dispatch only (`deploy-clean.sh` stays the one deploy path).

## After a merge that touches the sync

The nightly sync does **not** run from this checkout. It runs from the scheduler clone
`C:\Users\ASUS\Oshinei` (Task Scheduler: *Oshinei Auto Sync* 06:00 local full run, *Oshinei
DB Backup* 07:30, *Oshinei Feedback Sync* 09:00 review fetch → live app, *Oshinei
Sync Live (Fly)* 10:00 incremental + push). That clone ran #90's code for a week while `main`
moved 22 commits, and the first real run of the newer code failed (#115). So:

```bash
git -C /c/Users/ASUS/Oshinei pull --ff-only origin main
```

after every merge that changes `backend/sync_mssql.py`, `backend/refresh_fly_db.py`,
`backend/month_summary.py` or the `.bat` files — then read the next run's log
(`backend/logs/sync_live_fly.log` there, UTF-16). `reconcile()` refuses a build whose staged
tables start later than the current DB's (history loss, #113), on top of the revenue gates.

## Quick reference

| Goal | Command |
|------|---------|
| Gate a branch before merging | `bash scripts/gate.sh` |
| Update the scheduler clone after a sync change | `git -C /c/Users/ASUS/Oshinei pull --ff-only origin main` |
| Deploy latest `main` to Fly | `bash scripts/deploy-clean.sh` |
| Deploy a specific commit/tag | `bash scripts/deploy-clean.sh <ref>` |
| Check what's live vs `main` | `git fetch && git log --oneline origin/main -3` |
| Prove what is actually live | `curl -s https://oshinei-dashboard.fly.dev/ \| grep -c '<marker>'` |
| Set/rotate a Fly secret | `flyctl secrets set NAME=value` (restarts the app) |
| List Fly secrets (names only) | `flyctl secrets list` |

> Code deploy ≠ data refresh. Deploying ships code only; the live SQLite lives on the Fly
> volume and is untouched. Data syncs run separately (`Auto Sync Local.bat`).

## Commit scopes

One scope per area, always the same word. The Management Report alone has been
committed as `mgt`, `mgt-report` and `mgmt-report`, which makes `git log --grep`
and the fix-rate audit lie.

| Area | Scope |
|------|-------|
| Management Report (daily/weekly/monthly) | `mgt` |
| Cost / BoM / sku_cost / effective cost | `cost` |
| Live MSSQL sync, staging, hot-swap | `sync` |
| Recon (reconciliation app) | `recon` |
| Ingredient order plan / order setup | `ordering` |
| Dashboard tabs & charts (frontend) | `ui` |
| Auth, roles, tokens | `auth` |
| Reports other than the Management Report | `reports` |
