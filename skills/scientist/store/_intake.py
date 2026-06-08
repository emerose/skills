"""Intake: classify the files in a new CRO/vendor delivery and plan where each
goes within an experiment folder, per the data folder's LAYOUT.md. Dependency-free
(stdlib only) so it unit-tests without libkit.

Intake always *copies* (never moves) — the source is treated as an immutable
delivery (often Attic), and a dry-run plan is reviewed before anything is written.
Classification is heuristic; the dry-run + the experiment author are the safety net.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

# Subfolders an incoming file can be routed to (per LAYOUT.md).
SUBDIRS = ("protocol", "reports", "data", "raw", "analysis")

# Filename keyword -> destination subfolder, checked in order (first wins).
# Patterns avoid a trailing \b: filenames append digits/underscores ("SoW2",
# "Report_03") and \b doesn't trigger before a word char.
_KEYWORD_ROUTES: list[tuple[str, str]] = [
    (r"\bsow\d*\b|\bstatement of work", "protocol"),
    (r"\bwork ?plan", "protocol"),
    (r"\bamendment", "protocol"),
    (r"\bprotocol", "protocol"),
    (r"\bdraft results", "reports"),
    (r"\breports?\b|\breport[_\-\s]", "reports"),
    (r"\bdata package", "reports"),
    (r"\binterpretation", "reports"),
    (r"\bsummary", "reports"),
    (r"\bTC[-_ ]?\d", "reports"),          # TC-05 telecon decks
    (r"\bposter", "reports"),
]
# Extension -> destination subfolder when no keyword matched.
_EXT_ROUTES: dict[str, str] = {
    ".pptx": "reports", ".ppt": "reports",
    ".eds": "raw", ".spk": "raw", ".pzfx": "raw", ".fcs": "raw", ".wsp": "raw",
    ".cram": "raw", ".bam": "raw", ".vcf": "raw", ".fastq": "raw", ".sav": "raw",
}


def classify_incoming(filename: str) -> str:
    """Route one incoming file to a subfolder (protocol/reports/data/raw/analysis).

    Keyword matches win first (a 'Final Report.docx' is a report, not raw); then
    extension; the default for an unmarked CRO data file is ``raw`` (original
    measurements), which is the conservative, LAYOUT-faithful choice.
    """
    name = filename.lower()
    for pat, sub in _KEYWORD_ROUTES:
        if re.search(pat, name):
            return sub
    return _EXT_ROUTES.get(Path(filename).suffix.lower(), "raw")


def plan_intake(sources: Iterable[Path], exp_dir: Path) -> list[dict[str, Any]]:
    """Plan copies of ``sources`` into ``exp_dir`` per classification.

    Returns one entry per source: ``{src, subdir, dest, exists}``. ``dest`` is the
    planned absolute path (preserving any subpath *below* a recognised subfolder in
    the source, e.g. ``raw/Run 2/x.eds``); ``exists`` flags a destination collision
    so the caller can warn before overwriting. Mac/OS cruft is skipped.
    """
    plan: list[dict[str, Any]] = []
    for src in sources:
        if src.name in (".DS_Store", "Thumbs.db") or src.name.startswith("~$"):
            continue
        # If the source path already sits under a known subfolder, keep that
        # structure; otherwise classify by name/extension.
        subdir = None
        rel_tail = src.name
        for part in src.parts:
            if part.lower() in SUBDIRS:
                subdir = part.lower()
                idx = [p.lower() for p in src.parts].index(part)
                rel_tail = str(Path(*src.parts[idx + 1:]))
                break
        if subdir is None:
            subdir = classify_incoming(src.name)
        dest = exp_dir / subdir / rel_tail
        plan.append({"src": src, "subdir": subdir, "dest": dest, "exists": dest.exists()})
    return plan
