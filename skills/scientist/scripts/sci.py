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

`report` is the terminal claims->report phase: it audits a human-facing report
Markdown's [claim:<id>] citations and figure/table embeds (each citation must resolve
to a live, grounded claim; each embed to a current sha-pinned analysis artifact),
renders it to PDF/HTML/docx via pandoc, traces it (report -> claims -> raw), and
indexes it as kind=report. The *semantic* "is every result cited / on-topic" check
stays the audit semantic pass (references/review-audit.md); `sci report` mechanizes
citation + artifact resolution and render. `sci trace <report.md>` is the same
report-rooted walk.

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
from scientist.cli_utils import emit, resolve_home  # noqa: E402
from scientist.provenance import trace as TRACE  # noqa: E402
from scientist.provenance import reproduce as REPRODUCE  # noqa: E402
from scientist.provenance import report as REPORT  # noqa: E402
from scientist.provenance import coverage as COVERAGE  # noqa: E402
from scientist.store import cli as STORE_CLI  # noqa: E402
from scientist.store import _meta as STORE_META  # noqa: E402


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
    p_tr.add_argument("exp", help="experiment folder, OR a report .md (report-rooted trace)")
    p_tr.add_argument("--json", action="store_true", help="machine-readable output")
    p_tr.add_argument("--claim", help="trace just this claim id (full nodeid or its trailing name)")
    p_tr.add_argument("--report", help="grounding_report.json to use "
                      "(default <exp>/analysis/grounding_report.json then <exp>/grounding_report.json)")
    p_tr.add_argument("--home", help="data-tree root for a report-rooted trace "
                      "(default: $SCIENTIST_HOME or inferred)")

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

    # ---- report: build / audit / render the terminal claims -> report phase ----
    p_rep = sub.add_parser("report",
                           help="audit a report's [claim:<id>] citations + figure/table embeds "
                                "(grounded rule), render it to PDF/HTML/docx, trace it, or index it")
    p_rep.add_argument("path", help="report Markdown file (program/reports/<slug>/… or <exp>/reports/<slug>/…)")
    p_rep.add_argument("--home", help="managed data folder (default: $SCIENTIST_HOME or inferred)")
    p_rep.add_argument("--json", action="store_true", help="machine-readable output")
    p_rep.add_argument("--render", metavar="OUT", help="render the validated report to OUT")
    p_rep.add_argument("--to", choices=["pdf", "html", "docx"], default="pdf",
                       help="render format (default pdf; via pandoc)")
    p_rep.add_argument("--force", action="store_true",
                       help="render even if the audit is BROKEN (default: refuse)")
    p_rep.add_argument("--trace", action="store_true",
                       help="also print the report-rooted provenance trace (report -> claims -> raw)")
    p_rep.add_argument("--index", action="store_true",
                       help="index the report into the store as kind=report (needs the store)")

    # ---- judge: list the literature-support work + record caller-supplied verdicts.
    #      NO model lives in the tool — the orchestrating agent (ideally a fresh-context judge
    #      subagent) decides supported/unsupported; this command only surfaces + records it. ----
    p_jd = sub.add_parser("judge",
                          help="literature support verdicts: `--list` the [lit:] sources whose "
                               "verdict is missing/stale (with the span to judge), then `--record` "
                               "the caller's verdicts into the pinned cache. No model in the tool.")
    p_jd.add_argument("--list", dest="do_list", action="store_true",
                      help="emit the worklist of [lit:] sources to judge (span_text + paraphrase) — "
                           "what a fresh-context judge subagent reads")
    p_jd.add_argument("--record", metavar="FILE",
                      help="ingest caller-supplied verdicts {citekey, paraphrase, supported, "
                           "rationale} from a JSON file (or `-` for stdin) into the pinned cache")
    p_jd.add_argument("--judge-id", help="who judged (stamped as metadata; default: 'agent')")
    p_jd.add_argument("--home", help="managed data folder (default: $SCIENTIST_HOME or inferred)")
    p_jd.add_argument("--report", help="a single grounding_report.json to operate on "
                      "(default: every one under home)")
    p_jd.add_argument("--cache", help="verdict cache sidecar to read/write "
                      "(default: <report dir>/lit_judgments.json, next to each report)")
    p_jd.add_argument("--force", action="store_true",
                      help="with --list, include sources whose cached verdict is still fresh")
    p_jd.add_argument("--json", action="store_true", help="machine-readable output")

    # ---- coverage: is the grounding keeping up with the library? ----
    p_cov = sub.add_parser("coverage",
                           help="library papers cited by NO grounded claim — the completeness "
                                "counterpart to `report` (catches grounding stagnation)")
    p_cov.add_argument("--home", help="managed data folder (default: $SCIENTIST_HOME or inferred)")
    p_cov.add_argument("--since", help="flag uncited papers added on/after this ISO date "
                       "(e.g. 2026-06-16); default: the most recently banked uncited")
    p_cov.add_argument("--bib", help="command to run the bibliographer CLI "
                       "(default: $SCIENTIST_BIB_CMD, else the sibling bib.py via uv, else `bib`)")
    p_cov.add_argument("--json", action="store_true", help="machine-readable output")

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
    if args.cmd == "reproduce":
        return _reproduce(args)
    if args.cmd == "report":
        return _report(args)
    if args.cmd == "judge":
        return _judge(args)
    if args.cmd == "coverage":
        return _coverage(args)
    if args.cmd == "audit":
        return _audit_both(args)
    return STORE_CLI.dispatch(args)


def _trace(args: argparse.Namespace) -> int:
    """`sci trace <exp>`: pure provenance walk — no libkit store. Exit 0 if fully
    grounded, 1 if any break.

    If the target is a report Markdown file (``<…>/reports/…/*.md``), trace it
    report-rooted instead: a report node atop the DAG, walked down through each cited
    claim to raw."""
    target = Path(args.exp)
    if target.is_file() and target.suffix.lower() == ".md":
        home = resolve_home(args)
        result = TRACE.trace_report(target, repo_root=home)
        emit(result, args.json, TRACE.render_report_trace)
        return 0 if result["status"] == "GROUNDED" else 1

    result = TRACE.trace(target, report_path=args.report, claim_id=args.claim)
    emit(result, args.json, TRACE.render)
    return 0 if result["status"] == "GROUNDED" else 1


def _report(args: argparse.Namespace) -> int:
    """`sci report <path>`: audit a report's citations + embeds (the mechanical half of
    the report phase), and optionally render / trace / index it. Exit 0 if the audit is
    GROUNDED (and any requested render succeeded), 1 otherwise."""
    import json

    path = Path(args.path)
    home = resolve_home(args)

    result = REPORT.audit(path, home=home)
    emit(result, args.json, REPORT.render_audit)

    rc = 0 if result["status"] == "GROUNDED" else 1

    if args.trace:
        tr = TRACE.trace_report(path, repo_root=home)
        if args.json:
            print(json.dumps(tr, indent=2, ensure_ascii=False, default=str))
        else:
            print("\n" + TRACE.render_report_trace(tr))
        if tr["status"] != "GROUNDED":
            rc = 1

    if args.render:
        if result["status"] != "GROUNDED" and not args.force:
            print(f"refusing to render a BROKEN report (fix the findings, or --force): {args.render}",
                  file=sys.stderr)
            rc = 1
        else:
            try:
                out = REPORT.render(path, Path(args.render), home=home, to=args.to)
                print(f"rendered {out['format'].upper()} → {out['output']}")
            except REPORT.RenderError as e:
                print(f"render failed: {e}", file=sys.stderr)
                rc = 1

    if args.index:
        sc = REPORT.report_scope(path, home or REPORT._infer_home(path.resolve()))
        sec = REPORT.parse_sections(path.read_text(encoding="utf-8"))
        cited = sorted({c.get("claim_id") or c["id"] for c in result["citations"]})
        card = {
            "report_id": STORE_META.report_id_for(sc["scope"], sc["exp_id"], sc["slug"]),
            "scope": sc["scope"], "exp_id": sc["exp_id"], "slug": sc["slug"],
            "title": sec["title"], "abstract": sec["abstract"], "sections": sec["sections"],
            "cited_claims": cited, "audit_status": result["status"],
            "path": result["report"],
        }
        STORE_CLI.index_report(args, card)

    return rc


def _judge(args: argparse.Namespace) -> int:
    """`sci judge`: list the literature-support work, or record caller-supplied verdicts.

    **No model runs in the tool.** ``--list`` surfaces each machine-judged ``[lit:]`` source
    (``source(paraphrase=…)``) whose cached verdict is missing or stale, with the ``span_text`` and
    ``paraphrase`` a *fresh-context judge subagent* reads to decide "does the span fairly support
    the paraphrase?". ``--record <file|->`` ingests that subagent's verdicts ``{citekey,
    paraphrase, supported, rationale}`` and writes them into the pinned cache (the tool recomputes
    ``evidence_sha`` from the report's stored span, so a verdict can't attach to a wrong/stale
    span). Re-run the claims suite afterwards so the cached verdicts back the citations. Exit 0
    (a worklist/record op, not a gate)."""
    import json

    from scientist.grounding import refresh as REFRESH

    if not args.do_list and not args.record:
        print("sci judge needs --list (surface work) or --record <file|-> (write verdicts)",
              file=sys.stderr)
        return 1

    home = resolve_home(args)

    if args.report:
        reports = [Path(args.report)]
    elif home is not None:
        reports = [p for _, p in REPORT._grounding_reports(home)]
    else:
        print("no grounding report: pass --report, or --home / $SCIENTIST_HOME", file=sys.stderr)
        return 1
    if not reports:
        print("no grounding_report.json found — run the claims suite first "
              "(pytest … --grounding-out <dir>)", file=sys.stderr)
        return 1

    if args.record:
        records = _read_verdict_records(args.record)
        results = []
        for rp in reports:
            cache = Path(args.cache) if args.cache else None
            res = REFRESH.record_verdicts(rp, records, cache, judge_id=args.judge_id)
            results.append(res)
            if not args.json:
                print(REFRESH.render_record(res))
        if args.json:
            print(json.dumps({"results": results}, indent=2, ensure_ascii=False, default=str))
        return 0

    # --list
    results = []
    for rp in reports:
        cache = Path(args.cache) if args.cache else None
        res = REFRESH.worklist(rp, cache, force=args.force)
        results.append(res)
        if not args.json:
            print(REFRESH.render_worklist(res))
    if args.json:
        print(json.dumps({"results": results}, indent=2, ensure_ascii=False, default=str))
    return 0


def _read_verdict_records(src: str) -> list:
    """Read caller-supplied verdict records from a JSON file (or stdin via ``-``). Accepts either a
    bare list ``[{citekey, paraphrase, supported, rationale}, …]`` or ``{"verdicts": [...]}``."""
    import json

    text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as exc:
        print(f"sci judge --record: not valid JSON ({exc})", file=sys.stderr)
        raise SystemExit(1)
    if isinstance(data, dict):
        data = data.get("verdicts", data.get("records", []))
    if not isinstance(data, list):
        print("sci judge --record: expected a JSON list of "
              "{citekey, paraphrase, supported, rationale}", file=sys.stderr)
        raise SystemExit(1)
    return data


def _coverage(args: argparse.Namespace) -> int:
    """`sci coverage`: library papers cited by no grounded claim — the completeness
    counterpart to `report`. Reads cited citekeys from the grounding reports under the
    data tree and the library via `bib list --json`. Informational (always exit 0); it
    is a worklist, not a gate."""
    import json
    import os
    import shlex
    import subprocess

    home = resolve_home(args)
    if home is None:
        print("no data folder: pass --home or set $SCIENTIST_HOME", file=sys.stderr)
        return 1

    cited = COVERAGE.cited_citekeys(REPORT.index_claims(home))

    if args.bib:
        bib_cmd = shlex.split(args.bib)
    elif os.environ.get("SCIENTIST_BIB_CMD"):
        bib_cmd = shlex.split(os.environ["SCIENTIST_BIB_CMD"])
    else:
        sibling = Path(__file__).resolve().parent.parent.parent / "bibliographer" / "scripts" / "bib.py"
        bib_cmd = ["uv", "run", str(sibling)] if sibling.is_file() else ["bib"]

    try:
        proc = subprocess.run([*bib_cmd, "list", "--json"],
                              capture_output=True, text=True, check=True)
        library = json.loads(proc.stdout)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"could not run the bibliographer CLI ({' '.join(bib_cmd)} list --json): {e}\n"
              f"pass --bib '<cmd>' or set $SCIENTIST_BIB_CMD; ensure $BIBLIOGRAPHER_HOME is set",
              file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"bibliographer did not return JSON: {e}", file=sys.stderr)
        return 1
    if isinstance(library, dict):
        library = library.get("articles") or library.get("records") or []

    result = COVERAGE.coverage(library, cited, since=args.since)
    emit(result, args.json, COVERAGE.render_coverage)
    return 0


def _reproduce(args: argparse.Namespace) -> int:
    """`sci reproduce <exp>`: re-run analysis/derive.py and check it reproduces the
    recorded artifacts within tolerance and read only from data/. Pure re-run (scratch
    output only); no libkit store. Exit 0 if REPRODUCES, 1 otherwise."""
    result = REPRODUCE.reproduce(Path(args.exp), rtol=args.rtol, atol=args.atol)
    emit(result, args.json, REPRODUCE.render)
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
