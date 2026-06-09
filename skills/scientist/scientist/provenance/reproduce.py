"""Analysis reproduction audit — ``sci reproduce``.

The *executable* complement to :mod:`provenance.trace`. Where ``trace`` checks the DAG
*statically* (inputs exist, recorded shas still match) and runs nothing, this **re-runs
each experiment's** ``analysis/derive.py`` in the pinned environment and checks that the
regenerated ``analysis/tables|fig/*`` reproduce the recorded artifacts (within fit
tolerance), and that the derivation read only from ``data/``. It turns "the recipe sha
still matches" into "the recipe still produces the numbers."

## How it runs

The derivation is re-executed under :func:`grounding.audit_derivations`: its
``write_table``/``write_fig`` are redirected to a scratch directory (the recorded
``analysis/tables|fig/*`` are never touched), no provenance is written, and the claim-time
**bypass guard** stays live for the whole run — so an out-of-``data/`` read is flagged for
a derivation exactly as it is for a claim. We re-run ``derive.main()`` (the documented
entry point a derivation's ``main()`` writes through a ``derivation(study, __file__)``
context).

## Three independent verdicts per analysis

* **runs** — ``derive.main()`` executed without raising.
* **reproduces** — every recorded ``analysis/`` artifact regenerated to within tolerance.
* **reads_only_data** — every input the derivation read is the experiment's own ``data/``
  (plus ``experiment.yml`` config and the program convention/reference facts the
  canonicalization boundary uses); a read of ``raw/``, of a derived ``analysis/`` artifact,
  of another experiment's files, or any untracked read is flagged.

## Comparing artifacts

* **Tables (``.csv``)** — exact sha first (a deterministic table reproduces byte-for-byte);
  otherwise a numeric-tolerant cell-by-cell compare: identical columns + shape, numeric
  cells within ``rtol``/``atol`` (mirrors the ``pytest.approx`` convention claims use for
  Hill/EC50 fits; both-NaN counts as equal), non-numeric cells exact. Mismatches name the
  first differing cells.
* **Figures (``analysis/fig/*``)** — figures are **not** byte-compared. A PNG embeds
  matplotlib/freetype/libpng versions (and the data is already checked via the tables a
  figure is drawn from), so bytes differ across pinned-but-distinct environments without the
  numbers having moved. We instead confirm the figure **regenerated** and that its decoded
  pixel dimensions match the recorded figure (within a few px) — a tolerant structural check
  read straight from the PNG ``IHDR`` (stdlib only, no image library). A different format or
  an undecodable figure degrades to an existence-only "regenerated" verdict.

Pure-ish: reads the ledger + re-runs the recipe in-process. PyYAML + pandas (already the
analysis runtime); grounding/experiments are imported lazily to keep the provenance package's
top-level imports light (and avoid a cycle with grounding).
"""

from __future__ import annotations

import io
import math
import struct
import tempfile
import traceback
from pathlib import Path
from typing import Any

from . import _load_raw, edges

# A derived float reproduces if it matches within a small relative OR absolute tolerance
# (the absolute floor catches values near zero where a relative test is meaningless).
# These mirror the log/`pytest.approx` tolerance claims already use for fits.
DEFAULT_RTOL = 1e-3
DEFAULT_ATOL = 1e-6
# Figures: decoded pixel dimensions may shift by a hair across freetype versions under a
# "tight" bbox; allow a few px before calling it a structural mismatch.
FIG_DIM_TOL_PX = 4


# --------------------------------------------------------------------------- #
# recorded artifacts
# --------------------------------------------------------------------------- #
def _recorded_analysis_artifacts(exp: Path) -> dict[str, Path]:
    """Map ``analysis/...`` artifact rel-path -> on-disk file for every analysis edge in
    the ledger; fall back to scanning ``analysis/tables`` + ``analysis/fig`` on disk when
    the ledger records none (a derivation that was run but not committed)."""
    out: dict[str, Path] = {}
    for e in edges(_load_raw(exp), "analysis/"):
        art = str(e.get("artifact", ""))
        if art:
            out[art] = exp / art
    if not out:
        for sub in ("tables", "fig"):
            d = exp / "analysis" / sub
            if d.is_dir():
                for f in sorted(d.glob("*")):
                    if f.is_file():
                        out[f"analysis/{sub}/{f.name}"] = f
    return out


# --------------------------------------------------------------------------- #
# table comparison
# --------------------------------------------------------------------------- #
def _compare_table(regen: bytes, recorded_path: Path, rtol: float, atol: float) -> dict[str, Any]:
    """Compare a regenerated CSV (bytes) to the recorded CSV on disk. Exact sha wins;
    else a tolerant numeric compare. Returns ``{verdict, detail?}`` with verdict one of
    ``exact`` / ``approx`` / ``mismatch``."""
    import pandas as pd

    if not recorded_path.is_file():
        return {"verdict": "mismatch", "detail": f"recorded artifact missing on disk: {recorded_path.name}"}
    recorded = recorded_path.read_bytes()
    if regen == recorded:
        return {"verdict": "exact"}

    a = pd.read_csv(io.BytesIO(regen))
    b = pd.read_csv(io.BytesIO(recorded))
    if list(a.columns) != list(b.columns):
        return {"verdict": "mismatch",
                "detail": f"columns differ: {list(a.columns)} vs recorded {list(b.columns)}"}
    if a.shape != b.shape:
        return {"verdict": "mismatch",
                "detail": f"shape differs: {a.shape} vs recorded {b.shape}"}

    def _cell(v):                       # numpy scalar -> native python for a clean message
        return v.item() if hasattr(v, "item") else v

    diffs: list[str] = []
    for col in a.columns:
        s_new, s_old = a[col], b[col]
        both_numeric = (pd.api.types.is_numeric_dtype(s_new) and pd.api.types.is_numeric_dtype(s_old))
        for i in range(len(s_new)):
            x, y = s_new.iloc[i], s_old.iloc[i]
            if both_numeric:
                xn, yn = (x != x), (y != y)   # NaN check without importing numpy
                if xn and yn:
                    continue
                if xn or yn or not math.isclose(float(x), float(y), rel_tol=rtol, abs_tol=atol):
                    diffs.append(f"[{col}][{i}]: {_cell(x)!r} vs recorded {_cell(y)!r}")
            elif str(x) != str(y):
                diffs.append(f"[{col}][{i}]: {_cell(x)!r} vs recorded {_cell(y)!r}")
            if len(diffs) >= 5:
                break
        if len(diffs) >= 5:
            break
    if diffs:
        return {"verdict": "mismatch", "detail": "; ".join(diffs)}
    return {"verdict": "approx", "detail": f"within tolerance (rtol={rtol}, atol={atol})"}


# --------------------------------------------------------------------------- #
# figure comparison
# --------------------------------------------------------------------------- #
def _png_dims(b: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR chunk, or None if not a PNG. Stdlib only."""
    if len(b) < 24 or b[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        w, h = struct.unpack(">II", b[16:24])
        return int(w), int(h)
    except struct.error:
        return None


def _compare_fig(regen: bytes, recorded_path: Path) -> dict[str, Any]:
    """Confirm a figure regenerated and (for PNGs) that its pixel dimensions match the
    recorded figure within tolerance. Figures are deliberately not byte-compared (see the
    module docstring). Returns ``{verdict, detail?}`` with verdict ``exact`` /
    ``regenerated`` / ``mismatch``."""
    if not recorded_path.is_file():
        return {"verdict": "mismatch", "detail": f"recorded figure missing on disk: {recorded_path.name}"}
    recorded = recorded_path.read_bytes()
    if regen == recorded:
        return {"verdict": "exact"}
    if not regen:
        return {"verdict": "mismatch", "detail": "regenerated figure is empty"}
    new_dims, old_dims = _png_dims(regen), _png_dims(recorded)
    if new_dims and old_dims:
        if abs(new_dims[0] - old_dims[0]) <= FIG_DIM_TOL_PX and abs(new_dims[1] - old_dims[1]) <= FIG_DIM_TOL_PX:
            return {"verdict": "regenerated",
                    "detail": f"dims {new_dims[0]}x{new_dims[1]} ≈ recorded {old_dims[0]}x{old_dims[1]} "
                              f"(bytes differ; figures not byte-compared)"}
        return {"verdict": "mismatch",
                "detail": f"figure dims differ: {new_dims[0]}x{new_dims[1]} vs recorded {old_dims[0]}x{old_dims[1]}"}
    return {"verdict": "regenerated", "detail": "non-PNG figure; existence-only check (bytes not compared)"}


# --------------------------------------------------------------------------- #
# reads-only-data enforcement
# --------------------------------------------------------------------------- #
def _off_data_reads(inputs: list[dict], exp: Path, repo_root: Path) -> list[dict[str, str]]:
    """The derivation's reads that violate "read only from ``data/``". Allowed: the
    experiment's own ``data/`` files, its ``experiment.yml`` (``meta`` config), and the
    program convention/reference facts the canonicalization boundary uses (``reference``).
    Flagged: a read of ``raw/`` or a derived ``analysis/`` artifact, another experiment's
    files, or any untracked read the bypass guard caught."""
    data_dir = (exp / "data").resolve()
    flags: list[dict[str, str]] = []
    for inp in inputs:
        kind = str(inp.get("kind", ""))
        via = str(inp.get("via", ""))
        path = Path(inp["path"])
        ap = path if path.is_absolute() else (repo_root / path)
        try:
            under_data = ap.resolve().is_relative_to(data_dir)
        except (OSError, ValueError):
            under_data = False
        if kind in ("meta", "reference"):
            continue                              # config / program convention — allowed
        if kind == "data" and under_data:
            continue                              # the faithful data/ layer — the whole point
        if kind == "bypass" or via.startswith("bypass"):
            flags.append({"path": inp["path"], "reason": "untracked read (not via the tracked accessor)"})
        elif kind == "analysis":
            flags.append({"path": inp["path"], "reason": "reads a derived analysis/ artifact, not faithful data/"})
        elif not under_data:
            flags.append({"path": inp["path"], "reason": f"reads outside {exp.name}/data/"})
    return flags


# --------------------------------------------------------------------------- #
# the audit
# --------------------------------------------------------------------------- #
def reproduce(exp_dir: Path, repo_root: Path | None = None, *,
              rtol: float = DEFAULT_RTOL, atol: float = DEFAULT_ATOL) -> dict[str, Any]:
    """Re-run ``<exp>/analysis/derive.py`` and check it reproduces the recorded artifacts
    and read only from ``data/``.

    Returns ``{experiment, recipe, runs, reproduces, reads_only_data, artifacts,
    off_data_reads, error?, status}`` where ``status`` is ``REPRODUCES`` (all three
    verdicts hold) or ``BROKEN`` / ``NO-DERIVATION``. Each ``artifacts`` entry is
    ``{artifact, type, verdict, detail?}``. Pure re-run: writes only to a temp scratch dir;
    never mutates the experiment.
    """
    import os

    exp = Path(exp_dir).resolve()
    home = Path(repo_root).resolve() if repo_root is not None else exp.parent
    recipe = exp / "analysis" / "derive.py"
    result: dict[str, Any] = {
        "experiment": exp.name,
        "recipe": "analysis/derive.py",
        "runs": False,
        "reproduces": False,
        "reads_only_data": False,
        "artifacts": [],
        "off_data_reads": [],
    }
    if not recipe.is_file():
        result["status"] = "NO-DERIVATION"
        result["error"] = f"no analysis/derive.py for {exp.name}"
        return result

    from .. import grounding
    from .. import experiments as E

    # The derive.py resolves its own Study via SCIENTIST_HOME; point it at the repo root
    # so a synthetic/off-tree experiment audits without a globally-configured home.
    prev_home = os.environ.get("SCIENTIST_HOME")
    os.environ["SCIENTIST_HOME"] = str(home)
    try:
        code = exp.name.split(" ")[0].lower().replace("-", "_")   # "K1-000000 - Demo" -> k1_000000
        try:
            study = getattr(E, code)
            module = study.derive
        except Exception as e:                                    # noqa: BLE001 — surface as a clean verdict
            result["status"] = "BROKEN"
            result["error"] = f"could not load derive.py: {type(e).__name__}: {e}"
            return result
        main = getattr(module, "main", None)
        if not callable(main):
            result["status"] = "BROKEN"
            result["error"] = "analysis/derive.py defines no callable main()"
            return result

        with tempfile.TemporaryDirectory(prefix="sci-reproduce-") as scratch:
            with grounding.audit_derivations(Path(scratch)) as audit:
                try:
                    main()
                    result["runs"] = True
                except Exception:                                 # noqa: BLE001 — a failed re-run is a verdict
                    result["error"] = "derive.main() raised:\n" + traceback.format_exc()
            regenerated = {a["rel"]: a for a in audit.artifacts}
            captured_inputs = audit.inputs
    finally:
        if prev_home is None:
            os.environ.pop("SCIENTIST_HOME", None)
        else:
            os.environ["SCIENTIST_HOME"] = prev_home

    # --- reads-only-data ---
    off_data = _off_data_reads(captured_inputs, exp, home)
    result["off_data_reads"] = off_data
    result["reads_only_data"] = result["runs"] and not off_data

    # --- reproduces: compare every recorded artifact to its re-run ---
    recorded = _recorded_analysis_artifacts(exp)
    art_results: list[dict[str, Any]] = []
    all_reproduced = result["runs"] and bool(recorded)
    for rel in sorted(recorded):
        on_disk = recorded[rel]
        is_fig = "/fig/" in rel
        regen = regenerated.pop(rel, None)
        if regen is None:
            art_results.append({"artifact": rel, "type": "fig" if is_fig else "table",
                                "verdict": "not-regenerated",
                                "detail": "the re-run produced no artifact at this path"})
            all_reproduced = False
            continue
        cmp = (_compare_fig(regen["bytes"], on_disk) if is_fig
               else _compare_table(regen["bytes"], on_disk, rtol, atol))
        art_results.append({"artifact": rel, "type": "fig" if is_fig else "table", **cmp})
        if cmp["verdict"] == "mismatch":
            all_reproduced = False
    # artifacts the re-run produced that aren't recorded anywhere (a new, untracked output)
    for rel, a in sorted(regenerated.items()):
        art_results.append({"artifact": rel, "type": a["kind"] if a["kind"] != "table" else "table",
                            "verdict": "unrecorded",
                            "detail": "the re-run produced this but no analysis edge records it"})

    result["artifacts"] = art_results
    result["reproduces"] = bool(all_reproduced)
    result["status"] = ("REPRODUCES"
                        if (result["runs"] and result["reproduces"] and result["reads_only_data"])
                        else "BROKEN")
    return result


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
_VERDICT_MARK = {
    "exact": "✅ exact", "approx": "✅ approx", "regenerated": "✅ regenerated",
    "mismatch": "❌ MISMATCH", "not-regenerated": "❌ NOT REGENERATED",
    "unrecorded": "⚠️ unrecorded",
}


def render(result: dict[str, Any]) -> str:
    """Human-readable reproduction status for one experiment, matching the ``sci trace``
    / ``sci audit`` output style."""
    yn = lambda b: "yes" if b else "no"   # noqa: E731
    lines = [f"{result['experiment']}: {result['status']}"]
    lines.append(f"  recipe: {result['recipe']}")
    if result.get("error") and result["status"] in ("NO-DERIVATION",) or (
            result.get("error") and not result["runs"]):
        for ln in str(result["error"]).splitlines():
            lines.append(f"  ! {ln}")
        if result["status"] == "NO-DERIVATION":
            return "\n".join(lines)
    lines.append(f"  runs: {yn(result['runs'])} · reproduces: {yn(result['reproduces'])} "
                 f"· reads-only-data: {yn(result['reads_only_data'])}")
    for a in result["artifacts"]:
        mark = _VERDICT_MARK.get(a["verdict"], a["verdict"])
        kind = "fig" if a["type"] == "fig" else "table"
        line = f"  [{kind}] {a['artifact']}: {mark}"
        if a.get("detail") and a["verdict"] in ("mismatch", "not-regenerated", "unrecorded"):
            line += f"\n      ! {a['detail']}"
        elif a.get("detail"):
            line += f"  ({a['detail']})"
        lines.append(line)
    for f in result["off_data_reads"]:
        lines.append(f"  ! off-data read: {f['path']} — {f['reason']}")
    return "\n".join(lines)
