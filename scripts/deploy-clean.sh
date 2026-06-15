#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy-clean.sh — deploy an EXACT committed ref to Fly from an ISOLATED worktree.
#
# WHY THIS EXISTS:
#   `flyctl deploy` ships the *working tree*, not git. The main checkout is shared
#   by several Claude sessions, each with its own uncommitted WIP. Deploying from
#   there risks (a) shipping someone else's half-done WIP, and (b) colliding with an
#   active session (stash dances, conflicts, lost work — all happened on 2026-06-15).
#
#   This script deploys from a DEDICATED worktree pinned to a committed ref and wiped
#   clean, so ONLY that ref ships — no WIP leaks, and no other working tree is touched.
#
# USAGE:
#   bash scripts/deploy-clean.sh [git-ref]      # default ref: origin/main
#   OSHINEI_DEPLOY_WT=/path bash scripts/deploy-clean.sh v1.2   # custom worktree path
#
# PRECONDITION: the code you want live must already be committed AND pushed to the ref
#   (origin/main by default). Run `git push origin main` first.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REF="${1:-origin/main}"
DEPLOY_WT="${OSHINEI_DEPLOY_WT:-$HOME/oshinei_deploy_clean}"
REPO="$(git rev-parse --show-toplevel)"

echo "→ fetching origin (the only network step before deploy)…"
ok=0; for i in 1 2 3; do git -C "$REPO" fetch origin --quiet && { ok=1; break; }; echo "  fetch retry $i…"; sleep 5; done
[ "$ok" = 1 ] || { echo "✗ could not reach origin after retries — aborting (re-run when the network is back)"; exit 1; }

# Create the dedicated deploy worktree once (detached HEAD so it never fights a
# branch checked out elsewhere); thereafter just repoint + scrub it. Worktrees share
# the repo's object store + refs, so no second fetch is needed. (Check by asking git
# directly — never grep paths: git-bash mixes 'C:/…' and '/c/…' formats.)
if git -C "$DEPLOY_WT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "→ reusing deploy worktree: $DEPLOY_WT"
else
  echo "→ creating isolated deploy worktree: $DEPLOY_WT"
  git -C "$REPO" worktree add --detach "$DEPLOY_WT" "$REF"
fi

echo "→ pinning worktree to '$REF' (hard reset + clean)…"
git -C "$DEPLOY_WT" checkout --detach --quiet "$REF"
git -C "$DEPLOY_WT" reset --hard --quiet "$REF"
git -C "$DEPLOY_WT" clean -fdxq            # remove ANY stray/untracked file → pristine

SHA="$(git -C "$DEPLOY_WT" rev-parse --short HEAD)"
echo "→ deploying $SHA from a clean tree (no WIP, isolated)…"
( cd "$DEPLOY_WT" && flyctl deploy --remote-only )
echo "✓ deployed $SHA from $DEPLOY_WT"
