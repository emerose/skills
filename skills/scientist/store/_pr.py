"""Git/GitHub plumbing: package scientist's generated changes into a branch and
open a pull request for the user to review and merge.

scientist never writes content silently to the main branch — `readme`/`summary`
regenerate files in the working tree, and this module wraps them into a reviewable
PR (branch -> commit -> push -> `gh pr create`). The data folder is the git repo;
the private remote is configured separately (`gh repo create`).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


class GitError(RuntimeError):
    pass


def _git(home: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(home), *args],
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def slug_branch(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-")
    return f"scientist/{s or 'update'}"


def current_branch(home: Path) -> str:
    # symbolic-ref works even on an unborn branch (no commits yet); fall back to main.
    return _git(home, "symbolic-ref", "--short", "HEAD", check=False) or "main"


def has_changes(home: Path, paths: list[str] | None) -> bool:
    args = ["status", "--porcelain", *(["--", *paths] if paths else [])]
    return bool(_git(home, *args))


def create_pr(home: Path, *, title: str, body: str, paths: list[str],
              base: str | None = None, branch: str | None = None,
              push: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """Branch from the current HEAD, commit ``paths``, push, and open a PR.

    With ``push=False`` (or ``dry_run``) the network steps are skipped — used by
    tests and for a local preview. Returns a dict with the branch, the committed
    paths, and (when pushed) the PR URL.
    """
    base = base or current_branch(home)
    branch = branch or slug_branch(title)
    plan = {
        "branch": branch, "base": base, "paths": paths,
        "steps": [f"git checkout -b {branch}",
                  f"git add {' '.join(paths)}",
                  "git commit", *(["git push -u origin", "gh pr create"] if push else [])],
    }
    if dry_run:
        plan["dry_run"] = True
        return plan
    if not has_changes(home, paths):
        raise GitError("no changes to commit for the given paths")

    _git(home, "checkout", "-b", branch)
    _git(home, "add", "--", *paths)
    msg = f"{title}\n\n{body}\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
    _git(home, "commit", "-m", msg)
    plan["committed"] = True
    if not push:
        return plan

    _git(home, "push", "-u", "origin", branch)
    proc = subprocess.run(
        ["gh", "pr", "create", "--title", title, "--body", body, "--base", base, "--head", branch],
        cwd=str(home), capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"gh pr create: {proc.stderr.strip() or proc.stdout.strip()}")
    plan["pr_url"] = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else None
    return plan
