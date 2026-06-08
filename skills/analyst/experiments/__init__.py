"""experiments — typed, tracked access to an experiment's tidy data.

    from experiments import k1_000000 as k
    k.assay_summary          # data/02_assay_summary.csv as a DataFrame (sha-pinned)
    k.meta                  # experiment.yml as a dict
    k.analysis.ec50_summary  # analysis/tables/ec50_summary.csv (a derived output)

A ``k1_NNNNNN`` attribute resolves to the experiment folder ``K1-NNNNNN *`` under
``$EXPERIMENTS_ROOT`` (the scientific-data checkout) and returns a :class:`Study`. Tidy tables
under ``data/`` become attributes: ``NN_<assay>_<content>.csv`` -> drop the ``NN_``
prefix and ``.csv`` -> ``k.<assay>_<content>``. Access is lazy, cached, and sha-pinned,
and **every access is recorded as provenance** through ``analyst.record`` — so a claim
or derivation that touches ``k.assay_summary`` has that input captured automatically.

This module is the one tracked accessor the spec calls for: it knows nothing about
claims; it just loads tables and announces what it loaded. The recording is a no-op
when no capture is active (plain IPython use), so ``k.assay_summary`` works the same in
a notebook and inside a claim.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import analyst

__all__ = ["Study", "Program", "program", "canonical_aso", "root", "resolve"]

_STUDY_RE = re.compile(r"^k1_\d{6}$", re.IGNORECASE)
# An ASO reference, possibly with a CRO client-id prefix: '[ASO][ _-]<client>[_-]<id>' or
# '[ASO][ _-]<id>'. The trailing number is the canonical id; leading zeros are dropped.
_ASO_RE = re.compile(r"(?:ASO)?\s*[ _-]*(?:\d+[_-])?0*(\d+)\s*$", re.IGNORECASE)


def root() -> Path:
    r = os.environ.get("EXPERIMENTS_ROOT")
    if not r:
        raise RuntimeError(
            "EXPERIMENTS_ROOT is not set — point it at the '05 - Scientific Data' checkout.")
    p = Path(r)
    if not p.is_dir():
        raise RuntimeError(f"EXPERIMENTS_ROOT does not exist: {p}")
    return p


def resolve(exp_id: str) -> Path:
    """``k1_000000`` -> the ``K1-000000 *`` folder under EXPERIMENTS_ROOT (glob on the id)."""
    code = exp_id.upper().replace("_", "-")           # k1_000000 -> K1-000000
    matches = sorted(root().glob(f"{code} *")) + [p for p in [root() / code] if p.is_dir()]
    matches = [m for m in matches if m.is_dir()]
    if not matches:
        raise FileNotFoundError(f"no experiment folder for {code} under {root()}")
    if len(matches) > 1:
        raise FileNotFoundError(f"ambiguous: {code} matches {[m.name for m in matches]}")
    return matches[0]


def _attr_for(csv_name: str) -> str:
    """``02_assay_summary.csv`` -> ``assay_summary`` (drop NN_ prefix and extension)."""
    stem = Path(csv_name).stem
    return re.sub(r"^\d+_", "", stem)


class _AnalysisNS:
    """``k.analysis.<name>`` -> ``analysis/tables/<name>.csv`` (derived outputs),
    loaded through the same tracked accessor (recorded with kind='analysis')."""

    def __init__(self, study: "Study"):
        self._study = study
        self._dir = study.path / "analysis" / "tables"

    def _files(self) -> dict[str, Path]:
        if not self._dir.is_dir():
            return {}
        return {_attr_for(p.name): p for p in sorted(self._dir.glob("*.csv"))}

    def __dir__(self):
        return list(self._files())

    def __getattr__(self, name: str):
        files = self._files()
        if name not in files:
            raise AttributeError(
                f"no derived table '{name}' for {self._study.id} "
                f"(have: {sorted(files)}; run analysis/derive.py first)")
        return self._study._load(files[name], kind="analysis")


class Study:
    """A handle to one experiment, exposing its tidy ``data/`` tables as DataFrame
    attributes. Loads are cached (per process) but provenance is recorded on *every*
    access, so each claim that touches a table captures it independently."""

    def __init__(self, exp_id: str):
        self.id = exp_id.upper().replace("_", "-")
        self.path = resolve(exp_id)
        self._cache: dict[str, tuple] = {}    # resolved path -> (df, sha)

    # --- table discovery ---
    def _data_files(self) -> dict[str, Path]:
        d = self.path / "data"
        if not d.is_dir():
            return {}
        return {_attr_for(p.name): p for p in sorted(d.glob("*.csv"))}

    def __dir__(self):
        return sorted(set(list(self._data_files()) + ["meta", "analysis", "id", "path"]))

    # --- tracked load (records on every call, even on a cache hit) ---
    def _load(self, path: Path, kind: str = "data"):
        key = str(path)
        if key in self._cache:
            df, sha = self._cache[key]
            analyst.record(kind, path, sha)   # re-record for this capture
            return df
        df = analyst.load(path, kind=kind)    # reads bytes, sha-pins, records, sets .attrs
        self._cache[key] = (df, df.attrs["sha256"])
        return df

    @property
    def meta(self) -> dict:
        """``experiment.yml`` as a dict (recorded as a provenance input)."""
        import yaml
        p = self.path / "experiment.yml"
        sha = analyst._sha256(p.read_bytes())
        analyst.record("meta", p, sha)
        return yaml.safe_load(p.read_text(encoding="utf-8"))

    @property
    def analysis(self) -> _AnalysisNS:
        return _AnalysisNS(self)

    @property
    def derive(self):
        """The experiment's ``analysis/derive.py`` as a module, loaded under a unique
        name so multiple experiments' derive.py files never collide in ``sys.modules``
        (each is named ``derive``). Lets a claim reuse derivation helpers safely:
        ``k.derive.per_animal_target(k)``. Cached per process."""
        import importlib.util
        import sys
        name = f"experiments_derive_{self.id.replace('-', '_')}"
        if name in sys.modules:
            return sys.modules[name]
        p = self.path / "analysis" / "derive.py"
        if not p.is_file():
            raise AttributeError(f"{self.id} has no analysis/derive.py")
        spec = importlib.util.spec_from_file_location(name, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    def __getattr__(self, name: str):
        # only reached for names not found normally
        files = self._data_files()
        if name in files:
            return self._load(files[name], kind="data")
        raise AttributeError(
            f"{self.id} has no table '{name}' (have: {sorted(files)})")

    def __repr__(self) -> str:
        return f"<Study {self.id} @ {self.path.name}>"


class Program:
    """Cross-experimental reference facts + the home for program-level claims:
    ``$EXPERIMENTS_ROOT/program/``. The *contents* (entity registries, naming conventions,
    program constants) are program-specific and live in the data repo; this accessor is
    generic. Reads route through the tracked loader, so referencing a program fact from a
    claim/derivation is captured as provenance like any other input.

        from experiments import program
        program.asos                      # program/asos.csv — the molecule registry
        program.conventions               # program/conventions.yml — naming rules, constants
        program.canonical("ASO3607_154")  # -> 154  (resolve an alias to the canonical id)

    ``program/claims/test_*.py`` is the natural home for grounded cross-cutting claims
    (e.g. a lead/backup assessment) — collected by the same pytest plugin as any claims dir."""

    def __init__(self):
        self.path = root() / "program"

    @property
    def conventions(self) -> dict:
        """``program/conventions.yml`` as a dict (recorded as a provenance input)."""
        import yaml
        p = self.path / "conventions.yml"
        analyst.record("reference", p, analyst._sha256(p.read_bytes()))
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    def table(self, name: str):
        """A reference table ``program/<name>[.csv]`` as a tracked DataFrame."""
        p = self.path / (name if name.endswith(".csv") else f"{name}.csv")
        return analyst.load(p, kind="reference")

    @property
    def asos(self):
        """``program/asos.csv`` — the ASO molecule registry."""
        return self.table("asos")

    def canonical(self, name) -> int | None:
        """Resolve an ASO alias to its canonical numeric Kicho id, applying the program's
        documented naming convention (``conventions.yml: aso_naming``) — the CRO client-id
        prefix to strip and any explicit overrides for the cases the rule misses. Falls back
        to the bare "trailing ASO number" rule if no conventions file is present."""
        s = str(name).strip()
        try:
            conv = (self.conventions or {}).get("aso_naming", {}) or {}
        except (FileNotFoundError, OSError):
            conv = {}
        overrides = conv.get("overrides") or {}
        if s in overrides:
            return int(overrides[s])
        m = _ASO_RE.search(s)
        return int(m.group(1)) if m else None


# Module-level attribute access (PEP 562): `from experiments import k1_000000`, `program`.
_studies: dict[str, Study] = {}
_program: Program | None = None


def _get_program() -> Program:
    global _program
    if _program is None:
        _program = Program()
    return _program


def canonical_aso(name) -> int | None:
    """Module-level shortcut for ``program.canonical(name)`` — resolve an ASO alias
    (``ASO-154``, ``ASO 154``, ``ASO3607_154``, …) to its canonical numeric id."""
    return _get_program().canonical(name)


def __getattr__(name: str):
    if _STUDY_RE.match(name):
        if name not in _studies:
            _studies[name] = Study(name)
        return _studies[name]
    if name == "program":            # `from experiments import program` -> the Program accessor
        return _get_program()
    raise AttributeError(f"module 'experiments' has no attribute '{name}'")


def __dir__():
    return __all__ + sorted(_studies)
