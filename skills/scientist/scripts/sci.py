#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "openpyxl>=3.1",
#   "pyyaml>=6.0",
#   "xlrd>=2.0",
#   "python-docx>=1.1",
#   "pdfplumber>=0.11",
#   "libkit>=0.4.0",
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
from scientist.provenance import litreview as LITREVIEW  # noqa: E402
from scientist.provenance import paperclaims as PAPERCLAIMS  # noqa: E402
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
    p_rep.add_argument("--write-pins", dest="write_pins", action="store_true",
                       help="write the surfaced litreview_pins into the report's YAML front matter "
                            "(mechanizes the manual paste). The recorded pin is a 12-char prefix "
                            "matched by startswith; the pin is over the cited litreview's search "
                            "protocol (queries + as_of + sources)")

    # ---- litreview: audit a neutral literature survey (kind=litreview). ----
    p_lr = sub.add_parser("litreview",
                          help="audit a literature review (review.md): every [lit:] claim backed, "
                               "literature-only, a gaps section present, the protocol + screening "
                               "committed and every cited paper screened-in; render/trace it.")
    p_lr.add_argument("path", help="litreview review.md (program/litreviews/<slug>/review.md) OR a "
                      "bare <slug> (resolved to program/litreviews/<slug>/review.md). A review may "
                      "be a flat review.md or a Phase-3 node tree (nodes/ + [litreview:] edges) — "
                      "audit/render are tree-aware.")
    p_lr.add_argument("--home", help="managed data folder (default: $SCIENTIST_HOME or inferred)")
    p_lr.add_argument("--add-node", dest="add_node", metavar="NEW_ID",
                      help="(tree) scaffold a child node nodes/<NEW_ID>.md under --parent; `sci` only "
                           "lays out the file — move the [lit:] cites + add the parent edge by hand "
                           "(see references/reviews-tree.md)")
    p_lr.add_argument("--parent", help="with --add-node: the parent node id the new node rolls into")
    p_lr.add_argument("--write-rollup-pins", dest="write_rollup_pins", action="store_true",
                      help="(tree) write each rollup's rolled_against: {<child>: <summary-sha>} pins "
                           "into its frontmatter (mechanizes the manual paste)")
    p_lr.add_argument("--json", action="store_true", help="machine-readable output")
    p_lr.add_argument("--ingest-discover", dest="ingest_discover", metavar="DISCOVER_JSON",
                      help="append candidate rows to screening.jsonl from a `bib discover --json` "
                           "payload (decision unset, de-duped by id) — `sci` never calls the search "
                           "API; a re-discover is re-fed through here")
    p_lr.add_argument("--query", help="with --ingest-discover: the query string to stamp on the "
                      "ingested rows (default: the payload's top-level `query`, if any)")
    p_lr.add_argument("--render", metavar="OUT", help="render the validated litreview to OUT (via pandoc)")
    p_lr.add_argument("--to", choices=["pdf", "html", "docx"], default="pdf",
                      help="render format (default pdf; via pandoc)")
    p_lr.add_argument("--force", action="store_true",
                      help="render even if the audit is BROKEN (default: refuse)")
    p_lr.add_argument("--trace", action="store_true",
                      help="also print the provenance trace (litreview -> each [lit:] claim -> paper)")
    p_lr.add_argument("--delta", metavar="BASELINE",
                      help="claim-set delta of this litreview's module vs a baseline "
                           "grounding_report.json (e.g. `git show <ref>:program/analysis/"
                           "grounding_report.json > base.json`) — the cheap-update filter")
    p_lr.add_argument("--index", action="store_true",
                      help="index the litreview into the store as kind=litreview (needs the store)")

    # ---- new-litreview: scaffold a litreview folder + the correctly-named claim module. ----
    p_nlr = sub.add_parser("new-litreview",
                           help="scaffold program/litreviews/<slug>/ (review.md + protocol.md + "
                                "screening.jsonl + prompt.md) and the correctly-named "
                                "program/claims/test_litreview_<slug>.py claim module")
    p_nlr.add_argument("slug", help="litreview slug (hyphenated, e.g. it-aso-biodistribution)")
    p_nlr.add_argument("--home", help="managed data folder (default: $SCIENTIST_HOME or inferred)")
    p_nlr.add_argument("--title", help="review.md front-matter title (default: the slug, de-hyphenated)")
    p_nlr.add_argument("--scope", default="program",
                       help="scope dir to scaffold under (default: program)")
    p_nlr.add_argument("--json", action="store_true", help="machine-readable output")

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

    # ---- paper-claims: a paper's pre-extracted ATTRIBUTED claim set (Phase 2). ----
    #      scaffold/validate/verify a per-paper JSONL, or (no action) load + emit for the
    #      `--json | python3 -c` pattern. Scientist-side, scientist's own store; bib read-only.
    p_pc = sub.add_parser("paper-claims",
                          help="a paper's pre-extracted attributed claim set: `scaffold <citekey>` "
                               "(open the JSONL + emit the extraction brief), `validate <citekey>` "
                               "(schema), `verify <citekey>` (quote-integrity), or no action to "
                               "load + emit (--json, filter with --query/--paper)")
    p_pc.add_argument("action", nargs="?", default="list",
                      choices=["scaffold", "validate", "verify", "list"],
                      help="scaffold | validate | verify | list (default: list/emit)")
    p_pc.add_argument("citekey", nargs="?",
                      help="bibliographer citekey (required for scaffold/validate/verify)")
    p_pc.add_argument("--home", help="managed data folder (default: $SCIENTIST_HOME or inferred)")
    p_pc.add_argument("--paper", help="with `list`: scope to one paper's citekey")
    p_pc.add_argument("--query", help="with `list`: substring/regex filter over `paraphrase` "
                      "(the grep path — no semantic ranking)")
    p_pc.add_argument("--json", action="store_true", help="machine-readable output")

    # ---- coverage: is the grounding keeping up with the library? ----
    p_cov = sub.add_parser("coverage",
                           help="library papers cited by NO grounded claim — the completeness "
                                "counterpart to `report` (catches grounding stagnation)")
    p_cov.add_argument("--home", help="managed data folder (default: $SCIENTIST_HOME or inferred)")
    p_cov.add_argument("--since", help="flag uncited papers added on/after this ISO date "
                       "(e.g. 2026-06-16); default: the most recently banked uncited")
    p_cov.add_argument("--query", help="topic to scope the worklist to (RECOMMENDED for a single "
                       "report/sub-question): intersect the uncited set with `bib query` hits and "
                       "rank by score, instead of the coarse library-wide tally")
    p_cov.add_argument("--query-limit", type=int, default=100,
                       help="max `bib query` hits to scope against when --query is given (default 100)")
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
    if args.cmd == "litreview":
        return _litreview(args)
    if args.cmd == "new-litreview":
        return _new_litreview(args)
    if args.cmd == "judge":
        return _judge(args)
    if args.cmd == "paper-claims":
        return _paper_claims(args)
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

    if getattr(args, "write_pins", False):
        # Each resolved [litreview:] cite carries a protocol-keyed pin; write them into
        # litreview_pins so re-runs stay green until the survey's search protocol drifts.
        surfaced = {lc["id"]: lc["pin"] for lc in result.get("litreview_cites", [])
                    if lc.get("pin")}
        if surfaced:
            merged = REPORT.write_litreview_pins(path, surfaced)
            if not args.json:
                wrote = ", ".join(f"{k}: {v}" for k, v in sorted(surfaced.items()))
                print(f"wrote litreview_pins ({wrote}); {len(merged)} pin(s) total in front matter")
        elif not args.json:
            print("no litreview pins to write — no resolved [litreview:] cite in this report")

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


def _resolve_review_path(arg: str, home) -> Path:
    """Resolve the litreview positional to a ``review.md`` path. An existing file/dir is used as
    given (a dir → its ``review.md``); otherwise a bare ``<slug>`` is resolved to
    ``<home>/program/litreviews/<slug>/review.md`` (then tree-wide ``**/litreviews/<slug>/``)."""
    p = Path(arg)
    if p.is_file():
        return p
    if p.is_dir():
        return p / "review.md"
    if home is not None and "/" not in arg and "\\" not in arg and not arg.endswith(".md"):
        cand = Path(home) / "program" / "litreviews" / arg / "review.md"
        if cand.is_file():
            return cand
        hits = sorted(Path(home).glob(f"**/litreviews/{arg}/review.md"))
        if hits:
            return hits[0]
    return p


def _litreview(args: argparse.Namespace) -> int:
    """`sci litreview <path>`: audit a litreview (review.md) — every [lit:] claim backed,
    literature-only, a gaps section present, the protocol + screening committed and every cited
    paper screened-in — and optionally ingest a `bib discover` payload into screening.jsonl, render,
    or trace it. The protocol-keyed `stale-litreview` pin (a property of the consuming report) lives
    in `sci report`. Exit 0 if GROUNDED (and any render succeeded), 1 otherwise."""
    import json

    from scientist.provenance import reviewtree as TREE

    home = resolve_home(args)
    path = _resolve_review_path(args.path, home)

    # --add-node: scaffold a child node file (tree authoring). Needs --parent.
    if getattr(args, "add_node", None):
        if not args.parent:
            print("sci litreview --add-node <id> needs --parent <parent-id>", file=sys.stderr)
            return 1
        if home is None:
            print("no data-tree root: pass --home or set $SCIENTIST_HOME", file=sys.stderr)
            return 1
        sc = REPORT.report_scope(path, home)
        res = TREE.add_node(home, sc["slug"], args.add_node, args.parent, scope=sc["scope"])
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        else:
            state = "created" if res["created"] else "exists"
            print(f"{state} {res['path']}\n  → {res['reminder']}")
        return 0

    if getattr(args, "write_rollup_pins", False):
        touched = TREE.write_rollup_pins(path, home=home)
        if args.json:
            print(json.dumps(touched, indent=2, ensure_ascii=False, default=str))
        else:
            if not touched:
                print("no rollup nodes to pin (a flat review, or no [litreview:] edges)")
            for nid, pins in sorted(touched.items()):
                print(f"pinned {nid}: " + ", ".join(f"{c}={s}" for c, s in sorted(pins.items())))
        return 0

    if getattr(args, "ingest_discover", None):
        res = LITREVIEW.ingest_discover(path, Path(args.ingest_discover), home=home,
                                        query=getattr(args, "query", None))
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        else:
            print(f"appended {res['appended']} candidate(s) to {res['screening']} "
                  f"(skipped {res['skipped_duplicate']} duplicate, {res['skipped_no_id']} "
                  f"without an id) — screen each to included|excluded(+reason) by hand")
        return 0

    if args.delta:
        d = LITREVIEW.delta(path, Path(args.delta), home=home)
        if args.json:
            print(json.dumps(d, indent=2, ensure_ascii=False, default=str))
        else:
            print(LITREVIEW.render_delta(d))
        return 0

    result = LITREVIEW.audit(path, home=home)
    emit(result, args.json, LITREVIEW.render_audit)
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
            print(f"refusing to render a BROKEN litreview (fix the findings, or --force): {args.render}",
                  file=sys.stderr)
            rc = 1
        else:
            try:
                # A node tree linearizes depth-first (facts resolved fresh) before pandoc; a flat
                # review renders directly. Both reuse the `sci report` markdown→pandoc path.
                renderer = TREE.render if result.get("tree") else REPORT.render
                out = renderer(path, Path(args.render), home=home, to=args.to)
                print(f"rendered {out['format'].upper()} → {out['output']}")
            except REPORT.RenderError as e:
                print(f"render failed: {e}", file=sys.stderr)
                rc = 1

    if args.index:
        sc = REPORT.report_scope(path, home or REPORT._infer_home(path.resolve()))
        sec = REPORT.parse_sections(path.read_text(encoding="utf-8"))
        cited = sorted({lc.get("claim_id") or lc["id"] for lc in result.get("lit_cites", [])})
        card = {
            "litreview_id": STORE_META.report_id_for(sc["scope"], sc["exp_id"], sc["slug"]),
            "scope": sc["scope"], "slug": sc["slug"],
            "title": sec["title"], "abstract": sec["abstract"], "sections": sec["sections"],
            "cited_claims": cited, "funnel": result.get("funnel", {}),
            "audit_status": result["status"], "path": result["report"],
        }
        STORE_CLI.index_litreview(args, card)

    return rc


def _new_litreview(args: argparse.Namespace) -> int:
    """`sci new-litreview <slug>`: scaffold a litreview folder (review.md + protocol.md +
    screening.jsonl + prompt.md) and its correctly-named claim module (test_litreview_<slug>.py).
    Removes the highest-risk manual steps — the module name and the committed PRISMA artifacts.
    Exit 0 on success."""
    import json

    home = resolve_home(args)
    if home is None:
        print("no data-tree root: pass --home or set $SCIENTIST_HOME", file=sys.stderr)
        return 1
    res = LITREVIEW.scaffold(home, args.slug, title=args.title, scope=args.scope)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return 0
    for rel in res["created"]:
        print(f"created {rel}")
    for rel in res["skipped"]:
        print(f"skipped {rel} (exists)")
    if not res["created"]:
        print("nothing created (all files already exist)")
    else:
        print(f"pre-register the search in protocol.md, seed screening.jsonl "
              f"(`sci litreview <review.md> --ingest-discover <discover.json>`), author the survey "
              f"in review.md, ground [lit:] claims in {res['module']}, then `sci litreview <review.md>`")
    return 0


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


def _paper_claims(args: argparse.Namespace) -> int:
    """`sci paper-claims …`: scaffold / validate / verify a paper's pre-extracted ATTRIBUTED
    claim set, or (no action) load + emit it. All offline + store-local — the extractor reads
    bibliographer's PDFs read-only and writes scientist's OWN per-paper JSONL; bib's DB is never
    touched. Exit 0 unless a validate/verify finds a blocking problem."""
    import json

    home = resolve_home(args)
    if home is None:
        print("no data-tree root: pass --home or set $SCIENTIST_HOME", file=sys.stderr)
        return 1

    action = args.action
    if action in ("scaffold", "validate", "verify"):
        if not args.citekey:
            print(f"sci paper-claims {action} needs a <citekey>", file=sys.stderr)
            return 1
        if action == "scaffold":
            try:
                res = PAPERCLAIMS.scaffold(home, args.citekey)
            except Exception as e:                       # LiteratureError etc. — paper not resolvable
                print(f"could not resolve {args.citekey} in the bibliographer library: {e}",
                      file=sys.stderr)
                return 1
            emit(res, args.json, PAPERCLAIMS.render_scaffold)
            return 0
        if action == "validate":
            res = PAPERCLAIMS.validate(home, args.citekey)
            emit(res, args.json, PAPERCLAIMS.render_validate)
            return 0 if res["status"] == "VALID" else 1
        # verify
        try:
            res = PAPERCLAIMS.verify(home, args.citekey)
        except Exception as e:
            print(f"could not read {args.citekey}'s text to verify quotes: {e}", file=sys.stderr)
            return 1
        emit(res, args.json, PAPERCLAIMS.render_verify)
        return 0 if res["status"] == "VERIFIED" else 1

    # list / emit
    claims = PAPERCLAIMS.query(home, paper=args.paper, query=args.query)
    if args.json:
        print(json.dumps(claims, indent=2, ensure_ascii=False, default=str))
    else:
        print(PAPERCLAIMS.render_query(claims))
    return 0


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

    # Topic-scoping (recommended for a single report): intersect the uncited set with `bib query`
    # hits and rank by score. The same chunk-level hits a literature sweep uses; we fold to the
    # best score per citekey (a paper may surface via several chunks).
    query_scores: dict[str, float] | None = None
    if args.query:
        try:
            proc = subprocess.run(
                [*bib_cmd, "query", args.query, "--json", "--limit", str(args.query_limit)],
                capture_output=True, text=True, check=True)
            hits = json.loads(proc.stdout)
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"could not run `bib query` for --query scoping: {e}", file=sys.stderr)
            return 1
        except ValueError as e:
            print(f"bibliographer did not return JSON for `bib query`: {e}", file=sys.stderr)
            return 1
        query_scores = {}
        for h in hits if isinstance(hits, list) else []:
            ck, score = h.get("citekey"), h.get("score")
            if ck and score is not None:
                query_scores[str(ck)] = max(query_scores.get(str(ck), float(score)), float(score))

    result = COVERAGE.coverage(library, cited, since=args.since,
                               query=args.query, query_scores=query_scores)
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
