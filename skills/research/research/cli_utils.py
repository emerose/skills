"""Shared CLI helpers for the ``res`` entry point.

Tiny and dependency-free (mirrors scientist's ``cli_utils``): JSON emission, fatal-error exit,
data-tree home resolution, and the JSON/render dispatcher. research operates on the SAME
scientific-data tree as scientist (litreviews live under ``program/litreviews/``, claims under
``…/claims/``), rooted at ``$SCIENTIST_HOME`` — but the home-resolution walk-up is inlined here so
research never imports scientist.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, NoReturn


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def emit_json(obj: Any) -> None:
    import json
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _is_data_root(p: Path) -> bool:
    """A directory is the scientific-data root if it carries the managed store dir
    (``.scientist/``) OR the layout doc + program dir every checkout has. Intrinsic markers, so
    the walk-up never mistakes an ordinary parent for the root. (Same markers scientist uses.)"""
    return (p / ".scientist").is_dir() or (
        (p / "LAYOUT.md").is_file() and (p / "program").is_dir())


def _infer_root(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (default cwd) to the data-repo root by :func:`_is_data_root`.
    A pure fallback that never raises (returns ``None`` if no root is found)."""
    try:
        here = (start or Path.cwd()).resolve()
    except OSError:
        return None
    for cand in (here, *here.parents):
        if _is_data_root(cand):
            return cand
    return None


def resolve_home(args: argparse.Namespace) -> Path | None:
    """Resolve the data-tree root with the same precedence scientist uses:

    ``--home`` → ``$SCIENTIST_HOME`` → the data-repo root inferred by walking up from cwd
    (so commands run from inside a checkout just work).

    Returns ``None`` if none resolve — callers that must error on "no data folder" rely on this
    nullable contract.
    """
    import os

    if getattr(args, "home", None):
        return Path(args.home).resolve()
    if os.environ.get("SCIENTIST_HOME"):
        return Path(os.environ["SCIENTIST_HOME"]).resolve()
    inferred = _infer_root()
    return inferred.resolve() if inferred is not None else None


def emit(result: Any, as_json: bool, render: Callable[[Any], str]) -> None:
    """Print ``result`` as JSON when ``as_json``, else ``render(result)``."""
    if as_json:
        emit_json(result)
    else:
        print(render(result))
