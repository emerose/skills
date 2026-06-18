"""Shared CLI helpers for the ``sci`` entry point and the store subcommands.

These collapse boilerplate that was duplicated between :mod:`scripts.sci` (the
top-level dispatcher) and :mod:`scientist.store.cli` (the store CLI): JSON
emission, fatal-error exit, and data-tree home resolution. Keeping one
implementation here means the home-resolution precedence — and crucially the
run-from-inside-a-checkout inference — is identical everywhere.

These are deliberately tiny and dependency-free so any CLI layer can import
them without pulling in the heavier store/provenance machinery.
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


def resolve_home(args: argparse.Namespace) -> Path | None:
    """Resolve the data-tree root with consistent precedence:

    ``--home`` → ``$SCIENTIST_HOME`` → the data-repo root inferred by walking up
    from cwd (so commands run from inside a checkout just work).

    Returns ``None`` if none of those resolve — callers that must error on "no
    data folder" rely on this nullable contract; callers wanting a cwd fallback
    should do ``resolve_home(args) or Path.cwd()``.
    """
    import os

    if getattr(args, "home", None):
        return Path(args.home).resolve()
    if os.environ.get("SCIENTIST_HOME"):
        return Path(os.environ["SCIENTIST_HOME"]).resolve()
    from .experiments import _infer_root
    inferred = _infer_root()
    return inferred.resolve() if inferred is not None else None


def emit(result: Any, as_json: bool, render: Callable[[Any], str]) -> None:
    """Print ``result`` as JSON when ``as_json``, else ``render(result)``."""
    if as_json:
        emit_json(result)
    else:
        print(render(result))
