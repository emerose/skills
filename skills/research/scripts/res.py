#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml>=6.0",
#   "libkit>=0.5.0",
#   "platformdirs>=4.0",
#   "pytest-grounding>=0.0.3",
# ]
# ///
"""research CLI — zero-install entry point for the literature layer.

Runnable directly with uv (PEP 723 deps inline), no virtualenv. The literature half of the
scientific-data pipeline, split out of `sci`:

Literature reviews (neutral PROSPERO/PRISMA surveys; kind=litreview):

    uv run skills/research/scripts/res.py new-litreview <slug> --home "<data folder>"
    uv run skills/research/scripts/res.py litreview <review.md|slug> --home "<data folder>"
    uv run skills/research/scripts/res.py litreview <review.md> --ingest-discover <discover.json>

Paper-claims (a paper's pre-extracted ATTRIBUTED claim set; per-paper JSONL store):

    uv run skills/research/scripts/res.py paper-claims scaffold <citekey> --home "<data folder>"
    uv run skills/research/scripts/res.py paper-claims verify  <citekey> --home "<data folder>"

Literature support verdicts (`[lit:]` entailment; NO model in the tool):

    uv run skills/research/scripts/res.py judge --list   --home "<data folder>"
    uv run skills/research/scripts/res.py judge --record verdicts.json --home "<data folder>"

Coverage (library papers cited by no grounded claim):

    uv run skills/research/scripts/res.py coverage --home "<data folder>" --query "<topic>"

research operates on the SAME scientific-data tree as scientist (`$SCIENTIST_HOME`): litreviews
under `program/litreviews/<slug>/`, `[lit:]` claim modules under `…/claims/`, and the per-paper
paper-claims store at the home root. It owns no libkit store — its citation tracking lives in the
grounding reports + the litreview files + the bibliographer library (`$BIBLIOGRAPHER_HOME`).
research never imports scientist; it composes with experiment reports only through the shared
reportkit citation registry (which `research.literature_cites` registers `[lit:]`/`[litreview:]`
on). `sci report` is the experiment-report verb and stays in scientist.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Put skills/research (the dir containing the `research` package) onto sys.path so
# `import research` and its modules resolve. research bootstraps reportkit on its own import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import litreview as LITREVIEW  # noqa: E402  (importing research registers [lit:]/[litreview:])
from research import paperclaims as PAPERCLAIMS  # noqa: E402
from research import coverage as COVERAGE  # noqa: E402
from research.cli_utils import emit, resolve_home  # noqa: E402

from reportkit import report as REPORT  # noqa: E402  (the generic engine)
import reportkit.trace as RKTRACE  # noqa: E402


def _trace_report(path: Path, home):
    """Report-rooted provenance trace for a review. A litreview is `[lit:]`-only, so the generic
    engine trace (which walks `[claim:]` citations) has no experiment chain to follow — a trivial
    ``trace_fn`` suffices (it is never invoked for a literature-only review)."""
    return RKTRACE.trace_report(path, home, trace_fn=lambda exp_dir, **_kw: {"chains": []})


def main() -> int:
    ap = argparse.ArgumentParser(prog="res", description="research CLI: literature reviews, "
                                 "paper-claims, bibliometric coverage",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

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
                      help="(tree) scaffold a child node nodes/<NEW_ID>.md under --parent; `res` only "
                           "lays out the file — move the [lit:] cites + add the parent edge by hand "
                           "(see references/reviews-tree.md)")
    p_lr.add_argument("--parent", help="with --add-node: the parent node id the new node rolls into")
    p_lr.add_argument("--write-rollup-pins", dest="write_rollup_pins", action="store_true",
                      help="(tree) write each rollup's rolled_against: {<child>: <summary-sha>} pins "
                           "into its frontmatter (mechanizes the manual paste)")
    p_lr.add_argument("--json", action="store_true", help="machine-readable output")
    p_lr.add_argument("--ingest-discover", dest="ingest_discover", metavar="DISCOVER_JSON",
                      help="append candidate rows to screening.jsonl from a `bib discover --json` "
                           "payload (decision unset, de-duped by id) — `res` never calls the search "
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

    # ---- judge: list the literature-support work + record caller-supplied verdicts. ----
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
                                "counterpart to a report (catches grounding stagnation)")
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

    args = ap.parse_args()

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
    return 2


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
    """`res litreview <path>`: audit a litreview (review.md) — every [lit:] claim backed,
    literature-only, a gaps section present, the protocol + screening committed and every cited
    paper screened-in — and optionally ingest a `bib discover` payload into screening.jsonl, render,
    or trace it. research owns no store, so there is no `--index`. Exit 0 if GROUNDED (and any
    render succeeded), 1 otherwise."""
    import json

    from research import reviewtree as TREE

    home = resolve_home(args)
    path = _resolve_review_path(args.path, home)

    # --add-node: scaffold a child node file (tree authoring). Needs --parent.
    if getattr(args, "add_node", None):
        if not args.parent:
            print("res litreview --add-node <id> needs --parent <parent-id>", file=sys.stderr)
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
        tr = _trace_report(path, home)
        if args.json:
            print(json.dumps(tr, indent=2, ensure_ascii=False, default=str))
        else:
            print("\n" + RKTRACE.render_report_trace(tr))
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
                # review renders directly. Both reuse the engine's markdown→pandoc path.
                renderer = TREE.render if result.get("tree") else REPORT.render
                out = renderer(path, Path(args.render), home=home, to=args.to)
                print(f"rendered {out['format'].upper()} → {out['output']}")
            except REPORT.RenderError as e:
                print(f"render failed: {e}", file=sys.stderr)
                rc = 1

    return rc


def _new_litreview(args: argparse.Namespace) -> int:
    """`res new-litreview <slug>`: scaffold a litreview folder (review.md + protocol.md +
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
              f"(`res litreview <review.md> --ingest-discover <discover.json>`), author the survey "
              f"in review.md, ground [lit:] claims in {res['module']}, then `res litreview <review.md>`")
    return 0


def _judge(args: argparse.Namespace) -> int:
    """`res judge`: list the literature-support work, or record caller-supplied verdicts.

    **No model runs in the tool.** ``--list`` surfaces each machine-judged ``[lit:]`` source
    (``source(paraphrase=…)``) whose cached verdict is missing or stale, with the ``span_text`` and
    ``paraphrase`` a *fresh-context judge subagent* reads. ``--record <file|->`` ingests that
    subagent's verdicts ``{citekey, paraphrase, supported, rationale}`` and writes them into the
    pinned cache (the tool recomputes ``evidence_sha`` from the report's stored span). Re-run the
    claims suite afterwards. Exit 0 (a worklist/record op, not a gate)."""
    import json

    from research import refresh as REFRESH

    if not args.do_list and not args.record:
        print("res judge needs --list (surface work) or --record <file|-> (write verdicts)",
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
        print(f"res judge --record: not valid JSON ({exc})", file=sys.stderr)
        raise SystemExit(1)
    if isinstance(data, dict):
        data = data.get("verdicts", data.get("records", []))
    if not isinstance(data, list):
        print("res judge --record: expected a JSON list of "
              "{citekey, paraphrase, supported, rationale}", file=sys.stderr)
        raise SystemExit(1)
    return data


def _paper_claims(args: argparse.Namespace) -> int:
    """`res paper-claims …`: scaffold / validate / verify a paper's pre-extracted ATTRIBUTED
    claim set, or (no action) load + emit it. All offline + store-local — the extractor reads
    bibliographer's PDFs read-only and writes research's OWN per-paper JSONL; bib's DB is never
    touched. Exit 0 unless a validate/verify finds a blocking problem."""
    import json

    home = resolve_home(args)
    if home is None:
        print("no data-tree root: pass --home or set $SCIENTIST_HOME", file=sys.stderr)
        return 1

    action = args.action
    if action in ("scaffold", "validate", "verify"):
        if not args.citekey:
            print(f"res paper-claims {action} needs a <citekey>", file=sys.stderr)
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
    """`res coverage`: library papers cited by no grounded claim — the completeness counterpart
    to a report. Reads cited citekeys from the grounding reports under the data tree and the
    library via `bib list --json`. Informational (always exit 0); a worklist, not a gate."""
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


if __name__ == "__main__":
    raise SystemExit(main())
