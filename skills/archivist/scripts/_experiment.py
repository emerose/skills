"""The ``experiment.yml`` sidecar: archivist's schema'd, machine-owned structured
metadata for one experiment. The README stays purely human prose; *all* structured
data (CRO, study ids, assays, ASOs, status, related, provenance) lives here, in a
file the tool reads/writes/validates deterministically — so it can never be
corrupted by parsing free-form prose.

The sidecar is YAML (a real, enforced schema), tracked in git next to ``README.md``,
human-editable and agent-populated. The tool reads metadata ONLY from here.

Stdlib + PyYAML only (no libkit), so it unit-tests in isolation.

## Provenance (the staleness signal) — an explicit input list, not a fingerprint

Each derived artifact (today: ``README.md``) records the exact files it was verified
against, with each file's version (sha256) at review time. No opaque roll-up — the
list IS the record, so a reviewer sees precisely what was checked, and ``audit``
reports drift per file. ``provenance`` is a list of entries, one per artifact:

```yaml
provenance:
  - artifact: README.md            # experiment-relative
    artifact_sha256: <sha of README.md at review>
    reviewed_at: 2026-06-04
    inputs:                        # the files the prose depends on
      - { path: "<repo-root-relative path>", sha256: <sha at review> }
```

* **Paths**: an input ``path`` is relative to the data-folder root, so a dependency
  can live anywhere (in the experiment's ``data/``/``raw/``/``reports/``/``analysis/``
  OR elsewhere, e.g. CRO slides under ``Shared/``). The ``artifact`` path is relative
  to the experiment folder.
* **No globs/directories** — inputs are individual files, deliberately, for clarity.
* **``review``** resolves the input set = the experiment's in-folder data files (every
  indexed file except a root ``README.*`` and ``experiment.yml``) PLUS any
  externally-declared inputs already listed (or passed via ``--input``), hashes each,
  and records them with ``artifact_sha256`` + ``reviewed_at``.
* **``audit``** re-hashes each listed input and the artifact and reports, per path,
  what ``changed`` / went ``missing`` / was ``added`` (a new in-folder data file not
  yet in the list), plus whether the artifact itself was edited since review. Nothing
  to squint at — every difference names a file.

A file/dir of hundreds of inputs is fine: clarity beats a compact opaque hash.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import _files

SIDECAR_NAME = "experiment.yml"
DEFAULT_ARTIFACT = "README.md"

# Recommended lifecycle statuses; common synonyms normalise to these. Unknown
# values raise a clear error (never silently accepted) listing the allowed set.
STATUS_VALUES = ("planned", "active", "complete", "terminated", "failed",
                 "superseded", "draft")
_STATUS_SYNONYMS = {"completed": "complete", "done": "complete", "in progress": "active",
                    "in-progress": "active", "ongoing": "active", "cancelled": "terminated",
                    "canceled": "terminated", "abandoned": "terminated"}

# The schema: field -> ("scalar" | "list", required?). Everything else is rejected
# so typos surface instead of silently vanishing.
_SCALAR = "scalar"
_LIST = "list"
SCHEMA: dict[str, str] = {
    "exp_id": _SCALAR, "name": _SCALAR, "title": _SCALAR, "cro": _SCALAR,
    "status": _SCALAR, "model": _SCALAR, "species": _SCALAR,
    "cro_study_ids": _LIST, "assays": _LIST, "asos": _LIST, "related": _LIST,
}
_FIELD_ORDER = ["exp_id", "name", "title", "cro", "cro_study_ids", "status",
                "model", "species", "assays", "asos", "related", "provenance"]


class SidecarError(ValueError):
    """experiment.yml failed validation — message names the field and the problem."""


# --------------------------------------------------------------------------- #
# provenance: explicit per-artifact input lists
# --------------------------------------------------------------------------- #
def _repo_rel(home: Path, abs_path: Path) -> str:
    return abs_path.resolve().relative_to(home.resolve()).as_posix()


def in_folder_data_files(home: Path, exp_dir: Path) -> list[str]:
    """Repo-root-relative paths of the experiment's in-folder *data* files — every
    indexed file under ``exp_dir`` except a root ``README.*`` render and the
    ``experiment.yml`` sidecar (those are prose/metadata, not evidence)."""
    out = []
    for f in _files.iter_experiment_files(exp_dir):
        if not f["rel_parts"]:  # at the experiment root
            low = f["filename"].lower()
            if low == SIDECAR_NAME or low.startswith("readme."):
                continue
        out.append(_repo_rel(home, f["abs_path"]))
    return sorted(out)


def resolve_inputs(home: Path, exp_dir: Path,
                   declared: list[str] | None = None) -> tuple[list[dict[str, str]], list[str]]:
    """Build the input list: the experiment's in-folder data files plus any
    ``declared`` external paths (repo-root-relative). Returns ``(inputs, missing)``
    where ``inputs`` is ``[{path, sha256}]`` sorted by path and ``missing`` lists any
    declared path that doesn't exist on disk."""
    paths = set(in_folder_data_files(home, exp_dir)) | set(declared or [])
    inputs, missing = [], []
    for p in sorted(paths):
        ap = home / p
        if ap.is_file():
            inputs.append({"path": p, "sha256": _files.sha256_file(ap)})
        else:
            missing.append(p)
    return inputs, missing


def _provenance_entry(sidecar: dict[str, Any], artifact: str) -> dict[str, Any] | None:
    for e in sidecar.get("provenance") or []:
        if isinstance(e, dict) and e.get("artifact") == artifact:
            return e
    return None


def review(home: Path, exp_dir: Path, sidecar: dict[str, Any], *, today: str | None = None,
           extra_inputs: list[str] | None = None,
           artifact: str = DEFAULT_ARTIFACT) -> tuple[dict[str, Any], list[str]]:
    """Record provenance for ``artifact``: resolve its input set (in-folder data +
    any previously-declared externals + ``extra_inputs``), hash each, and stamp
    ``artifact_sha256`` + ``reviewed_at``. Returns ``(updated_sidecar, missing)``.

    Externally-declared inputs (paths outside the experiment folder) are carried over
    from the existing entry so they survive re-reviews; in-folder data files are
    always (re)discovered."""
    exp_rel = exp_dir.resolve().relative_to(home.resolve()).as_posix()
    prev = _provenance_entry(sidecar, artifact) or {}
    prev_external = [i["path"] for i in (prev.get("inputs") or [])
                     if isinstance(i, dict) and not i["path"].startswith(exp_rel + "/")]
    declared = sorted(set(prev_external) | set(extra_inputs or []))
    inputs, missing = resolve_inputs(home, exp_dir, declared)

    art_abs = exp_dir / artifact
    entry = {
        "artifact": artifact,
        "artifact_sha256": _files.sha256_file(art_abs) if art_abs.is_file() else None,
        "reviewed_at": today or date.today().isoformat(),
        "inputs": inputs,
    }
    others = [e for e in (sidecar.get("provenance") or [])
              if not (isinstance(e, dict) and e.get("artifact") == artifact)]
    out = dict(sidecar)
    out["provenance"] = others + [entry]
    return out, missing


def staleness(home: Path, exp_dir: Path, sidecar: dict[str, Any],
              *, artifact: str = DEFAULT_ARTIFACT) -> dict[str, Any]:
    """Compare an artifact's recorded provenance to the files on disk now. Returns a
    dict with ``state`` one of ``no-provenance`` | ``up-to-date`` | ``stale``; when
    stale, also ``changed`` / ``missing`` / ``added`` input paths and
    ``artifact_changed``."""
    entry = _provenance_entry(sidecar, artifact)
    if not entry or not entry.get("inputs"):
        return {"state": "no-provenance"}
    recorded = {i["path"]: i.get("sha256") for i in entry["inputs"] if isinstance(i, dict)}
    changed, missing = [], []
    for p, sha in recorded.items():
        ap = home / p
        if not ap.is_file():
            missing.append(p)
        elif _files.sha256_file(ap) != sha:
            changed.append(p)
    # new in-folder data files not yet recorded (data added since review)
    added = sorted(set(in_folder_data_files(home, exp_dir)) - set(recorded))
    art_abs = exp_dir / artifact
    art_now = _files.sha256_file(art_abs) if art_abs.is_file() else None
    artifact_changed = art_now != entry.get("artifact_sha256")
    if not (changed or missing or added or artifact_changed):
        return {"state": "up-to-date", "n_inputs": len(recorded)}
    return {"state": "stale", "changed": sorted(changed), "missing": sorted(missing),
            "added": added, "artifact_changed": artifact_changed,
            "reviewed_at": entry.get("reviewed_at")}


# --------------------------------------------------------------------------- #
# read / validate / write
# --------------------------------------------------------------------------- #
def _as_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raise SidecarError(f"field '{field}' must be a YAML list, not a string "
                           f"(use '[a, b]' or '- a'); got {value!r}")
    if not isinstance(value, list):
        raise SidecarError(f"field '{field}' must be a list; got {type(value).__name__}")
    return [str(v).strip() for v in value if str(v).strip()]


def validate(data: Any) -> dict[str, Any]:
    """Validate + normalise a parsed sidecar value. Raises :class:`SidecarError` with
    a specific message on any problem (not a mapping, unknown field, wrong type, bad
    status)."""
    if not isinstance(data, dict):
        raise SidecarError("experiment.yml must contain a YAML mapping")
    unknown = set(data) - set(SCHEMA) - {"provenance"}
    if unknown:
        raise SidecarError(f"unknown field(s): {', '.join(sorted(unknown))}; "
                           f"allowed: {', '.join(sorted(SCHEMA))}")
    out: dict[str, Any] = {}
    for field, kind in SCHEMA.items():
        if field not in data or data[field] is None:
            continue
        if kind == _LIST:
            vals = _as_list(data[field], field)
            if vals:
                out[field] = vals
        else:
            out[field] = str(data[field]).strip()
    if not out.get("exp_id"):
        raise SidecarError("field 'exp_id' is required")
    if "status" in out:
        s = out["status"].lower()
        s = _STATUS_SYNONYMS.get(s, s)
        if s not in STATUS_VALUES:
            raise SidecarError(f"status {out['status']!r} is not recognised; "
                               f"allowed: {', '.join(STATUS_VALUES)} "
                               f"(synonyms: {', '.join(sorted(_STATUS_SYNONYMS))})")
        out["status"] = s
    prov = data.get("provenance")
    if isinstance(prov, list):
        entries = []
        for e in prov:
            if not isinstance(e, dict) or not e.get("artifact"):
                raise SidecarError("each 'provenance' entry must be a mapping with an 'artifact'")
            entry = {"artifact": str(e["artifact"]),
                     "artifact_sha256": e.get("artifact_sha256"),
                     "reviewed_at": e.get("reviewed_at"),
                     "inputs": [{"path": str(i["path"]), "sha256": i.get("sha256")}
                                for i in (e.get("inputs") or []) if isinstance(i, dict) and i.get("path")]}
            entries.append(entry)
        if entries:
            out["provenance"] = entries
    # a legacy mapping-shaped provenance (old data_fingerprint form) is simply
    # dropped: the experiment then reads as needing a re-review under the new model.
    return out


def read_sidecar(path: Path) -> dict[str, Any]:
    """Parse + validate an ``experiment.yml``. Raises :class:`SidecarError` (with the
    file named) on malformed YAML or schema violations."""
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise SidecarError(f"{path}: invalid YAML: {e}") from e
    try:
        return validate(raw)
    except SidecarError as e:
        raise SidecarError(f"{path}: {e}") from e


def dump_sidecar(data: dict[str, Any]) -> str:
    """Serialise a validated metadata dict to deterministic YAML (fixed field order,
    block style, UTF-8), suitable for writing to ``experiment.yml``."""
    import yaml

    ordered = {k: data[k] for k in _FIELD_ORDER if k in data and data[k] not in (None, [], "")}
    body = yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=100)
    return ("# archivist structured metadata for this experiment.\n"
            "# Machine-read for the catalog/search; edit freely or regenerate.\n"
            "# The README.md is yours — archivist never writes prose.\n"
            + body)
