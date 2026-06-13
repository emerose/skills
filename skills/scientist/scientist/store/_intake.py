"""Intake: plan where each file in a new CRO/vendor delivery goes within an
experiment folder, per the data folder's LAYOUT.md. Dependency-free (stdlib only)
so it unit-tests without libkit.

Intake always *copies* (never moves) — the source is treated as an immutable
delivery (often Attic), and a dry-run plan is reviewed before anything is written.

**Placement is mechanical, not semantic.** The *role* of a document (is this PDF a
protocol, a report, or a raw deliverable?) is a content judgment the calling agent
makes by reading the delivery — not something this module guesses from the filename.
The agent passes its decisions in via ``routes`` (see references/search-index.md,
"Filing a delivery"). This module only does the deterministic part:

  1. a file already organised under a known subfolder keeps that structure;
  2. an explicit ``routes`` entry from the agent wins for the rest;
  3. otherwise a format-determined extension (instrument/genomics binaries) or the
     conservative LAYOUT default (``raw`` = original measurements) applies.

Every plan entry records ``routed_by`` so the dry-run shows which placements are
certain (``path``/``agent``) and which are an unreviewed default (``ext``/``default``)
the agent should confirm or override with ``--route`` before committing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

# Subfolders an incoming file can be routed to (per LAYOUT.md).
SUBDIRS = ("protocol", "reports", "data", "raw", "analysis")

# Extension -> destination subfolder for formats whose role is fixed by the format
# itself (instrument output, genomics, flow): these are always original measurements,
# so routing them to ``raw`` is mechanical, not a content judgment. Document formats
# (.pdf/.docx/.pptx/.xlsx/…) are deliberately NOT here — their role depends on what
# they contain, which is the agent's call (pass via ``routes``).
_EXT_ROUTES: dict[str, str] = {
    ".eds": "raw", ".spk": "raw", ".pzfx": "raw", ".fcs": "raw", ".wsp": "raw",
    ".cram": "raw", ".bam": "raw", ".vcf": "raw", ".fastq": "raw", ".sav": "raw",
}


def classify_incoming(filename: str) -> str:
    """The deterministic *fallback* placement for an incoming file the agent didn't
    route and that isn't already under a known subfolder: a format-determined
    extension, else ``raw`` (original measurements — the conservative, LAYOUT-faithful
    default). Document role is the agent's judgment, supplied via ``plan_intake(...,
    routes=...)``; this is only the floor."""
    return _EXT_ROUTES.get(Path(filename).suffix.lower(), "raw")


def plan_intake(sources: Iterable[Path], exp_dir: Path,
                routes: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Plan copies of ``sources`` into ``exp_dir``.

    ``routes`` maps a source file's *name* to the subfolder the agent chose for it
    (the content judgment). Resolution order per file: (1) the file already sits under
    a known subfolder in the source → keep that structure; (2) an explicit ``routes``
    entry; (3) the deterministic fallback (:func:`classify_incoming`).

    Returns one entry per source: ``{src, subdir, dest, exists, routed_by}`` where
    ``dest`` is the planned absolute path (preserving any subpath *below* a recognised
    subfolder in the source, e.g. ``raw/Run 2/x.eds``), ``exists`` flags a destination
    collision, and ``routed_by`` is ``path`` | ``agent`` | ``ext`` | ``default`` so the
    caller can show which placements are certain vs an unreviewed default. Mac/OS cruft
    is skipped.
    """
    routes = routes or {}
    plan: list[dict[str, Any]] = []
    for src in sources:
        if src.name in (".DS_Store", "Thumbs.db") or src.name.startswith("~$"):
            continue
        # 1. source already organised under a known subfolder → keep that structure.
        subdir = None
        routed_by = "default"
        rel_tail = src.name
        for part in src.parts:
            if part.lower() in SUBDIRS:
                subdir = part.lower()
                idx = [p.lower() for p in src.parts].index(part)
                rel_tail = str(Path(*src.parts[idx + 1:]))
                routed_by = "path"
                break
        # 2. the agent's explicit routing decision; 3. the deterministic fallback.
        if subdir is None:
            if src.name in routes:
                subdir, routed_by = routes[src.name], "agent"
            else:
                subdir = classify_incoming(src.name)
                routed_by = "ext" if src.suffix.lower() in _EXT_ROUTES else "default"
        dest = exp_dir / subdir / rel_tail
        plan.append({"src": src, "subdir": subdir, "dest": dest,
                     "exists": dest.exists(), "routed_by": routed_by})
    return plan
