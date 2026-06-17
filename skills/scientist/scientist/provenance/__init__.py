"""Shared provenance core for the scientist skill: the ``experiment.yml`` sidecar
and its unified ``provenance`` ledger.

This is the single source of truth for an experiment's structured metadata + the
per-artifact input lists that ground every derived file (a ``data/…`` extraction,
an ``analysis/…`` rollup, the ``README.md`` review) back to the exact raw files it
was built from, each with its sha256 at review/extraction time.

The design is a superset of two predecessors that this consolidation merges:

* the extract stage's data-provenance writer — wrote ``data/<file>`` artifact entries
  (raw sources + the recipe as inputs), deduped by artifact, sorted for diffability;
* the schema'd sidecar (validated fields, status synonyms, unknown-key rejection) and
  the ``README.md`` provenance + staleness model.

Both write the SAME entry shape into one ``provenance`` list keyed by artifact path;
the artifact path (``data/…`` vs ``analysis/…`` vs ``README.md``) is the only kind
discriminator, so ``raw → data → analysis → README`` is one DAG.

Stdlib + PyYAML only — pure, no keys, no network; unit-tests in isolation.

## Provenance entry shape

```yaml
provenance:
  - artifact: data/01_qpcr_cp_dcp.csv   # experiment-relative artifact path
    artifact_sha256: <sha of the artifact at extraction/review>
    reviewed_at: 2026-06-08
    inputs:                              # the files the artifact depends on
      - { path: "<repo-root-relative path>", sha256: <sha at review> }
```

* **Input ``path``** is relative to the data-repo root, so an input can live anywhere
  (the experiment's ``raw/``/``data/``/``reports/``/``analysis/`` OR elsewhere, e.g.
  CRO slides under ``Shared/``).
* **No globs/directories** — inputs are individual files, deliberately, for clarity.
* **Staleness** re-hashes each recorded input + the artifact and reports, per path,
  what ``changed`` / went ``missing`` / was ``added`` — every difference names a file.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

SIDECAR_NAME = "experiment.yml"


# Lifecycle statuses; common synonyms normalise to these. Unknown values raise a
# clear error (never silently accepted) listing the allowed set.
STATUSES = {"planned", "active", "complete", "terminated", "failed",
            "superseded", "draft"}
_STATUS_VALUES = ("planned", "active", "complete", "terminated", "failed",
                  "superseded", "draft")
_STATUS_SYNONYMS = {"completed": "complete", "done": "complete", "in progress": "active",
                    "in-progress": "active", "ongoing": "active", "cancelled": "terminated",
                    "canceled": "terminated", "abandoned": "terminated"}

# The schema: field -> "scalar" | "list". Everything else (besides ``provenance``)
# is rejected so typos surface instead of silently vanishing.
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
# hashing
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file's bytes (hex)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------- #
# validate / read / write
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
    """Validate + normalise a parsed sidecar value. Enforces the schema, normalises
    status synonyms (e.g. ``"completed" -> "complete"``), rejects unknown top-level
    keys, and requires ``exp_id``. Raises :class:`SidecarError` with a specific
    message on any problem."""
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
        if s not in STATUSES:
            raise SidecarError(f"status {out['status']!r} is not recognised; "
                               f"allowed: {', '.join(_STATUS_VALUES)} "
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
                                for i in (e.get("inputs") or [])
                                if isinstance(i, dict) and i.get("path")]}
            entries.append(entry)
        if entries:
            out["provenance"] = entries
    # a legacy mapping-shaped provenance (old data_fingerprint form) is simply
    # dropped: the experiment then reads as needing a re-review under the new model.
    return out


def _load_raw(exp_dir: Path) -> dict[str, Any]:
    """Parse ``experiment.yml`` to a raw dict (no schema validation). Empty dict if
    absent; raises only on malformed YAML."""
    import yaml

    path = Path(exp_dir) / SIDECAR_NAME
    if not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise SidecarError(f"{path}: invalid YAML: {e}") from e


def read_sidecar(exp_dir: Path) -> dict[str, Any]:
    """Load + validate ``<exp_dir>/experiment.yml``. Returns a validated dict (empty
    dict if the file is absent). Raises :class:`SidecarError` (with the file named)
    on malformed YAML or schema violations."""
    path = Path(exp_dir) / SIDECAR_NAME
    raw = _load_raw(exp_dir)
    if not raw:
        return {}
    try:
        return validate(raw)
    except SidecarError as e:
        raise SidecarError(f"{path}: {e}") from e


def _dump_sidecar_text(data: dict[str, Any]) -> str:
    """Deterministic YAML for an (already-shaped) sidecar dict: fixed field order,
    block style, UTF-8, wide so long paths aren't folded. No validation."""
    import yaml

    ordered = {k: data[k] for k in _FIELD_ORDER
               if k in data and data[k] not in (None, [], "")}
    body = yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=4096)
    return ("# scientist structured metadata for this experiment.\n"
            "# Machine-read for the catalog/search and provenance; edit freely or regenerate.\n"
            "# The README.md is yours — the tool never writes prose.\n"
            + body)


def write_sidecar(exp_dir: Path, data: dict[str, Any]) -> None:
    """Validate ``data`` and write it to ``<exp_dir>/experiment.yml`` as deterministic
    YAML (fixed field order, block style, UTF-8). Two writes of equal data are
    byte-identical."""
    (Path(exp_dir) / SIDECAR_NAME).write_text(_dump_sidecar_text(validate(data)),
                                              encoding="utf-8")


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def input_entry(path: Path, repo_root: Path) -> dict[str, str]:
    """An input descriptor ``{path: <repo-relative>, sha256: ...}`` for a file. The
    path is rendered relative to ``repo_root`` when possible, else the bare name."""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        rel = p.name
    return {"path": rel, "sha256": sha256_file(p)}


DEFAULT_ARTIFACT = "README.md"

_IN_FOLDER_DIRS = ("raw", "data", "reports", "analysis")


def in_folder_inputs(exp_dir: Path) -> list[Path]:
    """Absolute paths of the experiment's in-folder data files: everything under
    ``raw/`` ``data/`` ``reports/`` ``analysis/``, EXCLUDING a root ``README.*`` and
    the ``experiment.yml`` sidecar (those are prose/metadata, not evidence). Sorted,
    OS-cruft (``.DS_Store``, ``~$`` lock files, ``__pycache__``) skipped."""
    exp = Path(exp_dir)
    out: list[Path] = []
    for sub in _IN_FOLDER_DIRS:
        d = exp / sub
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            if "__pycache__" in f.parts:
                continue
            name = f.name
            if name in {".DS_Store", "Thumbs.db"} or name.startswith("~$") or name.startswith("._"):
                continue
            out.append(f)
    return sorted(out)


# --------------------------------------------------------------------------- #
# provenance ledger
# --------------------------------------------------------------------------- #
def record_provenance(exp_dir: Path, entries: list[dict], *, repo_root: Path | None = None) -> None:
    """Merge artifact provenance ``entries`` into ``<exp_dir>/experiment.yml``'s unified
    ``provenance`` list, then write it back deterministically.

    Each entry has shape ``{artifact, artifact_sha256, reviewed_at, inputs:[{path,sha256}]}``.
    Merge rules:

    * DEDUP by ``artifact`` — a new entry replaces any existing entry for the same
      artifact path;
    * PRESERVE entries for OTHER artifacts (e.g. ``README.md`` owned by the review
      step, or ``analysis/…`` rollups) and any external (out-of-folder) inputs they
      carry;
    * SORT the merged list by ``artifact`` for diffability.

    ``repo_root`` is accepted for symmetry with the rest of the API; paths in
    ``entries`` are already repo-relative, so it is not required here.

    Recording is a ledger operation, not metadata authoring: the existing sidecar is
    read leniently (any metadata fields it owns are preserved verbatim) and a
    provenance-only sidecar — one with no ``exp_id`` yet — is accepted, matching the
    extract stage's behavior of stamping provenance onto an otherwise-bare experiment.yml.
    """
    sidecar = _load_raw(exp_dir)
    # superseded legacy key from an earlier data-provenance form — drop if present
    sidecar.pop("data_provenance", None)
    ours = {e["artifact"] for e in entries}
    kept = [e for e in (sidecar.get("provenance") or [])
            if isinstance(e, dict) and e.get("artifact") not in ours]
    sidecar["provenance"] = sorted(kept + list(entries), key=lambda e: e["artifact"])
    (Path(exp_dir) / SIDECAR_NAME).write_text(_dump_sidecar_text(sidecar), encoding="utf-8")


def edges(sidecar: dict[str, Any], prefix: str | None = None) -> list[dict]:
    """Provenance entries from a sidecar, optionally filtered by artifact prefix
    (``'data/'``, ``'analysis/'``, ``'README'``). ``None`` returns all entries."""
    prov = [e for e in (sidecar.get("provenance") or []) if isinstance(e, dict)]
    if prefix is None:
        return prov
    return [e for e in prov if str(e.get("artifact", "")).startswith(prefix)]


# --------------------------------------------------------------------------- #
# README review (artifact provenance with declared external inputs)
# --------------------------------------------------------------------------- #
def provenance_entry(sidecar: dict[str, Any], artifact: str) -> dict[str, Any] | None:
    """The provenance entry for ``artifact`` in a (raw or validated) sidecar, or None."""
    for e in sidecar.get("provenance") or []:
        if isinstance(e, dict) and e.get("artifact") == artifact:
            return e
    return None


def in_folder_data_files(repo_root: Path, exp_dir: Path) -> list[str]:
    """Repo-root-relative paths of the experiment's in-folder *data* files — every
    file under ``raw/`` ``data/`` ``reports/`` ``analysis/`` except a root ``README.*``
    and the ``experiment.yml`` sidecar (those are prose/metadata, not evidence). The
    same set as :func:`in_folder_inputs`, rendered relative to ``repo_root``."""
    out = []
    for f in in_folder_inputs(exp_dir):
        try:
            out.append(f.resolve().relative_to(Path(repo_root).resolve()).as_posix())
        except ValueError:
            out.append(f.name)
    return sorted(out)


def resolve_inputs(repo_root: Path, exp_dir: Path,
                   declared: list[str] | None = None) -> tuple[list[dict[str, str]], list[str]]:
    """Build the input list for a review: the experiment's in-folder data files plus
    any ``declared`` external paths (repo-root-relative). Returns ``(inputs, missing)``
    where ``inputs`` is ``[{path, sha256}]`` sorted by path and ``missing`` lists any
    declared path that doesn't exist on disk."""
    paths = set(in_folder_data_files(repo_root, exp_dir)) | set(declared or [])
    inputs, missing = [], []
    for p in sorted(paths):
        ap = Path(repo_root) / p
        if ap.is_file():
            inputs.append({"path": p, "sha256": sha256_file(ap)})
        else:
            missing.append(p)
    return inputs, missing


def review(repo_root: Path, exp_dir: Path, sidecar: dict[str, Any], *,
           today: str | None = None, extra_inputs: list[str] | None = None,
           artifact: str = DEFAULT_ARTIFACT) -> tuple[dict[str, Any], list[str]]:
    """Record provenance for ``artifact`` (default ``README.md``): resolve its input
    set (in-folder data + any previously-declared externals + ``extra_inputs``), hash
    each, and stamp ``artifact_sha256`` + ``reviewed_at``. Returns
    ``(updated_sidecar, missing)``.

    Externally-declared inputs (paths outside the experiment folder) are carried over
    from the existing entry so they survive re-reviews; in-folder data files are always
    (re)discovered. This is the README-review wrapper over :func:`record_provenance`'s
    ledger merge — the data/ and analysis/ edges are recorded the same way."""
    exp_rel = Path(exp_dir).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    prev = provenance_entry(sidecar, artifact) or {}
    prev_external = [i["path"] for i in (prev.get("inputs") or [])
                     if isinstance(i, dict) and not str(i["path"]).startswith(exp_rel + "/")]
    declared = sorted(set(prev_external) | set(extra_inputs or []))
    inputs, missing = resolve_inputs(repo_root, exp_dir, declared)

    art_abs = Path(exp_dir) / artifact
    entry = {
        "artifact": artifact,
        "artifact_sha256": sha256_file(art_abs) if art_abs.is_file() else None,
        "reviewed_at": today or date.today().isoformat(),
        "inputs": inputs,
    }
    others = [e for e in (sidecar.get("provenance") or [])
              if not (isinstance(e, dict) and e.get("artifact") == artifact)]
    out = dict(sidecar)
    out["provenance"] = others + [entry]
    return out, missing


def staleness(exp_dir: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """Re-hash each artifact's recorded inputs + the artifact itself and report drift.

    Returns a dict with ``state`` one of ``no-provenance`` | ``up-to-date`` | ``stale``.
    When stale, also ``changed`` / ``missing`` / ``added`` input paths and a per-artifact
    ``artifact_changed`` flag (aggregated as ``artifact_changed`` = any artifact edited).

    * ``changed`` — a recorded input's bytes differ from the recorded sha;
    * ``missing`` — a recorded input no longer exists on disk;
    * ``added``   — an in-folder data file not yet recorded under any artifact.

    Paths are resolved relative to ``repo_root`` (default: ``exp_dir.parent``, the
    data-repo root, matching how the extract stage records repo-relative input paths).
    """
    exp = Path(exp_dir)
    home = Path(repo_root) if repo_root is not None else exp.parent
    # Staleness only needs the provenance ledger, so read it LENIENTLY: a provenance-only
    # sidecar (no exp_id yet — the extract stage stamps provenance before metadata is filled)
    # is a valid ledger to check. (Callers that want schema validation call read_sidecar
    # separately first, as `audit` does.)
    sidecar = _load_raw(exp)
    prov = edges(sidecar)
    recorded_inputs = [e for e in prov if e.get("inputs")]
    if not recorded_inputs:
        return {"state": "no-provenance"}

    recorded: dict[str, str | None] = {}
    for e in recorded_inputs:
        for i in e.get("inputs") or []:
            if isinstance(i, dict) and i.get("path"):
                recorded[i["path"]] = i.get("sha256")

    changed, missing = [], []
    for p, sha in recorded.items():
        ap = home / p
        if not ap.is_file():
            missing.append(p)
        elif sha256_file(ap) != sha:
            changed.append(p)

    # in-folder data files not yet recorded under any artifact (data added since
    # review). Recorded *artifacts* (e.g. data/<file> outputs) are themselves
    # in-folder files but are tracked as artifacts, not inputs — exclude them so a
    # committed output doesn't read as an unrecorded addition.
    recorded_paths = set(recorded)
    artifact_rel = set()
    for e in prov:
        try:
            artifact_rel.add((exp / e["artifact"]).resolve().relative_to(home.resolve()).as_posix())
        except ValueError:
            artifact_rel.add(e["artifact"])
    in_folder_rel = set()
    for f in in_folder_inputs(exp):
        try:
            in_folder_rel.add(f.resolve().relative_to(home.resolve()).as_posix())
        except ValueError:
            in_folder_rel.add(f.name)
    added = sorted(in_folder_rel - recorded_paths - artifact_rel)

    artifact_changed = False
    for e in prov:
        art_abs = exp / e["artifact"]
        art_now = sha256_file(art_abs) if art_abs.is_file() else None
        if art_now != e.get("artifact_sha256"):
            artifact_changed = True

    if not (changed or missing or added or artifact_changed):
        return {"state": "up-to-date", "n_inputs": len(recorded)}
    # Surface the most-recent review date among the recorded edges so callers can
    # report "last reviewed <date>" without re-reading the sidecar.
    reviewed = sorted(r for e in recorded_inputs if (r := e.get("reviewed_at")))
    return {"state": "stale", "changed": sorted(changed), "missing": sorted(missing),
            "added": added, "artifact_changed": artifact_changed,
            "reviewed_at": reviewed[-1] if reviewed else None}
