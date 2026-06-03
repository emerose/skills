"""Generators for archivist's maintained documents: per-experiment README blocks
and the top-level scientific summary. Dependency-free (stdlib + sibling pure
modules) so the generation logic unit-tests without libkit.

Division of labour (per the skill's design): archivist generates the *mechanical*
sections (the Files-on-disk table, the explicit dependency block, the top-level
experiment index) deterministically and leaves them in archivist-managed regions;
the *interpretive* prose (synopsis, key findings, caveats) is authored by the
human/agent and preserved verbatim. Generated docs always carry an explicit
dependency block so `audit` can detect staleness cheaply.
"""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Any

import _meta

# File roles that count as evidence a README/summary is derived from (so a change
# to one means the write-up may be stale). The readme/summary themselves are excluded.
_DEP_ROLES = {"raw", "data", "report", "protocol", "analysis"}


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
        if n <= list_threshold:
            listing = ", ".join(f"`{x}`" for x in names)
        else:
            exts = Counter((r.get("file_type") or "?") for r in recs)
            ext_str = ", ".join(f"{c}×{e}" for e, c in exts.most_common())
            sample = ", ".join(f"`{x}`" for x in names[:4])
            listing = f"{ext_str} — e.g. {sample}, …"
        lines.append(f"- **{role}** ({n}): {listing}")
    return "\n".join(lines)


def deps_for_experiment(file_records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The explicit dependency list for an experiment's README: each evidence file
    with the sha256 captured at index time (reused, not recomputed)."""
    return [{"path": fr["path"], "sha256": fr.get("sha256", "")}
            for fr in file_records
            if fr.get("role") in _DEP_ROLES and fr.get("path")]


def refresh_readme(existing: str | None, exp_rec: dict[str, Any],
                   file_records: list[dict[str, Any]]) -> str:
    """Return README text with the archivist-managed regions refreshed.

    Non-destructive: only the ``files`` managed block and the dependency block are
    (re)written; all human/agent prose (synopsis, key findings, caveats, the IDs
    table) is preserved. A brand-new README starts from the scaffold template.
    """
    text = existing if existing is not None else _meta.readme_template(exp_rec)
    text = _meta.set_managed_block(text, "files",
                                   "## Files on disk\n\n" + files_on_disk_table(file_records))
    text = _meta.set_deps_block(text, deps_for_experiment(file_records))
    return text


def top_summary(experiments: list[dict[str, Any]],
                readme_deps: list[dict[str, str]]) -> str:
    """The top-level cross-experiment scientific summary scaffold.

    The narrative synthesis above the managed index is authored by the human/agent
    (preserved across regenerations); archivist maintains the experiment index and
    the dependency block (each experiment's README), so `audit` flags the summary
    stale when any experiment write-up changes.
    """
    intro = ("# Scientific summary\n\n"
             "_Cross-experiment synthesis. Write the narrative above the index below;\n"
             "archivist maintains the index and dependency list._\n")
    index = "## Experiments\n\n" + _meta.catalog_markdown(experiments).split("\n", 2)[2]
    text = _meta.set_managed_block(intro, "experiment-index", index)
    text = _meta.set_deps_block(text, readme_deps)
    return text
