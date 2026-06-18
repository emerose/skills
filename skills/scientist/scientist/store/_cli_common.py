"""Shared, leaf-level helpers for the store CLI handler modules.

This is the bottom of the store-CLI import graph: the ``_cli_*`` handler modules
and the thin :mod:`store.cli` wiring layer both import from here, but nothing here
imports back from them — so the handler split stays free of circular imports.
Everything here is config/path/discovery glue over :mod:`provenance`, :mod:`_meta`,
and :mod:`cli_utils`; no command behaviour lives in this module.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from .. import cli_utils
from .. import provenance
from . import _meta
from ._store import STORE_DIRNAME
from ..cli_utils import die  # re-exported for handler call sites

# Narrative files larger than this are catalogued as descriptors rather than
# parsed+embedded whole (avoids choking on the multi-hundred-MB raw text dumps).
MAX_EMBED_BYTES = 25 * 1024 * 1024


def _load_dotenv(home: Path) -> None:
    """Load KEY=VALUE pairs from .env files (stdlib only). Real env + earlier
    files win. Search: home, cwd, this script's parents, then ~/.env."""
    here = Path(__file__).resolve()
    candidates = [home / ".env", Path.cwd() / ".env",
                  *[p / ".env" for p in here.parents], Path.home() / ".env"]
    seen: set[Path] = set()
    for env_path in candidates:
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _home(args: argparse.Namespace) -> Path:
    # Shared precedence (--home → $SCIENTIST_HOME → inferred checkout root); the store
    # additionally falls back to cwd when nothing else resolves.
    return cli_utils.resolve_home(args) or Path.cwd()


def _require_initialized(home: Path) -> None:
    if not (home / STORE_DIRNAME / "catalog.duckdb").exists():
        die(f"no scientist store under {home} — run `sci init --home {home}` first")


def _find_experiment_dir(home: Path, ident: str) -> tuple[Path, dict[str, Any]] | None:
    """Resolve an experiment by exp_id (prefix) or by a path."""
    p = Path(ident)
    if p.is_dir():
        parsed = _meta.parse_experiment_dirname(p.name)
        if parsed:
            return p.resolve(), parsed
    for child in sorted(home.iterdir()):
        if not child.is_dir():
            continue
        parsed = _meta.parse_experiment_dirname(child.name)
        if parsed and (parsed["exp_id"] == ident or child.name == ident):
            return child.resolve(), parsed
    return None


async def _experiment_dirs(store, only: str | None):
    """Yield (exp_dir, exp_id) for one experiment or all of them."""
    if only:
        found = _find_experiment_dir(store.home, only)
        if not found:
            die(f"no experiment matching {only!r}")
        yield found[0], found[1]["exp_id"]
        return
    for child in sorted(store.home.iterdir()):
        parsed = _meta.parse_experiment_dirname(child.name) if child.is_dir() else None
        if parsed:
            yield child.resolve(), parsed["exp_id"]


class _HomeOnly:
    """Lightweight stand-in for commands that need only the data folder, not the
    libkit store (so `pr` doesn't require an embedding backend)."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def relpath(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.home.resolve()))
        except ValueError:
            return str(path)
