#!/usr/bin/env bash
# new-unit.sh — provision an isolated, off-Drive git worktree for one analyst fan-out unit.
#
# WHY (read this once): the scientific-data repo (emerose/kicho-science) is checked out on a
# Google-Drive mount that GitSync keeps fast-forwarded to origin/main. Working there directly
# is a trap. Every fan-out session shares ONE working tree / index / HEAD, so concurrent units
# race: HEAD wanders between branches mid-task and a plain `git commit` sweeps a sibling's
# staged files. Pushing from the Drive also hits "mmap timed out" reading packs off the mount.
# raw/ IS tracked in git, so a unit needs nothing from the Drive — work in an isolated worktree
# off a LOCAL clone and push straight to GitHub. Leave the Drive checkout to GitSync + humans.
#
# Usage:
#   new-unit.sh <exp-id> [branch]     # provision; e.g.  new-unit.sh k1-221101
#   new-unit.sh --remove <exp-id>     # tear the worktree down once the PR is merged
#
# Typical call (sets EXPERIMENTS_ROOT in your shell):
#   eval "$(skills/analyst/scripts/new-unit.sh k1-221101)"
#
# Env overrides:
#   KICHO_REPO    repo URL          default https://github.com/emerose/kicho-science.git
#   KICHO_BASE    local base clone  default ~/.cache/kicho-science      (cloned once, ~1GB)
#   KICHO_WT_DIR  worktrees root    default ~/kicho-worktrees           (one per unit)
#
# It prints diagnostics to stderr and exactly one line to stdout — the export to eval.
set -euo pipefail

REPO="${KICHO_REPO:-https://github.com/emerose/kicho-science.git}"
BASE="${KICHO_BASE:-$HOME/.cache/kicho-science}"
WT_DIR="${KICHO_WT_DIR:-$HOME/kicho-worktrees}"

die() { echo "new-unit: $*" >&2; exit 1; }
norm() { echo "$1" | tr 'A-Z_' 'a-z-'; }   # k1_221101 / K1-221101 -> k1-221101 (path-safe)

[ $# -ge 1 ] || die "usage: new-unit.sh <exp-id> [branch] | --remove <exp-id>"

# --- teardown ---------------------------------------------------------------
if [ "$1" = "--remove" ]; then
  [ $# -eq 2 ] || die "usage: new-unit.sh --remove <exp-id>"
  id="$(norm "$2")"; wt="$WT_DIR/$id"
  git -C "$BASE" worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt"
  git -C "$BASE" worktree prune 2>/dev/null || true
  echo "new-unit: removed worktree $wt" >&2
  echo "          (branch analyst/$id kept; delete with: git -C \"$BASE\" branch -D analyst/$id)" >&2
  exit 0
fi

id="$(norm "$1")"
branch="${2:-analyst/$id}"
wt="$WT_DIR/$id"

# 1. local base clone (once) — cloning from GitHub sidesteps the Drive .git entirely.
if [ ! -d "$BASE/.git" ]; then
  echo "new-unit: cloning $REPO -> $BASE (one-time, ~1GB) ..." >&2
  git clone --quiet "$REPO" "$BASE"
fi

# 2. refresh origin/main so the unit bases on the latest merged state.
git -C "$BASE" fetch --quiet origin

# 3. validate the experiment id against origin/main BEFORE creating a worktree (so a typo
#    never leaves an orphaned worktree behind).
code="$(echo "$id" | tr 'a-z' 'A-Z')"          # k1-221101 -> K1-221101
git -C "$BASE" ls-tree --name-only origin/main | grep -q "^$code " \
  || die "no '$code *' folder on origin/main — check the exp id"

# 4. create the worktree (reuse if it already exists).
if [ -d "$wt" ]; then
  echo "new-unit: reusing existing worktree $wt" >&2
else
  mkdir -p "$WT_DIR"
  if git -C "$BASE" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$BASE" worktree add --quiet "$wt" "$branch"
  else
    git -C "$BASE" worktree add --quiet -b "$branch" "$wt" origin/main
  fi
fi

shopt -s nullglob
matches=( "$wt/$code "* )                       # for the basename display below

cat >&2 <<EOF
new-unit: worktree ready
  branch:      $branch
  worktree:    $wt
  experiment:  $(basename "${matches[0]}")
  push:        git -C "$wt" push origin $branch     # straight to GitHub, no Drive
  teardown:    $(basename "$0") --remove $id        # after the PR merges
EOF

# the one line to eval — sets EXPERIMENTS_ROOT to the worktree root (where the K1-* folders live)
echo "export EXPERIMENTS_ROOT=\"$wt\""
