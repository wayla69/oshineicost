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

## Quick reference

| Goal | Command |
|------|---------|
| Deploy latest `main` to Fly | `bash scripts/deploy-clean.sh` |
| Deploy a specific commit/tag | `bash scripts/deploy-clean.sh <ref>` |
| Check what's live vs `main` | `git fetch && git log --oneline origin/main -3` |
| Set/rotate a Fly secret | `flyctl secrets set NAME=value` (restarts the app) |
| List Fly secrets (names only) | `flyctl secrets list` |

> Code deploy ≠ data refresh. Deploying ships code only; the live SQLite lives on the Fly
> volume and is untouched. Data syncs run separately (`Auto Sync Local.bat`).
