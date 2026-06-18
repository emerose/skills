"""scientist.grounding.bypass_guard — make untracked source reads visible.

While a capture is active we wrap ``pandas.read_csv`` and ``builtins.open`` so that a
*direct* read of a tracked source file (one not routed through ``load``/``experiments``) is
still captured and flagged. This guarantees the captured input set is complete: a
claim can't quietly read a CSV behind the harness's back. We capture-and-flag rather
than hard-fail, so the grounding report still renders — the reconcile lint surfaces
the bypass. Reads outside SCIENTIST_HOME (pytest internals, the report file, temp files)
are ignored so the guard never interferes with the test runner itself.
"""
from __future__ import annotations

import builtins
import os
from pathlib import Path

from ._text import _sha256

_guard_installed = False
_orig_open = builtins.open
_orig_read_csv = None


def _data_root() -> Path | None:
    r = os.environ.get("SCIENTIST_HOME")
    return Path(r).resolve() if r else None


def _under_root(p: Path) -> bool:
    root = _data_root()
    if root is None:
        return False
    try:
        p.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _maybe_flag(path, via: str) -> None:
    from . import current_capture, TRACKED_SUFFIXES

    cap = current_capture()
    if cap is None:
        return
    try:
        p = Path(path)
    except TypeError:
        return
    if p.suffix.lower() not in TRACKED_SUFFIXES or not _under_root(p):
        return
    if not p.is_file():
        return
    sha = _sha256(p.read_bytes())
    # If load() already recorded this exact path, it's tracked — nothing to flag.
    if any(inp["path"] == str(p) for inp in cap.inputs):
        return
    cap.record("bypass", p, sha, via=f"bypass:{via}")
    cap.bypassed.append(f"{via}: {p}")


def install_guard() -> None:
    """Patch pandas.read_csv + builtins.open to flag untracked tracked-file reads.
    Idempotent; installed by the plugin for the whole session (no-op when no capture
    is active, so it is safe to leave installed)."""
    global _guard_installed, _orig_read_csv
    if _guard_installed:
        return
    import pandas as pd

    _orig_read_csv = pd.read_csv

    def guarded_read_csv(filepath_or_buffer=None, *a, **k):
        # Only path-like first args are real file reads; BytesIO (our load()) is skipped.
        if isinstance(filepath_or_buffer, (str, os.PathLike)):
            _maybe_flag(filepath_or_buffer, "pandas.read_csv")
        return _orig_read_csv(filepath_or_buffer, *a, **k)

    def guarded_open(file, mode="r", *a, **k):
        if "r" in mode and isinstance(file, (str, os.PathLike)):
            _maybe_flag(file, "open")
        return _orig_open(file, mode, *a, **k)

    pd.read_csv = guarded_read_csv
    builtins.open = guarded_open
    _guard_installed = True
