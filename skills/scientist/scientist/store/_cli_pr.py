"""The ``pr`` command handler: package working-tree changes into a branch + PR.

A thin CLI wrapper over the pure-git :mod:`_pr` plumbing. ``pr`` never opens the
libkit store (it runs against a :class:`_HomeOnly` stand-in), so it needs no
embedding backend.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..cli_utils import die, emit_json
from . import _pr


async def cmd_pr(store, args: argparse.Namespace) -> None:
    """Package working-tree changes into a branch + pull request for review."""
    await _maybe_pr(store, args.paths or None, args.title,
                    args.body or args.title, args, dry_run=args.dry_run)


async def _maybe_pr(store, paths, title: str, body: str,
                    args: argparse.Namespace, *, dry_run: bool = False) -> None:
    try:
        result = _pr.create_pr(store.home, title=title, body=body,
                               paths=paths or _changed_paths(store.home),
                               dry_run=dry_run)
    except _pr.GitError as e:
        die(str(e))
    if args.json:
        emit_json(result)
    elif result.get("pr_url"):
        print(f"opened PR: {result['pr_url']}")
    elif result.get("dry_run"):
        print("dry-run — would run:\n  " + "\n  ".join(result["steps"]))
    else:
        print(f"committed to branch {result['branch']} (not pushed)")


def _changed_paths(home: Path) -> list[str]:
    import subprocess
    # -z gives NUL-separated, UNquoted paths (paths here contain spaces), so we can
    # hand them straight to `git add --` without quoting/escaping surprises.
    out = subprocess.run(["git", "-C", str(home), "status", "--porcelain", "-z"],
                         capture_output=True, text=True).stdout
    return [entry[3:] for entry in out.split("\0") if entry.strip()]
