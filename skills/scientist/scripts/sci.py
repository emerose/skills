#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "openpyxl>=3.1",
#   "pyyaml>=6.0",
#   "xlrd>=2.0",
#   "python-docx>=1.1",
#   "pdfplumber>=0.11",
#   "libkit>=0.2.3",
#   "platformdirs>=4.0",
#   "markdown>=3.5",
#   "xhtml2pdf>=0.2.16",
# ]
# ///
"""scientist CLI — zero-install entry point for the whole skill.

Runnable directly with uv (PEP 723 deps inline), no virtualenv. Two families of
subcommands share one tool:

Extraction (operates on an experiment folder's data/ ↔ raw/):

    uv run skills/scientist/scripts/sci.py extract "<exp dir>"            # dry run → data/_preview/
    uv run skills/scientist/scripts/sci.py extract "<exp dir>" --commit   # write data/*.csv + provenance
    uv run skills/scientist/scripts/sci.py cellcov "<exp dir>"            # full cell-coverage of legacy CSVs

Store (a libkit-backed index/search/catalog over a tree of experiments):

    uv run skills/scientist/scripts/sci.py init --home "<data folder>"
    uv run skills/scientist/scripts/sci.py reindex --home "<data folder>"
    uv run skills/scientist/scripts/sci.py query "lumbar knockdown" --home "<data folder>"
    uv run skills/scientist/scripts/sci.py review K1-000000 --home "<data folder>"

`audit` runs BOTH passes on one experiment: the extraction re-extraction check of
data/ ↔ raw/ AND provenance staleness of the experiment.yml ledger. With no
experiment, it runs the store staleness pass across the whole data folder. Use
`sci check` for the structural-integrity report. The prose ↔ claims check (every
asserted result maps to a grounded claim) runs in audit's semantic pass — see
references/review-audit.md.

`trace` statically walks the provenance DAG (recorded shas still match); `reproduce`
is its executable complement — it RE-RUNS <exp>/analysis/derive.py in the pinned
environment and checks the regenerated analysis/tables|fig/* reproduce the recorded
artifacts (within tolerance) and that the derivation read only from data/. Because it
re-executes derive.py it needs the pinned analysis runtime, so run it via the editable
install (which carries pandas/scipy/matplotlib), not the bare PEP723 env:

    SCIENTIST_HOME=… uv run --with-editable skills/scientist \
        skills/scientist/scripts/sci.py reproduce "<exp dir>"

`extract`'s recipe lives at <exp>/data/extract.py and defines build(x); see the
extraction package and references/extract.md. The data-tree root is $SCIENTIST_HOME,
the private vocab is $SCIENTIST_VOCAB, and the store lives at
<home>/.scientist/catalog.duckdb.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Put skills/scientist (the dir containing the `scientist` package) onto sys.path so
# `import scientist` and its subpackages (provenance, labfiles, extraction, store) resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scientist import extraction as EXT  # noqa: E402
from scientist import report as REPORT  # noqa: E402
from scientist.provenance import trace as TRACE  # noqa: E402
from scientist.provenance import reproduce as REPRODUCE  # noqa: E402
from scientist.store import cli as STORE_CLI  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(prog="sci", description="scientist CLI: extraction + store",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    # ---- extraction subcommands ----
    p_ex = sub.add_parser("extract", help="(re)generate data/*.csv from raw/ via the recipe")
    p_ex.add_argument("exp", help="experiment folder (path)")
    p_ex.add_argument("--script", help="extract.py to run (default <exp>/data/extract.py)")
    p_ex.add_argument("--commit", action="store_true",
                      help="write data/*.csv + experiment.yml provenance")
    p_ex.add_argument("--preview", help="dry-run output dir (default <exp>/data/_preview)")

    p_cc = sub.add_parser("cellcov", help="full cell-coverage check of legacy data/ CSVs")
    p_cc.add_argument("exp", help="experiment folder (path)")
    p_cc.add_argument("--script", help="extract.py to run (default <exp>/data/extract.py)")
    p_cc.add_argument("--examples", type=int, default=8,
                      help="show up to N example uncovered values per file (0 = none)")

    # ---- trace: end-to-end provenance walk (claim -> analysis -> data -> raw) ----
    p_tr = sub.add_parser("trace",
                          help="walk the provenance DAG: claim/artifact -> data -> raw, flagging breaks")
    p_tr.add_argument("exp", help="experiment folder (path)")
    p_tr.add_argument("--json", action="store_true", help="machine-readable output")
    p_tr.add_argument("--claim", help="trace just this claim id (full nodeid or its trailing name)")
    p_tr.add_argument("--report", help="grounding_report.json to use "
                      "(default <exp>/analysis/grounding_report.json then <exp>/grounding_report.json)")

    # ---- report: build + audit + render a grounded human narrative (ROADMAP §5) ----
    p_rep = sub.add_parser("report",
                           help="validate a report's claim citations + sha-pinned exhibits and "
                                "render it to PDF (claims -> report)")
    p_rep.add_argument("path", help="report markdown file or its directory "
                       "(program/reports/<slug>/ or <exp>/reports/<slug>/)")
    p_rep.add_argument("--format", dest="fmt", choices=["pdf", "md"], default="pdf",
                       help="render format (default pdf; 'md' emits the processed source)")
    p_rep.add_argument("--out", help="output path (default: the report .md with a .pdf suffix)")
    p_rep.add_argument("--audit-only", action="store_true",
                       help="validate citations + exhibits without rendering")
    p_rep.add_argument("--strict", action="store_true",
                       help="treat advisory findings (uncited quantitative sentences) as failures")
    p_rep.add_argument("--json", action="store_true", help="machine-readable output")

    # ---- reproduce: re-run analysis/derive.py and check it reproduces the recorded artifacts ----
    p_rp = sub.add_parser("reproduce",
                          help="re-run analysis/derive.py and check it reproduces the recorded "
                               "artifacts (reads only data/) — the executable complement to trace")
    p_rp.add_argument("exp", help="experiment folder (path)")
    p_rp.add_argument("--json", action="store_true", help="machine-readable output")
    p_rp.add_argument("--rtol", type=float, default=REPRODUCE.DEFAULT_RTOL,
                      help=f"relative tolerance for derived floats (default {REPRODUCE.DEFAULT_RTOL})")
    p_rp.add_argument("--atol", type=float, default=REPRODUCE.DEFAULT_ATOL,
                      help=f"absolute tolerance for derived floats (default {REPRODUCE.DEFAULT_ATOL})")

    # ---- store subcommands (init/index/reindex/list/show/search/query/file/read/
    #      entity/new/intake/meta/review/fingerprint/catalog/check/audit/pr) ----
    STORE_CLI.register(sub)

    # `audit` is registered by the store as a provenance-staleness command; extend it
    # with the extraction re-extraction flag so `sci audit <exp>` runs BOTH passes.
    audit_p = sub.choices["audit"]
    audit_p.add_argument("--script",
                         help="extract.py for the data/ re-extraction pass (default <exp>/data/extract.py)")

    args = ap.parse_args()

    if args.cmd == "extract":
        EXT.extract(args.exp, script=args.script, commit=args.commit, preview=args.preview)
        return 0
    if args.cmd == "cellcov":
        return EXT.cellcov(args.exp, args.script, args.examples)
    if args.cmd == "trace":
        return _trace(args)
    if args.cmd == "report":
        return REPORT.run(args.path, fmt=args.fmt, out=args.out,
                          audit_only=args.audit_only, strict=args.strict, as_json=args.json)
    if args.cmd == "reproduce":
        return _reproduce(args)
    if args.cmd == "audit":
        return _audit_both(args)
    return STORE_CLI.dispatch(args)


def _trace(args: argparse.Namespace) -> int:
    """`sci trace <exp>`: pure provenance walk — no libkit store. Exit 0 if fully
    grounded, 1 if any break."""
    import json

    result = TRACE.trace(Path(args.exp), report_path=args.report, claim_id=args.claim)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(TRACE.render(result))
    return 0 if result["status"] == "GROUNDED" else 1


def _reproduce(args: argparse.Namespace) -> int:
    """`sci reproduce <exp>`: re-run analysis/derive.py and check it reproduces the
    recorded artifacts within tolerance and read only from data/. Pure re-run (scratch
    output only); no libkit store. Exit 0 if REPRODUCES, 1 otherwise."""
    import json

    result = REPRODUCE.reproduce(Path(args.exp), rtol=args.rtol, atol=args.atol)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(REPRODUCE.render(result))
    return 0 if result["status"] == "REPRODUCES" else 1


def _audit_both(args: argparse.Namespace) -> int:
    """`sci audit`: run the data/-edge re-extraction audit (extraction) AND the
    provenance-staleness audit (store). The extraction pass needs a single experiment
    folder with a recipe; the store pass runs over one experiment or the whole folder.
    """
    rc = 0
    exp = getattr(args, "experiment", None)
    if exp:
        exp_path = Path(exp)
        recipe = Path(args.script) if args.script else (exp_path / "data" / "extract.py")
        if exp_path.is_dir() and recipe.is_file():
            print("== data/ re-extraction audit ==")
            rc = EXT.audit(exp, args.script) or 0
        else:
            print("== data/ re-extraction audit ==")
            print(f"(skipped: no recipe at {recipe} — provenance pass only)")
        print("\n== provenance staleness audit ==")
    # Provenance staleness is a PURE on-disk check (provenance.staleness + the shared
    # core) and must not require the libkit store. Open the store only when one exists
    # (so its indexed source_files worklist is used); otherwise walk the folder directly.
    if STORE_CLI.store_exists(args):
        store_rc = STORE_CLI.dispatch(args)
    else:
        store_rc = STORE_CLI.dispatch_audit_storeless(args)
    return rc or store_rc


if __name__ == "__main__":
    raise SystemExit(main())
