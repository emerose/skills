"""Rendering helpers for archivist. Archivist does NOT write prose: READMEs are
purely human/agent-authored and structured metadata lives in ``experiment.yml``.
This module only renders *views* of the index on demand (e.g. the Files-on-disk
summary shown by ``arx show``). Dependency-free (stdlib) so it unit-tests in
isolation.
"""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Any

_ROLE_ORDER = ["readme", "protocol", "data", "raw", "report", "analysis", "other"]


def _basename(path: str) -> str:
    return PurePosixPath(path).name


def files_on_disk_table(file_records: list[dict[str, Any]], *, list_threshold: int = 12) -> str:
    """A deterministic, readable Markdown summary of an experiment's files, grouped
    by role. Small roles are listed by filename; large ones (e.g. hundreds of raw
    CSVs) are summarised by count + file types + a few examples, so the block stays
    legible even for 200+-file experiments instead of dumping a giant table."""
    if not file_records:
        return "_No files indexed yet._"
    by_role: dict[str, list[dict[str, Any]]] = {}
    for fr in file_records:
        by_role.setdefault(fr.get("role", "other"), []).append(fr)

    lines = []
    for role in _ROLE_ORDER + sorted(set(by_role) - set(_ROLE_ORDER)):
        recs = by_role.get(role)
        if not recs:
            continue
        names = sorted(_basename(r.get("path", "")) for r in recs)
        n = len(recs)
        dups = sorted({_basename(p) for r in recs for p in (r.get("other_paths") or [])})
        dup_note = f" (duplicate copies also on disk: {', '.join(f'`{d}`' for d in dups)})" if dups else ""
        if n <= list_threshold:
            listing = ", ".join(f"`{x}`" for x in names) + dup_note
        else:
            exts = Counter((r.get("file_type") or "?") for r in recs)
            ext_str = ", ".join(f"{c}×{e}" for e, c in exts.most_common())
            sample = ", ".join(f"`{x}`" for x in names[:4])
            listing = f"{ext_str} — e.g. {sample}, …"
        lines.append(f"- **{role}** ({n}): {listing}")
    return "\n".join(lines)
