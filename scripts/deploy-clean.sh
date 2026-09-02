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

# WHICH repo do the worktree's refs come from?
#   A linked worktree resolves refs against its OWNING repo, and that is not
#   necessarily the checkout you ran this from. This machine has TWO checkouts of the
#   same GitHub remote (the OneDrive copy and C:/Users/ASUS/Oshinei) with SEPARATE ref
#   stores; the deploy worktree belongs to exactly one of them. On 2026-09-02 the
#   script fetched the checkout it was invoked from, then reset the worktree to the
#   OWNER's stale origin/main — and still printed a green "deployed <old-sha>". A
#   silent stale ship, caught only by probing the live site.
# So: fetch the OWNER, and refuse to deploy when the two ref stores disagree.
# (Ask git for the path — never grep it: git-bash mixes 'C:/…' and '/c/…' formats.)
if git -C "$DEPLOY_WT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  OWNER_GITDIR="$(git -C "$DEPLOY_WT" rev-parse --path-format=absolute --git-common-dir)"
else
  OWNER_GITDIR="$(git -C "$REPO"      rev-parse --path-format=absolute --git-common-dir)"
fi
owner_git() { git --git-dir="$OWNER_GITDIR" "$@"; }   # a function, not a var: paths have spaces

echo "→ fetching origin in the repo that OWNS the deploy worktree…"
echo "    $OWNER_GITDIR"
ok=0; for i in 1 2 3; do owner_git fetch origin --quiet && { ok=1; break; }; echo "  fetch retry $i…"; sleep 5; done
[ "$ok" = 1 ] || { echo "✗ could not reach origin after retries — aborting (re-run when the network is back)"; exit 1; }
git -C "$REPO" fetch origin --quiet || true    # keep your own checkout current too (no-op when it IS the owner)

# The ref must exist in the OWNER, and must match this checkout's idea of it.
WT_SHA="$(owner_git rev-parse --verify --quiet "$REF" || true)"
[ -n "$WT_SHA" ] || { echo "✗ '$REF' does not exist in $OWNER_GITDIR — aborting"; exit 1; }
MY_SHA="$(git -C "$REPO" rev-parse --verify --quiet "$REF" || true)"
if [ -n "$MY_SHA" ] && [ "$MY_SHA" != "$WT_SHA" ]; then
  echo "✗ ref mismatch — '$REF' is ${MY_SHA:0:9} in this checkout but ${WT_SHA:0:9} in the"
  echo "  repo that owns the deploy worktree. Deploying now would ship the WRONG commit."
  echo "  Fix: git --git-dir=\"$OWNER_GITDIR\" fetch origin    (then re-run)"
  exit 1
fi

# Create the dedicated deploy worktree once (detached HEAD so it never fights a
# branch checked out elsewhere); thereafter just repoint + scrub it.
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
[ "$(git -C "$DEPLOY_WT" rev-parse HEAD)" = "$WT_SHA" ] || {
  echo "✗ worktree HEAD is not '$REF' after the reset — aborting rather than shipping a surprise"; exit 1; }
echo "→ deploying $SHA from a clean tree (no WIP, isolated)…"
( cd "$DEPLOY_WT" && flyctl deploy --remote-only )
echo "✓ deployed $SHA from $DEPLOY_WT"
