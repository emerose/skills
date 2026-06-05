#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["openpyxl>=3.1", "pyyaml>=6.0", "xlrd>=2.0"]
# ///
"""Comprehensive cell-coverage check used to justify deleting legacy data/ CSVs.

Where the audit's `reconcile` counts only non-integer numerics (real measurements),
this checks EVERY cell — integers AND text. For one experiment: run its
data/extract.py recipe (in-memory) and build the multiset of every cell value it
PRODUCES. Then, for every legacy data/*.csv the recipe does NOT produce, count cells
whose normalized value is absent from the produced universe = would-be-lost-on-delete.

Normalization: numbers → float (so 11 == 11.0), text → casefold, comma-decimals
handled, dates compared by value; the header row of each legacy file and blank/NA
cells are skipped.

This proves *value* coverage — no value disappears, modulo shape (see
references/recipes.md "information-coverage, not byte-coverage"). Run it before
deleting any legacy file; CLEAN (0 uncovered) is the bar. Residuals it reports are
either real loss (fix the recipe) or shape/redundancy/export artifacts you must
confirm recoverable by hand.

Exit 0 if every legacy file is fully covered, 1 if any cell is uncovered. Usage:
    cellcov.py "<exp dir>" [--script PATH] [--examples N]
"""
from __future__ import annotations
import argparse, csv, collections, math, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract as EX  # reuse the extraction engine  # noqa: E402  # type: ignore[import-not-found]

_NA = {"", "nan", "none", "na", "n/a", "#n/a", "null", "."}


def norm(s):
    """Normalize a cell to a comparable key, or None to skip (blank/NA)."""
    t = str(s).strip()
    if t.casefold() in _NA:
        return None
    cand = t.replace(",", ".") if ("," in t and "." not in t) else t
    try:
        f = float(cand)
        if math.isfinite(f):
            return ("n", round(f, 6))
    except ValueError:
        pass
    return ("t", t.casefold())


def _cells(path: Path, skip_header: bool) -> collections.Counter:
    c = collections.Counter()
    rows = list(csv.reader(path.open(encoding="utf-8", errors="replace")))
    for row in rows[(1 if skip_header else 0):]:
        for cell in row:
            n = norm(cell)
            if n is not None:
                c[n] += 1
    return c


def _build(exp: Path, script: Path):
    x = EX.Extraction(exp, exp.parent)
    EX._load_build(script)(x)
    return x.outputs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exp")
    ap.add_argument("--script")
    ap.add_argument("--examples", type=int, default=8,
                    help="show up to N example uncovered values per file (0 = none)")
    args = ap.parse_args()
    exp = Path(args.exp).resolve()
    script = Path(args.script) if args.script else exp / "data" / "extract.py"
    data = exp / "data"

    outputs = _build(exp, script)
    produced = {o["name"] for o in outputs}

    # The universe of every cell the recipe produces (header rows included — they hold
    # legitimate values like column-derived labels).
    universe = collections.Counter()
    for o in outputs:
        for row in [o["header"]] + o["rows"]:
            for cell in row:
                n = norm(cell)
                if n is not None:
                    universe[n] += 1

    legacy = sorted(f for f in data.glob("*.csv")
                    if f.name not in produced and f.name != "extract.py")
    total_lost = 0
    report = []
    for f in legacy:
        fc = _cells(f, skip_header=True)
        lost = collections.Counter()
        for val, k in fc.items():
            short = max(0, k - universe.get(val, 0))
            if short:
                lost[val] += short
        n = sum(lost.values())
        total_lost += n
        if n:
            kinds = collections.Counter(v[0] for v in lost)
            ex = ", ".join(f"{v[1]!r}x{c}" for v, c in lost.most_common(args.examples))
            report.append(
                f"  {f.name}: {n} uncovered (num={kinds.get('n', 0)} txt={kinds.get('t', 0)})"
                + (f"\n      {ex}" if args.examples else ""))

    tag = "CLEAN" if total_lost == 0 else f"{total_lost} UNCOVERED across {len(report)} file(s)"
    print(f"### cellcov: {exp.name}: {tag}  (legacy files: {len(legacy)}, produced: {len(produced)})")
    for r in report:
        print(r)
    return 0 if total_lost == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
