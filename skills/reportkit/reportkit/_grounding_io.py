"""Canonical locate + load for ``grounding_report.json``.

A *grounding report* is the JSON a claims pytest run writes (via ``--grounding-out``):
``{"claims": [ {id, statement, outcome, strength, kind, evidence, inputs, …}, … ]}``
(a bare top-level list is also tolerated). Four call sites used to reimplement the same
two mechanical steps — **where does the report live** and **read it + pull the claims
list** — each with its own copy of the ``analysis/`` vs experiment-root precedence and
the ``json.load`` + ``data.get("claims")`` shape. This module is the single home for
those two steps; every caller keeps its own *interpretation* of the claims (claim cards,
lit spans, store records, DAG trace) on top of these primitives.

Pure stdlib leaf module (imports nothing else in :mod:`reportkit`). The host skill re-exports
these names (e.g. ``scientist.provenance.find_report`` / ``…load_report`` / ``…claims_of``) so
its callers keep their surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GROUNDING_REPORT_NAME = "grounding_report.json"


# --------------------------------------------------------------------------- #
# locate
# --------------------------------------------------------------------------- #
def report_candidates(exp_dir: Path) -> list[Path]:
    """The locate ladder for one experiment, in precedence order:
    ``<exp>/analysis/grounding_report.json`` then ``<exp>/grounding_report.json``.

    Returns the candidate paths (whether or not they exist) so a caller can show what it
    looked for; use :func:`find_report` to get the first that is actually a file."""
    exp = Path(exp_dir)
    return [exp / "analysis" / GROUNDING_REPORT_NAME, exp / GROUNDING_REPORT_NAME]


def find_report(exp_dir: Path, override: Path | str | None = None) -> Path | None:
    """Locate the grounding report for one experiment: ``override`` if given (only when it
    is a file), else the first existing path in the :func:`report_candidates` ladder
    (``analysis/`` first, then the experiment root). Returns the path, or ``None``."""
    if override is not None:
        p = Path(override)
        return p if p.is_file() else None
    return next((p for p in report_candidates(exp_dir) if p.is_file()), None)


def iter_reports(home: Path):
    """Yield ``(exp_dir, report_path)`` for every immediate child of ``home`` that has a
    grounding report, resolving each child's locate ladder (``analysis/`` first, then the
    experiment root). Children are visited in sorted order; non-directories are skipped.
    The home-wide walk shared by the report-citation index, the store-side claim index,
    and the cross-module literature-divergence lint."""
    if not Path(home).is_dir():
        return
    for child in sorted(Path(home).iterdir()):
        if not child.is_dir():
            continue
        report_path = find_report(child)
        if report_path is not None:
            yield child, report_path


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #
def load_report(report_path: Path | str) -> Any:
    """Read + ``json.loads`` one grounding report (raises ``OSError`` / ``ValueError`` on a
    missing or malformed file — the caller decides whether to skip, ``die``, or surface it)."""
    return json.loads(Path(report_path).read_text(encoding="utf-8"))


def claims_of(data: Any) -> list[dict[str, Any]] | None:
    """The claims list out of a parsed report value: ``data["claims"]`` for the usual
    ``{"claims": [...]}`` mapping, or ``data`` itself when the report is a bare top-level
    list. Returns ``None`` when neither yields a list (caller treats that as "no claims" /
    malformed) — it does NOT coerce to ``[]``, so a caller that must distinguish an absent
    claims list from an empty one still can."""
    claims = data.get("claims") if isinstance(data, dict) else data
    return claims if isinstance(claims, list) else None
