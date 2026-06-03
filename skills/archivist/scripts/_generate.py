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

from typing import Any

import _meta

# File roles that count as evidence a README/summary is derived from (so a change
# to one means the write-up may be stale). The readme/summary themselves are excluded.
_DEP_ROLES = {"raw", "data", "report", "protocol", "analysis"}


def files_on_disk_table(file_records: list[dict[str, Any]]) -> str:
    """A deterministic Markdown table of an experiment's files, grouped by role."""
    if not file_records:
        return "_No files indexed yet._"
    order = ["readme", "protocol", "data", "raw", "report", "analysis", "other"]
    rows = ["| Role | File | Type | Indexed as |", "|------|------|------|------------|"]
    for fr in sorted(file_records,
                     key=lambda r: (order.index(r.get("role", "other")) if r.get("role") in order else 99,
                                    r.get("path", ""))):
        rows.append("| {role} | `{path}` | {ftype} | {idx} |".format(
            role=fr.get("role", "?"),
            path=(fr.get("path") or "").replace("|", "/"),
            ftype=fr.get("file_type", ""),
            idx=fr.get("indexed_as", ""),
        ))
    return "\n".join(rows)


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
