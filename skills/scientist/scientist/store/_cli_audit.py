"""Integrity / provenance command handlers: ``check``, ``audit``, ``meta``,
``fingerprint``, ``review`` (plus the store-free audit report builder/renderer).

``check`` is the deterministic structural report + the cross-module literature
divergence lint; ``audit`` is provenance staleness (experiment.yml ledger vs the
evidence on disk) + a semantic-pass worklist; ``meta``/``fingerprint``/``review``
read or stamp the sidecar via :mod:`provenance`. The staleness check is store-free
(:func:`_staleness_entry`), so ``audit_report``/``print_audit_report`` are reused
by the store-less ``sci audit`` path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .. import provenance
from ..cli_utils import die, emit_json
from . import _audit, _files, _meta
from ._store import Store
from ._cli_common import _experiment_dirs, _find_experiment_dir


def _grounding_report_paths(home: Path) -> list[Path]:
    """Every experiment's grounding_report.json under ``home`` (``analysis/`` first, then
    the experiment root). Pure folder walk — no store needed; used by the cross-module
    literature-divergence lint. Wraps the shared :func:`provenance.iter_reports` walk."""
    return [report_path for _exp_dir, report_path in provenance.iter_reports(home)]


async def cmd_check(store: Store, args: argparse.Namespace) -> None:
    """Deterministic structural integrity report (reports only; never mutates).

    Plus a cross-module hygiene pass — the literature-divergence lint — which warns (never
    fails) when the same ``(citekey, paraphrase)`` is grounded on quotes that fold to
    different spans across the program's grounding reports. See ``grounding.refresh``."""
    worklist = []
    async for exp_dir, exp_id in _experiment_dirs(store, args.experiment):
        rec = await store.get_experiment(exp_id) or {"exp_id": exp_id}
        files = await store.files(exp_id)
        flags = _audit.structural_flags(store.home, exp_dir, rec, files)
        if flags:
            worklist.append({"exp_id": exp_id, "flags": flags})
    # Cross-module literature-divergence lint. A divergence is a *program* property (the same
    # (citekey, paraphrase) grounded on different spans in two modules), so it scans every
    # grounding report under home even when --experiment narrows the structural pass.
    from ..grounding import refresh as _refresh
    divergences = _refresh.divergence_lint(_grounding_report_paths(store.home))
    if args.json:
        emit_json({"structural": worklist, "literature_divergence": divergences})
        return
    if not worklist:
        print("✓ no structural issues found")
    for item in worklist:
        print(f"{item['exp_id']}:")
        for f in item["flags"]:
            print(f"    {f}")
    if divergences:
        print(_refresh.render_divergence(divergences))


def _staleness_entry(home: Path, exp_dir: Path, exp_id: str) -> dict[str, Any]:
    """The per-experiment provenance-staleness portion of an audit entry — PURE: it
    re-hashes the recorded ledger against the evidence on disk via the shared core, and
    never touches the libkit store. Shared by the store-backed ``cmd_audit`` and the
    store-free ``audit_report``."""
    sidecar_path = exp_dir / provenance.SIDECAR_NAME
    entry: dict[str, Any] = {"exp_id": exp_id}
    if not sidecar_path.is_file():
        entry["staleness"] = "no-experiment-yml"
        return entry
    try:
        provenance.read_sidecar(exp_dir)  # validate (raises on a bad sidecar)
        st = provenance.staleness(exp_dir, repo_root=home)
        entry["staleness"] = st["state"]
        if st["state"] == "stale":
            for k in ("changed", "missing", "added", "artifact_changed", "reviewed_at"):
                if st.get(k):
                    entry[k] = st[k]
    except provenance.SidecarError as e:
        entry["staleness"] = "invalid-experiment-yml"
        entry["error"] = str(e)
    return entry


def _source_files_on_disk(exp_dir: Path, home: Path) -> list[str]:
    """The data/report/raw/analysis files an agent should read for the semantic pass,
    as home-relative paths — derived by walking the folder, no store required."""
    out = []
    for f in _files.iter_experiment_files(exp_dir):
        if f["role"] in ("data", "report", "raw", "analysis"):
            try:
                out.append(str(f["abs_path"].resolve().relative_to(home.resolve())))
            except ValueError:
                out.append(f["filename"])
    return sorted(out)


def audit_report(home: Path, only: str | None = None) -> list[dict[str, Any]]:
    """Build the provenance-staleness audit report by walking on-disk experiment folders
    — NO libkit store required. One entry per experiment (or just ``only``), each with its
    ``staleness`` state (+ drift detail when stale) and a ``source_files`` worklist for
    the semantic pass. This is the store-free path `sci audit` uses when no store exists.
    """
    report = []
    if only:
        found = _find_experiment_dir(home, only)
        if not found:
            die(f"no experiment matching {only!r}")
        pairs = [(found[0], found[1]["exp_id"])]
    else:
        pairs = [(c.resolve(), p["exp_id"]) for c in sorted(home.iterdir())
                 if c.is_dir() and (p := _meta.parse_experiment_dirname(c.name))]
    for exp_dir, exp_id in pairs:
        entry = _staleness_entry(home, exp_dir, exp_id)
        entry["source_files"] = _source_files_on_disk(exp_dir, home)
        report.append(entry)
    return report


def print_audit_report(report: list[dict[str, Any]], as_json: bool) -> None:
    """Render an audit report (store-backed or store-free) — identical output either way."""
    if as_json:
        emit_json(report)
        return
    for e in report:
        print(f"{e['exp_id']}: {e['staleness']}")
        if e.get("error"):
            print(f"    {e['error']}")
        if e.get("staleness") == "stale":
            if e.get("artifact_changed"):
                print("    an artifact (e.g. README) edited since last review")
            for p in e.get("changed", []):
                print(f"    changed: {p}")
            for p in e.get("missing", []):
                print(f"    missing: {p}")
            for p in e.get("added", []):
                print(f"    added (unrecorded): {p}")
            print(f"    last reviewed {e.get('reviewed_at')}")
    print("\nFor the semantic pass, run `sci audit --json` and fan out an agent per "
          "experiment to read its source_files and verify the README/reports prose — "
          "including the prose ↔ claims check (every quantitative or qualitative result "
          "maps to a grounded claim, else flag it; see references/review-audit.md).")


async def cmd_audit(store: Store, args: argparse.Namespace) -> None:
    """Provenance staleness (experiment.yml provenance vs the evidence on disk) +
    a worklist for the parallel-agent semantic pass.

    Checks the WHOLE provenance ledger (data/ extract edges + analysis/ derive edges
    + the README review edge) via the shared core — re-hashing every recorded input
    and artifact and reporting per-file drift. When a store is present the
    ``source_files`` worklist is sourced from the index; the staleness check itself is
    store-free (see :func:`_staleness_entry`).
    """
    report = []
    async for exp_dir, exp_id in _experiment_dirs(store, args.experiment):
        files = await store.files(exp_id)
        entry = _staleness_entry(store.home, exp_dir, exp_id)
        # source files an agent should read to verify the prose semantically
        entry["source_files"] = [fr["path"] for fr in files
                                 if fr.get("role") in ("data", "report", "raw", "analysis")]
        report.append(entry)
    print_audit_report(report, args.json)


async def cmd_meta(store: Store, args: argparse.Namespace) -> None:
    """Show an experiment's structured metadata (from experiment.yml).

    Authoring the sidecar from a README is a reading task the agent does directly —
    see references/search-index.md ("Author experiment.yml from the README"); the
    tool never writes the sidecar from prose."""
    found = _find_experiment_dir(store.home, args.experiment)
    if not found:
        die(f"no experiment matching {args.experiment!r}")
    exp_dir, parsed = found
    sidecar = exp_dir / provenance.SIDECAR_NAME

    if not sidecar.is_file():
        die(f"no {provenance.SIDECAR_NAME} for {parsed['exp_id']} — author one by reading "
            f"the README (references/search-index.md), or scaffold with `sci new`")
    try:
        meta = provenance.read_sidecar(exp_dir)
    except provenance.SidecarError as e:
        die(str(e))
    emit_json(meta)


async def cmd_fingerprint(store: Store, args: argparse.Namespace) -> None:
    """Show the input set `review` would record for an experiment's README right now —
    the in-folder data files (+ any externally-declared inputs), each with its current
    sha256. Lets you see exactly what provenance will track."""
    found = _find_experiment_dir(store.home, args.experiment)
    if not found:
        die(f"no experiment matching {args.experiment!r}")
    exp_dir, parsed = found
    sidecar = exp_dir / provenance.SIDECAR_NAME
    declared = []
    if sidecar.is_file():
        meta = provenance.read_sidecar(exp_dir)
        exp_rel = exp_dir.resolve().relative_to(store.home.resolve()).as_posix()
        entry = provenance.provenance_entry(meta, provenance.DEFAULT_ARTIFACT) or {}
        declared = [i["path"] for i in (entry.get("inputs") or [])
                    if not i["path"].startswith(exp_rel + "/")]
    inputs, missing = provenance.resolve_inputs(store.home, exp_dir, declared)
    if args.json:
        emit_json({"exp_id": parsed["exp_id"], "inputs": inputs, "missing": missing})
        return
    for i in inputs:
        print(f"  {i['sha256']}  {i['path']}")
    print(f"({len(inputs)} input files" + (f", {len(missing)} declared-but-missing" if missing else "") + ")")
    for m in missing:
        print(f"  ! missing: {m}", file=sys.stderr)


async def cmd_review(store: Store, args: argparse.Namespace) -> None:
    """Mark an experiment's README as verified against its data: record an explicit
    input list (with each file's sha256) + the README's sha + a review date in
    experiment.yml. In-folder data files are included automatically; declare any
    external dependency (e.g. CRO slides under Shared/) with --input <repo-rel path>
    (repeatable; preserved across re-reviews). `audit` then reports per-file drift."""
    found = _find_experiment_dir(store.home, args.experiment)
    if not found:
        die(f"no experiment matching {args.experiment!r}")
    exp_dir, parsed = found
    sidecar = exp_dir / provenance.SIDECAR_NAME
    if not sidecar.is_file():
        die(f"no {provenance.SIDECAR_NAME} for {parsed['exp_id']} — create one first")
    try:
        meta = provenance.read_sidecar(exp_dir)
    except provenance.SidecarError as e:
        die(str(e))
    updated, missing = provenance.review(store.home, exp_dir, meta, today=args.date,
                                         extra_inputs=args.input or [])
    provenance.write_sidecar(exp_dir, updated)
    entry = provenance.provenance_entry(updated, provenance.DEFAULT_ARTIFACT)
    if entry is None:  # review() always writes this artifact entry; guard for robustness
        die(f"review did not record a {provenance.DEFAULT_ARTIFACT} provenance entry")
    if args.json:
        emit_json({"exp_id": parsed["exp_id"], "provenance": entry, "missing": missing})
    else:
        print(f"stamped {parsed['exp_id']}: README verified against "
              f"{len(entry.get('inputs') or [])} "
              f"input files (reviewed {entry.get('reviewed_at')})")
        for m in missing:
            print(f"  ! declared input not found on disk: {m}", file=sys.stderr)
