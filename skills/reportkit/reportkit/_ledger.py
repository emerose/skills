"""Read-only primitives for the ``experiment.yml`` provenance sidecar.

The generic report engine grounds an embedded figure/table against a *recorded* analysis
artifact: it must read the sidecar's ``provenance`` ledger (which artifact has which
``artifact_sha256``) and hash the on-disk file to detect drift. Those three mechanical
steps — hash a file, parse the sidecar to a raw dict, and pull the provenance edges — are
the whole of what the report/trace engine needs from the ledger, so they live here as a
pure, dependency-free leaf.

The *authoring* side of the ledger (schema validation, deterministic write-out, the
README review + staleness model) is a superset built on top of these primitives and lives
in the host skill (``scientist.provenance``), which re-exports these names so its own
surface is unchanged. Keeping the read primitives here is what lets ``reportkit`` audit a
report without importing the host skill.

Stdlib + PyYAML only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

SIDECAR_NAME = "experiment.yml"


class SidecarError(ValueError):
    """experiment.yml failed to parse/validate — message names the file and the problem."""


def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file's bytes (hex)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


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


def edges(sidecar: dict[str, Any], prefix: str | None = None) -> list[dict]:
    """Provenance entries from a sidecar, optionally filtered by artifact prefix
    (``'data/'``, ``'analysis/'``, ``'README'``). ``None`` returns all entries."""
    prov = [e for e in (sidecar.get("provenance") or []) if isinstance(e, dict)]
    if prefix is None:
        return prov
    return [e for e in prov if str(e.get("artifact", "")).startswith(prefix)]
