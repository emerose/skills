#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["openpyxl>=3.1", "pyyaml>=6.0", "xlrd>=2.0", "python-docx>=1.1", "pdfplumber>=0.11"]
# ///
"""scientist CLI — zero-install entry point for the extraction stage.

Runnable directly with uv (PEP 723 deps inline), no virtualenv:

    uv run skills/scientist/scripts/sci.py extract "<exp dir>"            # dry run → data/_preview/
    uv run skills/scientist/scripts/sci.py extract "<exp dir>" --commit   # write data/*.csv + provenance
    uv run skills/scientist/scripts/sci.py audit   "<exp dir>"            # re-extract, check data/ vs raw/
    uv run skills/scientist/scripts/sci.py cellcov "<exp dir>"            # full cell-coverage of legacy CSVs

`extract`'s recipe lives at <exp>/data/extract.py and defines build(x); see the
extraction package and references/extract.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Put skills/scientist (the package root) onto sys.path so the flat top-level
# packages (provenance, labfiles, extraction) import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extraction as EXT  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(prog="sci", description="scientist extraction CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ex = sub.add_parser("extract", help="(re)generate data/*.csv from raw/ via the recipe")
    p_ex.add_argument("exp", help="experiment folder (path)")
    p_ex.add_argument("--script", help="extract.py to run (default <exp>/data/extract.py)")
    p_ex.add_argument("--commit", action="store_true",
                      help="write data/*.csv + experiment.yml provenance")
    p_ex.add_argument("--preview", help="dry-run output dir (default <exp>/data/_preview)")

    p_au = sub.add_parser("audit", help="re-extract and check data/ against raw/")
    p_au.add_argument("exp", help="experiment folder (path)")
    p_au.add_argument("--script", help="extract.py to run (default <exp>/data/extract.py)")

    p_cc = sub.add_parser("cellcov", help="full cell-coverage check of legacy data/ CSVs")
    p_cc.add_argument("exp", help="experiment folder (path)")
    p_cc.add_argument("--script", help="extract.py to run (default <exp>/data/extract.py)")
    p_cc.add_argument("--examples", type=int, default=8,
                      help="show up to N example uncovered values per file (0 = none)")

    args = ap.parse_args()

    if args.cmd == "extract":
        EXT.extract(args.exp, script=args.script, commit=args.commit, preview=args.preview)
        return 0
    if args.cmd == "audit":
        return EXT.audit(args.exp, args.script)
    if args.cmd == "cellcov":
        return EXT.cellcov(args.exp, args.script, args.examples)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
