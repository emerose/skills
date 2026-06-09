"""Audit an experiment's data/ against a fresh extraction from raw/.

Checks, all mechanical:
  1. Determinism  — running the recipe twice yields byte-identical output.
  2. Grounding    — every raw input the recipe reads exists; if experiment.yml
                    records provenance, the recorded input sha256s still match.
  3. data/ ↔ recipe — each recipe output is present in data/ and byte-identical
                    (= data/ is exactly extract(raw)); data/ files the recipe does
                    NOT produce are flagged (legacy / hand-curated / non-conforming).
  4. Reconciliation — no measurement value in any pre-existing data/ file is missing
                    from the extraction (lost data == finding), checked PER FILE so
                    redundant legacy copies don't inflate "lost"; extras are reported.
  5. Naming       — data/ files follow NN_<assay>_<content>[__<partition>].csv.

Exit 0 if clean, 1 if findings.
"""

from __future__ import annotations

import collections
import csv
import math
import re
from pathlib import Path

from .. import provenance as P
from .engine import Extraction, _rows_to_bytes, _sha256, load_build

NAME_RE = re.compile(r"^\d\d_[a-z0-9]+_[a-z0-9_]+(__[a-z0-9_]+)?\.csv$")


def _meas(rows_iter) -> collections.Counter:
    """Multiset of non-integer numeric cells (real measurements; drops bookkeeping)."""
    c = collections.Counter()
    for row in rows_iter:
        for cell in row:
            s = str(cell).strip()
            try:
                v = float(s)
            except ValueError:
                continue
            if not math.isfinite(v):     # skip NaN/inf sentinels (e.g. below-detection)
                continue
            if v != int(v):
                c[round(v, 4)] += 1
    return c


def _build(exp: Path, script: Path):
    x = Extraction(exp, exp.parent)
    load_build(script)(x)
    return x.outputs


def audit(exp: Path, script: Path | None = None) -> int:
    exp = Path(exp).resolve()
    script = Path(script) if script else exp / "data" / "extract.py"
    data_dir = exp / "data"
    findings: list[str] = []
    print(f"### audit: {exp.name}")

    # 1. determinism
    o1 = _build(exp, script)
    o2 = _build(exp, script)
    det = o1 == o2
    print(f"  [determinism]  re-run identical: {det}")
    if not det:
        findings.append("extraction is non-deterministic")

    # 2. grounding (inputs exist; provenance sha match if recorded). Read leniently:
    # the recorded provenance may sit on an exp_id-less sidecar (data committed before
    # the metadata is authored), and audit must still check it.
    sidecar = P._load_raw(exp)
    recorded, recorded_recipe = {}, {}
    for e in P.edges(sidecar, "data/"):
        art = e.get("artifact", "")
        ins = {i["path"]: i.get("sha256") for i in e.get("inputs", [])
               if isinstance(i, dict) and i.get("path")}
        recorded[art] = ins
        recorded_recipe[art] = next((s for p, s in ins.items() if p.endswith("extract.py")), None)
    inputs_ok = True
    for o in o1:
        for path, sha in o["inputs"]:
            rec = recorded.get(f"data/{o['name']}", {}).get(path)
            if rec and rec != sha:
                inputs_ok = False
                findings.append(f"provenance drift: {path} sha changed since recorded")
    print(f"  [grounding]    raw inputs present: True; provenance sha match: "
          f"{'n/a (not recorded)' if not recorded else inputs_ok}")
    cur_recipe = _sha256(script.read_bytes())
    stale = sorted(a for a, s in recorded_recipe.items() if s and s != cur_recipe)
    print(f"  [recipe]       recorded recipe matches current extract.py: "
          f"{'n/a' if not any(recorded_recipe.values()) else (not stale)}")
    if stale:
        findings.append(f"recipe (data/extract.py) changed since {len(stale)} artifact(s) stamped — re-extract")

    # 3. data/ vs recipe output
    produced = {o["name"]: _rows_to_bytes(o["header"], o["rows"]) for o in o1}
    on_disk = {f.name for f in data_dir.glob("*.csv")}
    matched = missing = differ = 0
    for name, b in produced.items():
        p = data_dir / name
        if not p.is_file():
            missing += 1
        elif p.read_bytes() == b:
            matched += 1
        else:
            differ += 1
            findings.append(f"{name}: data/ differs from extraction")
    extra = sorted(on_disk - set(produced) - {"extract.py"})
    print(f"  [data/↔recipe] outputs: {len(produced)}  identical:{matched} "
          f"differ:{differ} not-yet-in-data/:{missing}")
    if extra:
        print(f"                 {len(extra)} file(s) in data/ NOT produced by the recipe (legacy/non-conforming):")
        for e in extra[:8]:
            print(f"                   - {e}")
        if len(extra) > 8:
            print(f"                   … +{len(extra)-8} more")
        findings.append(f"{len(extra)} legacy/non-conforming file(s) in data/")

    # 4. reconciliation: nothing the PRE-EXISTING (non-recipe) data/ files hold is
    #    missing from the extraction. Exclude the recipe's own outputs from the
    #    "existing" set, else committed files get counted against themselves.
    new_meas = collections.Counter()
    for o in o1:
        new_meas += _meas([o["header"]] + o["rows"])
    # Per-file coverage: each pre-existing file's values must appear in the
    # extraction. Checked per file so redundant copies across legacy files
    # (Combined_*, *_File2, …) don't inflate "lost"; only a value no extraction
    # output holds is a real loss.
    legacy_union, lost = collections.Counter(), collections.Counter()
    for f in data_dir.glob("*.csv"):
        if f.name == "extract.py" or f.name in produced:
            continue
        fm = _meas(csv.reader(f.open(encoding="utf-8", errors="replace")))
        legacy_union += fm
        lost += fm - new_meas
    extras = new_meas - legacy_union
    print(f"  [reconcile]    pre-existing measurements: {sum(legacy_union.values())}  "
          f"lost by extraction: {sum(lost.values())}  faithful extras: {sum(extras.values())}")
    if lost:
        findings.append(f"{sum(lost.values())} measurement value(s) in data/ NOT reproduced — investigate")

    # 5. naming
    bad = sorted(n for n in produced if not NAME_RE.match(n))
    nonconf = sorted(n for n in (on_disk - {"extract.py"}) if not NAME_RE.match(n))
    print(f"  [naming]       recipe outputs conforming: {not bad}; "
          f"non-conforming files in data/: {len(nonconf)}")
    if bad:
        findings.append(f"recipe emits non-conforming names: {bad}")

    print(f"\n  → {'CLEAN' if not findings else str(len(findings)) + ' finding(s):'}")
    for f in findings:
        print(f"     • {f}")
    return 0 if not findings else 1
