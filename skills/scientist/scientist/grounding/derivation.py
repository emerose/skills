"""scientist.grounding.derivation — analysis-provenance recorder + the audit harness.

Two cooperating pieces:

* the **derivation recorder** (:class:`Derivation` / :func:`derivation`): a context manager
  for one analysis derivation. Inside the block every table read via ``experiments`` is
  captured as an input; ``write_table``/``write_fig`` write the artifact under ``analysis/``
  and record a provenance entry (artifact + sha, inputs = the captured data files + the
  deriving recipe) into the experiment's unified ``provenance`` list via
  :func:`provenance.record_provenance` — the SAME ledger writer the extractor's ``data/``
  edges use, so ``raw -> data -> analysis`` is one DAG in one place.

* the **derivation audit** (:class:`DerivationAudit` / :func:`audit_derivations` /
  :func:`current_audit`): when an audit context is active, a ``derivation()`` opened inside
  it runs in *audit mode* — its writes go to a scratch directory (never over the recorded
  ``analysis/tables|fig/*``), it records NO provenance into experiment.yml, and every
  artifact it produces — plus the inputs its capture saw — is collected on the
  ``DerivationAudit`` for the reproduction check (``provenance.reproduce``). The claim-time
  bypass guard is thus extended to derivations: the capture is live during the re-run, so an
  out-of-``data/`` read is flagged exactly as it is for a claim.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..provenance import record_provenance as _record_provenance
from ._text import _sha256
from grounding import install_guard


# --------------------------------------------------------------------------- #
# Derivation audit — re-run a derivation without touching the recorded artifacts.
# --------------------------------------------------------------------------- #
@dataclass
class DerivationAudit:
    """Collects the artifacts + captured inputs of every derivation re-run under it.

    ``artifacts`` — one ``{rel, kind, name, bytes}`` per ``write_table``/``write_fig``
    (``rel`` is the experiment-relative ``analysis/tables|fig/<name>`` path, matching the
    ledger's artifact key; ``bytes`` is the regenerated content for comparison).
    ``inputs`` — the merged, de-duplicated capture inputs across all blocks (the basis for
    the reads-only-``data/`` enforcement)."""

    scratch: Path
    artifacts: list[dict] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    _seen_inputs: set = field(default_factory=set)

    def _record_input(self, inp: dict) -> None:
        key = (inp.get("kind"), inp.get("path"))
        if key in self._seen_inputs:
            return
        self._seen_inputs.add(key)
        self.inputs.append(dict(inp))


_AUDIT: contextvars.ContextVar[DerivationAudit | None] = contextvars.ContextVar(
    "derivation_audit", default=None)


def current_audit() -> DerivationAudit | None:
    return _AUDIT.get()


class audit_derivations:
    """Context manager that puts derivations into *audit mode* (see above). Use as::

        with grounding.audit_derivations(scratch_dir) as audit:
            derive_module.main()          # its derivation() re-runs into scratch
        audit.artifacts, audit.inputs     # regenerated outputs + captured reads

    Returns a fresh :class:`DerivationAudit`; nesting is not supported (one audit at a
    time per execution context)."""

    def __init__(self, scratch: Path):
        self.audit = DerivationAudit(scratch=Path(scratch))
        self._tok = None

    def __enter__(self) -> DerivationAudit:
        self._tok = _AUDIT.set(self.audit)
        return self.audit

    def __exit__(self, *exc) -> None:
        _AUDIT.reset(self._tok)


# --------------------------------------------------------------------------- #
# Derivation recorder — analysis provenance, parallel to extraction provenance.
# --------------------------------------------------------------------------- #
class Derivation:
    """Context manager for an analysis derivation.

    Inside the ``with`` block, every table read via ``experiments`` is captured as an input.
    ``write_table``/``write_fig`` write the artifact under ``analysis/`` and record a
    provenance entry (artifact + sha, inputs = the captured data files + the deriving
    recipe) into the experiment's unified ``provenance`` list via
    :func:`provenance.record_provenance` — the SAME ledger writer the extractor's
    ``data/`` edges use, so ``raw -> data -> analysis`` is one DAG in one place.
    """

    def __init__(self, study, recipe):
        from . import Capture

        self.study = study
        self.exp = Path(study.path)
        self.recipe = Path(recipe).resolve()
        self.cap = Capture(claim_id=f"derive:{study.id}")
        self.entries: list[dict] = []
        self._tok = None
        # If an audit context is active, run in audit mode: write to its scratch dir,
        # record no provenance, and hand the regenerated artifacts + captured inputs to it.
        self.audit = _AUDIT.get()

    def __enter__(self) -> "Derivation":
        from . import _CURRENT

        install_guard()
        self._tok = _CURRENT.set(self.cap)
        base = (self.audit.scratch if self.audit is not None else self.exp / "analysis")
        self._tables_dir = base / "tables"
        self._fig_dir = base / "fig"
        self._tables_dir.mkdir(parents=True, exist_ok=True)
        self._fig_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *exc) -> None:
        from . import _CURRENT

        _CURRENT.reset(self._tok)
        if self.audit is not None:
            # Audit mode: surface the captured inputs (for the reads-only-data check);
            # never write provenance into experiment.yml.
            if exc[0] is None:
                for inp in self.cap.inputs:
                    self.audit._record_input(inp)
            return
        if exc[0] is None and self.entries:
            self._write_provenance()

    # --- writers ---
    def write_table(self, name: str, df, **to_csv_kw) -> Path:
        """Write a derived table to ``analysis/tables/<name>`` and record provenance.
        ``index=False`` by default for stable, diffable output. Under an audit context
        the write is redirected to the scratch dir and recorded for comparison instead."""
        out = self._tables_dir / name
        to_csv_kw.setdefault("index", False)
        df.to_csv(out, **to_csv_kw)
        self._after_write(out, "table", name)
        return out

    def write_fig(self, name: str, fig) -> Path:
        """Save a matplotlib figure to ``analysis/fig/<name>`` and record provenance.
        Under an audit context the write is redirected to scratch and recorded instead."""
        out = self._fig_dir / name
        fig.savefig(out, dpi=120, bbox_inches="tight")
        self._after_write(out, "fig", name)
        return out

    def _after_write(self, out: Path, kind: str, name: str) -> None:
        """Record a just-written artifact: into the live audit (re-run, for comparison)
        or as a provenance entry (normal derivation)."""
        if self.audit is not None:
            sub = "tables" if kind == "table" else "fig"
            self.audit.artifacts.append({
                "rel": f"analysis/{sub}/{name}", "kind": kind,
                "name": name, "bytes": out.read_bytes(),
            })
        else:
            self._record_artifact(out)

    def _rel(self, p: Path) -> str:
        # Resolve BOTH sides (realpath) before relative_to: when the data-repo root is
        # reached through a symlink (e.g. macOS /tmp -> /private/var), resolving only the
        # path leaves the two prefixes mismatched and relative_to falls back to an
        # absolute path. Resolving both keeps recorded input paths repo-relative.
        try:
            return str(p.resolve().relative_to(self.exp.parent.resolve()))
        except ValueError:
            return str(p)

    def _record_artifact(self, out: Path) -> None:
        recipe_in = {"path": self._rel(self.recipe), "sha256": _sha256(self.recipe.read_bytes())}
        inputs = [{"path": self._rel(Path(i["path"])), "sha256": i["sha256"]}
                  for i in self.cap.inputs] + [recipe_in]
        self.entries.append({
            "artifact": f"analysis/{out.relative_to(self.exp / 'analysis')}".replace("\\", "/"),
            "artifact_sha256": _sha256(out.read_bytes()),
            "reviewed_at": date.today().isoformat(),
            "inputs": inputs,
        })

    def _write_provenance(self) -> None:
        # Route through the shared ledger writer: it dedups by artifact, preserves
        # entries for OTHER artifacts (data/ extractions, the README review), and
        # writes the deterministic sidecar — identical merge semantics this method
        # used to reimplement, now in one place.
        _record_provenance(self.exp, self.entries, repo_root=self.exp.parent)


def derivation(study, recipe) -> Derivation:
    """Open a :class:`Derivation` for ``study`` whose recipe is ``recipe`` (pass
    ``__file__`` from the derive.py). Use as ``with derivation(k, __file__) as d:``."""
    return Derivation(study, recipe)
