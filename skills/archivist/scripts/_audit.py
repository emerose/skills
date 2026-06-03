"""Audit + integrity helpers for archivist (reports only — never mutates).

Two layers:

* **Structural** (`structural_flags`, `redundant_archives`): deterministic checks
  over the folder + index — missing/unindexed files, thin metadata, layout drift,
  and redundant archives (a zip whose members are already extracted in-folder, the
  ``raw.zip`` pattern).
* **Staleness** (`staleness`): a generated doc carries an explicit dependency block
  (``<!-- archivist:deps … -->``) listing the source files it was built from with
  their sha256 at generation time. Re-hashing those vs disk tells us, cheaply and
  deterministically, whether a README/summary is out of date — the first half of the
  "keep summaries current" goal; the parallel-agent semantic pass is the second half.

Dependency-light: stdlib only (plus the sibling pure modules).
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import _experiment
import _files

_CRUFT = {".DS_Store", "Thumbs.db"}


def structural_flags(home: Path, exp_dir: Path, exp_rec: dict[str, Any],
                     file_records: list[dict[str, Any]]) -> list[str]:
    """Per-experiment integrity flags (each a short machine-friendly string)."""
    flags: list[str] = []

    if not (exp_dir / "README.md").is_file():
        flags.append("missing:readme")
    if not (exp_dir / _experiment.SIDECAR_NAME).is_file():
        flags.append("missing:experiment-yml")

    # file records whose on-disk file is gone
    for fr in file_records:
        p = fr.get("path")
        if p and not (home / p).exists():
            flags.append(f"file-missing:{p}")

    # on-disk indexable files not represented in the index (drift -> reindex).
    # A file is "indexed" if it's a primary path OR a tracked duplicate (other_paths).
    indexed: set[str] = set()
    for fr in file_records:
        if fr.get("path"):
            indexed.add(fr["path"])
        indexed.update(fr.get("other_paths") or [])
    on_disk = {_relhome(home, f["abs_path"]) for f in _files.iter_experiment_files(exp_dir)}
    missing_from_index = sorted(on_disk - indexed)
    if missing_from_index:
        flags.append(f"unindexed:{len(missing_from_index)}")

    # thin metadata: nothing useful extracted
    if not (exp_rec.get("cro") or exp_rec.get("assays") or exp_rec.get("asos")
            or exp_rec.get("cro_study_ids")):
        flags.append("thin-metadata")

    # layout: stray files at the experiment root (only README.* + experiment.yml belong)
    for child in sorted(exp_dir.iterdir()):
        if child.is_file() and child.name not in _CRUFT \
                and child.name != _experiment.SIDECAR_NAME \
                and not child.name.lower().startswith("readme") \
                and not child.name.startswith("."):
            flags.append(f"layout:root-file:{child.name}")

    for arc in redundant_archives(exp_dir):
        if arc["redundant"]:
            flags.append(f"redundant-archive:{arc['zip']}")

    return flags


def redundant_archives(exp_dir: Path) -> list[dict[str, Any]]:
    """For each .zip under ``exp_dir``, decide if it's redundant — i.e. every real
    member already exists, by basename, as an extracted file elsewhere in the same
    experiment folder. (The ``raw.zip`` case: a packaged delivery left alongside its
    own extracted contents.)"""
    out: list[dict[str, Any]] = []
    on_disk_names: set[str] = set()
    zips: list[Path] = []
    for f in _files.iter_experiment_files(exp_dir):
        if f["ext"] == ".zip":
            zips.append(f["abs_path"])
        else:
            on_disk_names.add(f["filename"])
    for z in zips:
        members = _zip_real_members(z)
        if not members:
            out.append({"zip": _rel(exp_dir, z), "redundant": False, "members": 0})
            continue
        extracted = sum(1 for m in members if Path(m).name in on_disk_names)
        out.append({
            "zip": _rel(exp_dir, z),
            "members": len(members),
            "extracted_in_folder": extracted,
            "redundant": extracted == len(members),
        })
    return out


def _zip_real_members(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            return [n for n in zf.namelist()
                    if not n.endswith("/")
                    and "__MACOSX" not in n
                    and Path(n).name not in _CRUFT
                    and not Path(n).name.startswith("._")]
    except (zipfile.BadZipFile, OSError):
        return []


def staleness(exp_dir: Path, sidecar: dict[str, Any]) -> dict[str, Any]:
    """Compare an experiment's recorded provenance fingerprint to the evidence on
    disk now (see :func:`_experiment.compute_fingerprint` for the exact algorithm).

    Returns ``{"state": ...}`` where state is:
      * ``"no-provenance"`` — the sidecar has never been stamped (can't judge by
        fingerprint; needs a semantic review).
      * ``"up-to-date"`` — recorded fingerprint matches the current evidence.
      * ``"stale"`` — they differ; also returns ``recorded``/``current`` fingerprints,
        the input counts, and ``reviewed_at`` so the mismatch is fully explainable.
    """
    prov = (sidecar or {}).get("provenance") or {}
    recorded = prov.get("data_fingerprint")
    if not recorded:
        return {"state": "no-provenance"}
    current, n_inputs, _ = _experiment.compute_fingerprint(exp_dir)
    if current == recorded:
        return {"state": "up-to-date", "fingerprint": current, "n_inputs": n_inputs}
    return {"state": "stale", "recorded": recorded, "current": current,
            "recorded_inputs": prov.get("n_inputs"), "current_inputs": n_inputs,
            "reviewed_at": prov.get("reviewed_at")}


def _relhome(home: Path, p: Path) -> str:
    try:
        return str(p.resolve().relative_to(home.resolve()))
    except ValueError:
        return str(p)


def _rel(base: Path, p: Path) -> str:
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(p)
