"""kicho — typed, tracked access to an experiment's tidy data.

    from kicho import k1_210701 as k
    k.qpcr_summary          # data/02_qpcr_summary.csv as a DataFrame (sha-pinned)
    k.meta                  # experiment.yml as a dict
    k.analysis.ec50_by_aso  # analysis/tables/ec50_by_aso.csv (a derived output)

A ``k1_NNNNNN`` attribute resolves to the experiment folder ``K1-NNNNNN *`` under
``$KICHO_ROOT`` (the kicho-science checkout) and returns a :class:`Study`. Tidy tables
under ``data/`` become attributes: ``NN_<assay>_<content>.csv`` -> drop the ``NN_``
prefix and ``.csv`` -> ``k.<assay>_<content>``. Access is lazy, cached, and sha-pinned,
and **every access is recorded as provenance** through ``analyst.record`` — so a claim
or derivation that touches ``k.qpcr_summary`` has that input captured automatically.

This module is the one tracked accessor the spec calls for: it knows nothing about
claims; it just loads tables and announces what it loaded. The recording is a no-op
when no capture is active (plain IPython use), so ``k.qpcr_summary`` works the same in
a notebook and inside a claim.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import analyst

__all__ = ["Study", "root", "resolve"]

_STUDY_RE = re.compile(r"^k1_\d{6}$", re.IGNORECASE)


def root() -> Path:
    r = os.environ.get("KICHO_ROOT")
    if not r:
        raise RuntimeError(
            "KICHO_ROOT is not set — point it at the '05 - Scientific Data' checkout.")
    p = Path(r)
    if not p.is_dir():
        raise RuntimeError(f"KICHO_ROOT does not exist: {p}")
    return p


def resolve(exp_id: str) -> Path:
    """``k1_210701`` -> the ``K1-210701 *`` folder under KICHO_ROOT (glob on the id)."""
    code = exp_id.upper().replace("_", "-")           # k1_210701 -> K1-210701
    matches = sorted(root().glob(f"{code} *")) + [p for p in [root() / code] if p.is_dir()]
    matches = [m for m in matches if m.is_dir()]
    if not matches:
        raise FileNotFoundError(f"no experiment folder for {code} under {root()}")
    if len(matches) > 1:
        raise FileNotFoundError(f"ambiguous: {code} matches {[m.name for m in matches]}")
    return matches[0]


def _attr_for(csv_name: str) -> str:
    """``02_qpcr_summary.csv`` -> ``qpcr_summary`` (drop NN_ prefix and extension)."""
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
        ``k.derive.per_animal_ube3a(k)``. Cached per process."""
        import importlib.util
        import sys
        name = f"kicho_derive_{self.id.replace('-', '_')}"
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


# Module-level attribute access (PEP 562): `from kicho import k1_210701`.
_studies: dict[str, Study] = {}


def __getattr__(name: str):
    if _STUDY_RE.match(name):
        if name not in _studies:
            _studies[name] = Study(name)
        return _studies[name]
    raise AttributeError(f"module 'kicho' has no attribute '{name}'")


def __dir__():
    return __all__ + sorted(_studies)
