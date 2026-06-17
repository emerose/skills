"""Program-level traceability rollup (ROADMAP §4).

`sci trace <exp>` answers "is *this* experiment's evidence grounded?" and
`sci trace <report.md>` answers it for one report. This module rolls those
per-target verdicts up across the whole data tree into ONE status answering the
program-level question: **"is the program's stated evidence fully grounded?"**

The program is GROUNDED only when every tracked experiment *and* every report
traces cleanly to raw; otherwise it is BROKEN and the offenders (with their break
categories) are the worklist. Pure + store-free, exactly like :mod:`.trace`: it
walks the ``experiment.yml`` ledgers and on-disk ``grounding_report.json`` already
present in the tree — it runs no claims and touches no libkit store. Run the claims
first (e.g. via ``scripts/rollup.py``) if you want the per-experiment grounding
reports fresh before rolling up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import trace as T


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def find_experiments(home: Path) -> list[Path]:
    """Every tracked experiment under ``home`` — a depth-1 dir carrying an
    ``experiment.yml`` ledger. Includes ``program/`` when it has one (its
    cross-experiment derivations are a DAG worth tracing too)."""
    exps = [p.parent for p in home.glob("*/experiment.yml")]
    return sorted(set(exps), key=lambda p: p.name)


def find_reports(home: Path) -> list[Path]:
    """Every report Markdown under ``home`` — ``program/reports/<slug>/report.md``
    and ``<exp>/reports/<slug>/report.md`` (both match ``*/reports/*/report.md``)."""
    return sorted(home.glob("*/reports/*/report.md"), key=lambda p: p.as_posix())


# --------------------------------------------------------------------------- #
# the rollup
# --------------------------------------------------------------------------- #
def program_trace(home: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """Roll up the per-experiment and per-report trace verdict across ``home``.

    Returns ``{home, status, n_experiments, n_reports, n_broken_experiments,
    n_broken_reports, experiments:[{experiment, status, n_chains, breaks}],
    reports:[{report, status, n_cited, breaks}]}``. ``status`` is ``"GROUNDED"``
    iff no experiment and no report is BROKEN.
    """
    home = Path(home).resolve()
    repo = Path(repo_root).resolve() if repo_root is not None else home

    experiments: list[dict[str, Any]] = []
    for exp in find_experiments(home):
        res = T.trace(exp, repo_root=repo)
        experiments.append({
            "experiment": res["experiment"],
            "status": res["status"],
            "n_chains": len(res.get("chains", [])),
            "breaks": res.get("breaks", []),
        })

    reports: list[dict[str, Any]] = []
    for rp in find_reports(home):
        res = T.trace_report(rp, repo_root=repo)
        reports.append({
            "report": res["report"],
            "status": res["status"],
            "n_cited": len(res.get("terminals", [])),
            "breaks": res.get("breaks", []),
        })

    n_broken_exp = sum(1 for e in experiments if e["status"] != "GROUNDED")
    n_broken_rep = sum(1 for r in reports if r["status"] != "GROUNDED")
    status = "GROUNDED" if not n_broken_exp and not n_broken_rep else "BROKEN"
    return {
        "home": str(home),
        "status": status,
        "n_experiments": len(experiments),
        "n_reports": len(reports),
        "n_broken_experiments": n_broken_exp,
        "n_broken_reports": n_broken_rep,
        "experiments": experiments,
        "reports": reports,
    }


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _breaks_summary(breaks: list[dict[str, Any]]) -> str:
    """`kind(path)` per break, deduped in first-seen order — the worklist cell."""
    seen: list[str] = []
    for b in breaks:
        label = f"{b.get('kind', '?')}({b.get('path', b.get('terminal', '?'))})"
        if label not in seen:
            seen.append(label)
    return "; ".join(seen)


def render(result: dict[str, Any]) -> str:
    """Markdown 'Program traceability' section: the one-line verdict + a worklist
    table of only the BROKEN targets (a fully grounded program needs no table)."""
    L: list[str] = ["## Program traceability", ""]
    ne, nr = result["n_experiments"], result["n_reports"]
    if result["status"] == "GROUNDED":
        L.append(f"**GROUNDED** — all {ne} experiments and {nr} reports trace cleanly to raw.")
        return "\n".join(L) + "\n"

    nbe, nbr = result["n_broken_experiments"], result["n_broken_reports"]
    L.append(f"**BROKEN** — {nbe}/{ne} experiments and {nbr}/{nr} reports have a broken chain. "
             "The program's stated evidence is **not** fully grounded; the targets below are the worklist.")
    L.append("")
    L.append("| target | kind | breaks |")
    L.append("|---|---|---|")
    for e in result["experiments"]:
        if e["status"] != "GROUNDED":
            L.append(f"| {e['experiment']} | experiment | {_breaks_summary(e['breaks'])} |")
    for r in result["reports"]:
        if r["status"] != "GROUNDED":
            L.append(f"| {r['report']} | report | {_breaks_summary(r['breaks'])} |")
    return "\n".join(L) + "\n"
