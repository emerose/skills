#!/usr/bin/env python3
"""Program-wide grounding rollup — aggregate every experiment's claims into one
"state of the evidence" report.

Runs the claims of every ``<exp>/analysis/claims`` under ``$SCIENTIST_HOME`` in a
single pytest session (so cross-experiment ``uses()`` links resolve), then aggregates
the combined grounding report into a program-level view:

  * summary — claim/experiment counts, by outcome / kind / strength
  * per-experiment table — claims, kinds, outcomes
  * cross-experiment graph — every claim whose evidence spans >1 experiment
  * full claim index by kind — statement, strength, outcome, evidence, inputs

Output: ``program_evidence.md`` + ``program_evidence.json`` in --out (default: cwd).

Usage:
    SCIENTIST_HOME=… rollup.py [--out DIR]

The rollup tool is generic (no experiment-specifics). It is the substrate for the
semantic audit (checking the program's stated conclusions against these grounded claims).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

_EXP_RE = re.compile(r"(K1-[0-9A-Za-z]+)")


def _exp_of(path_or_nodeid: str) -> str | None:
    m = _EXP_RE.search(path_or_nodeid)
    if m:
        return m.group(1)
    if "program/claims" in path_or_nodeid or path_or_nodeid.startswith("program"):
        return "program"     # cross-cutting program-level claims
    return None


def find_claims_dirs(root: Path) -> list[str]:
    """Every experiment's ``<exp>/analysis/claims`` plus the program-level
    ``program/claims`` (cross-cutting claims), in path order."""
    dirs = [p for p in root.glob("*/analysis/claims") if any(p.glob("test_*.py"))]
    prog = root / "program" / "claims"
    if any(prog.glob("test_*.py")):
        dirs.append(prog)
    return sorted(str(p) for p in dirs)


def run_claims(dirs: list[str], out_dir: Path) -> dict:
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
           *dirs, "--grounding-out", str(out_dir), "-o", "addopts="]
    # tolerate a non-zero exit (a failed/contradicted claim shouldn't abort the rollup)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    report = out_dir / "grounding_report.json"
    if not report.is_file():
        sys.stderr.write(proc.stdout[-4000:] + "\n" + proc.stderr[-2000:] + "\n")
        raise SystemExit("rollup: pytest did not produce a grounding report")
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    return {"claims": json.loads(report.read_text())["claims"], "pytest_summary": tail}


def aggregate(claims: list[dict]) -> dict:
    by_exp: dict[str, list[dict]] = defaultdict(list)
    for c in claims:
        by_exp[_exp_of(c["id"]) or "?"].append(c)

    cross = []   # claims whose evidence spans >1 experiment
    for c in claims:
        exps = {e for i in c.get("inputs", []) if (e := _exp_of(i["path"]))}
        home = _exp_of(c["id"])
        others = sorted(exps - {home})
        if others:
            cross.append({"id": c["id"], "home": home, "refs": others,
                          "statement": c.get("statement"), "kind": c["kind"],
                          "outcome": c["outcome"], "strength": c["strength"]})

    return {
        "n_experiments": len(by_exp),
        "n_claims": len(claims),
        "by_outcome": dict(Counter(c["outcome"] for c in claims)),
        "by_kind": dict(Counter(c["kind"] for c in claims)),
        "by_strength": dict(Counter(c["strength"] for c in claims)),
        "by_experiment": {e: {
            "n": len(cs),
            "kinds": dict(Counter(c["kind"] for c in cs)),
            "outcomes": dict(Counter(c["outcome"] for c in cs)),
        } for e, cs in sorted(by_exp.items())},
        "cross_experiment": cross,
        "claims": claims,
    }


_OUT = {"passed": "✅", "failed": "❌", "xfail": "⊘", "xpass": "⚠️", "skipped": "…"}


def render_md(agg: dict) -> str:
    L = ["# Program evidence — grounding rollup", ""]
    L.append(f"**{agg['n_claims']} claims across {agg['n_experiments']} experiments.**")
    L.append("")
    L.append("| outcome | n | | kind | n | | strength | n |")
    L.append("|---|--:|---|---|--:|---|---|--:|")
    outs = list(agg["by_outcome"].items()); kinds = list(agg["by_kind"].items())
    strs = list(agg["by_strength"].items())
    for i in range(max(len(outs), len(kinds), len(strs))):
        o = f"{_OUT.get(outs[i][0], '')} {outs[i][0]} | {outs[i][1]}" if i < len(outs) else " | "
        k = f"{kinds[i][0]} | {kinds[i][1]}" if i < len(kinds) else " | "
        s = f"{strs[i][0]} | {strs[i][1]}" if i < len(strs) else " | "
        L.append(f"| {o} | | {k} | | {s} |")
    L.append("")

    L.append("## By experiment")
    L.append("| experiment | claims | kinds | outcomes |")
    L.append("|---|--:|---|---|")
    for e, d in agg["by_experiment"].items():
        ks = " ".join(f"{k}:{n}" for k, n in d["kinds"].items())
        os_ = " ".join(f"{_OUT.get(k, k)}{n}" for k, n in d["outcomes"].items())
        L.append(f"| {e} | {d['n']} | {ks} | {os_} |")
    L.append("")

    L.append(f"## Cross-experiment claims ({len(agg['cross_experiment'])})")
    L.append("Claims whose evidence spans more than one experiment — the program graph.")
    L.append("")
    for c in agg["cross_experiment"]:
        L.append(f"- **{c['home']} → {', '.join(c['refs'])}** "
                 f"({_OUT.get(c['outcome'], c['outcome'])} {c['kind']}/{c['strength']}) "
                 f"`{c['id'].split('::')[-1]}`<br>{c.get('statement')}")
    L.append("")

    L.append("## All claims by kind")
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for c in agg["claims"]:
        by_kind[c["kind"]].append(c)
    for kind in sorted(by_kind):
        L.append(f"### {kind} ({len(by_kind[kind])})")
        for c in by_kind[kind]:
            ev = ", ".join(f"`{k}={v}`" for k, v in list(c.get("evidence", {}).items())[:4])
            L.append(f"- {_OUT.get(c['outcome'], c['outcome'])} **{_exp_of(c['id'])}** "
                     f"`{c['id'].split('::')[-1]}` · _{c['strength']}_<br>"
                     f"{c.get('statement')}" + (f"<br><sub>{ev}</sub>" if ev else ""))
        L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".", help="output dir for program_evidence.{md,json}")
    args = ap.parse_args()

    # SCIENTIST_HOME is the data-tree root.
    root = os.environ.get("SCIENTIST_HOME")
    if not root:
        raise SystemExit("set SCIENTIST_HOME to the experiments data repo")
    root = Path(root)
    dirs = find_claims_dirs(root)
    if not dirs:
        raise SystemExit(f"no <exp>/analysis/claims found under {root}")
    print(f"rolling up {len(dirs)} experiments' claims…", file=sys.stderr)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        run = run_claims(dirs, Path(tmp))
    agg = aggregate(run["claims"])
    (out / "program_evidence.json").write_text(json.dumps(agg, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
    (out / "program_evidence.md").write_text(render_md(agg), encoding="utf-8")
    print(f"  {run['pytest_summary']}", file=sys.stderr)
    print(f"  {agg['n_claims']} claims · {agg['n_experiments']} experiments · "
          f"{len(agg['cross_experiment'])} cross-experiment", file=sys.stderr)
    print(out / "program_evidence.md")


if __name__ == "__main__":
    main()
