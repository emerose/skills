"""The ``experiment.yml`` sidecar: archivist's schema'd, machine-owned structured
metadata for one experiment. The README stays purely human prose; *all* structured
data (CRO, study ids, assays, ASOs, status, related, provenance) lives here, in a
file the tool reads/writes/validates deterministically — so it can never be
corrupted by parsing free-form prose.

The sidecar is YAML (a real, enforced schema), tracked in git next to ``README.md``,
human-editable and agent-populated. The tool reads metadata ONLY from here.

Stdlib + PyYAML only (no libkit), so it unit-tests in isolation.

## Provenance fingerprint (the staleness signal)

``provenance.data_fingerprint`` answers "has the evidence changed since the prose was
last verified?" It MUST be computed by one unambiguous, reproducible algorithm —
otherwise it produces mysterious staleness. The algorithm (`compute_fingerprint`):

1. **Evidence set** = every file under the experiment directory that
   :func:`_files.iter_experiment_files` yields (its ignore rules + the 0-byte skip
   apply), EXCEPT the experiment-root ``README.md`` (the prose being verified) and
   ``experiment.yml`` (this sidecar). Nothing else is excluded or added.
2. For each evidence file, take ``rel`` = its path relative to the experiment
   directory in POSIX form (``/`` separators, no leading ``./``), and ``digest`` =
   lowercase hex SHA-256 of the file's exact bytes.
3. Sort the ``(rel, digest)`` pairs by ``rel`` in Unicode code-point order
   (Python's default ``sorted`` on ``str``).
4. **Manifest** = for each pair, the line ``f"{digest}  {rel}\\n"`` (digest, exactly
   two spaces, rel, one newline), concatenated. This is GNU ``sha256sum`` output
   format with sorted, experiment-relative POSIX paths.
5. **Fingerprint** = ``"sha256:" + sha256(manifest.encode("utf-8")).hexdigest()``.

It is therefore: order-independent (sorted), location-independent (paths relative to
the experiment), encoding-explicit (UTF-8), and free of timestamps/locale. You can
reproduce it by hand: ``sha256sum`` each evidence file, rewrite paths relative to the
experiment with ``/``, sort by path, then ``sha256sum`` that manifest. ``arx
fingerprint <exp> --manifest`` prints the exact manifest so a mismatch is never a
mystery.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import _files

SIDECAR_NAME = "experiment.yml"

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
# fingerprint
# --------------------------------------------------------------------------- #
def evidence_files(exp_dir: Path) -> list[tuple[str, Path]]:
    """``(rel_posix, abs_path)`` for each evidence file under ``exp_dir`` — every
    indexable file except the root ``README.md`` and ``experiment.yml``."""
    exp_dir = exp_dir.resolve()
    out: list[tuple[str, Path]] = []
    for f in _files.iter_experiment_files(exp_dir):
        rel_parts: tuple[str, ...] = f["rel_parts"]
        name: str = f["filename"]
        if not rel_parts and (name.lower() == "readme.md" or name == SIDECAR_NAME):
            continue
        rel = f["abs_path"].relative_to(exp_dir).as_posix()
        out.append((rel, f["abs_path"]))
    return out


def compute_fingerprint(exp_dir: Path) -> tuple[str, int, str]:
    """Return ``(fingerprint, n_inputs, manifest)`` per the algorithm in the module
    docstring. ``manifest`` is the exact text that gets hashed (for transparency)."""
    entries = [(rel, _files.sha256_file(abs_path)) for rel, abs_path in evidence_files(exp_dir)]
    entries.sort(key=lambda e: e[0])  # Unicode code-point order on the POSIX rel path
    manifest = "".join(f"{digest}  {rel}\n" for rel, digest in entries)
    fingerprint = "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    return fingerprint, len(entries), manifest


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
    if prov is not None:
        if not isinstance(prov, dict):
            raise SidecarError("field 'provenance' must be a mapping")
        out["provenance"] = {k: prov[k] for k in ("reviewed_at", "data_fingerprint", "n_inputs")
                             if k in prov}
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


def stamp_provenance(data: dict[str, Any], exp_dir: Path, *, today: str | None = None) -> dict[str, Any]:
    """Set ``provenance`` to the current evidence fingerprint + a review date.

    ``today`` is taken from the caller (YYYY-MM-DD); if omitted, today's date is used.
    Returns a new dict; also returns the manifest via :func:`compute_fingerprint` if
    the caller wants to stash it. (Stamp this when the prose has been verified
    against the current data.)"""
    fingerprint, n_inputs, _ = compute_fingerprint(exp_dir)
    out = dict(data)
    out["provenance"] = {
        "reviewed_at": today or date.today().isoformat(),
        "data_fingerprint": fingerprint,
        "n_inputs": n_inputs,
    }
    return out
