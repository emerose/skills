"""The report-rooted provenance trace — a report node atop the pipeline DAG.

A *report* sits at the terminal of ``raw → data → analysis → claims → report``: it fans in
across experiments through the ``[claim:<id>]`` citations it carries. This module walks the
DAG *down from a report* — parse its citations, resolve each to a live claim, and chain
each cited claim back to its raw measurements.

The per-experiment claim→raw walk is the host skill's job (it reads the experiment ledger),
so :func:`trace_report` takes it as an injected ``trace_fn``. That keeps this module — like
the rest of :mod:`reportkit` — free of any experiment-ledger or domain coupling: it owns the
report-rooted aggregation (resolve, dangling/ambiguous, break roll-up), and delegates the
per-claim chain to whatever per-experiment tracer the host supplies.

Pure (the report's citations + whatever ``trace_fn`` reads); store-free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import report as R


def _repo_rel(path: Path | str, repo_root: Path) -> str:
    """Normalize a path to a repo-root-relative POSIX string. An *absolute* path is resolved
    and made relative to ``repo_root``; a *relative* path is taken as already-repo-relative and
    only POSIX-normalized (never resolved against cwd, which would mangle it)."""
    p = Path(path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return p.name


def trace_report(report_path: Path, repo_root: Path | None = None, *,
                 trace_fn: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Walk the DAG *down from a report*: a report node sits atop the pipeline, walkable
    through each ``[claim:<id>]`` it cites to that claim's analysis → data → raw chain.

    The report is a terminal that fans in across experiments. We parse its citations,
    resolve each to a live claim (across every experiment's grounding report under
    ``repo_root``), and reuse the per-experiment claim tracer ``trace_fn`` to chain each cited
    claim to raw. ``trace_fn(exp_dir, repo_root=…, claim_id=…)`` is the host skill's
    experiment-rooted trace (it reads the experiment ledger). The report is **GROUNDED** only
    when every cited claim resolves *and* its chain is unbroken.

    Returns ``{report, terminals:[{cite, claim_id, experiment, path_to_raw, breaks}],
    breaks, status}``. Pure (the report's citations + whatever ``trace_fn`` reads)."""
    rp = Path(report_path).resolve()
    home = Path(repo_root).resolve() if repo_root is not None else R._infer_home(rp)
    parsed = R.parse_report(rp.read_text(encoding="utf-8"))
    claim_index = R.index_claims(home)

    terminals: list[dict[str, Any]] = []
    all_breaks: list[dict[str, str]] = []
    seen: set[str] = set()
    for cit in parsed["citations"]:
        cid = cit["id"]
        if cid in seen:
            continue
        seen.add(cid)
        cands = R.resolve_citation(cid, claim_index)
        if len(cands) != 1:
            kind = "dangling" if not cands else "ambiguous"
            br = {"kind": kind, "path": cid, "terminal": cid}
            all_breaks.append(br)
            terminals.append({"cite": cid, "claim_id": None, "experiment": None,
                              "path_to_raw": [], "breaks": [br]})
            continue
        claim = claim_index[cands[0]]
        exp_dir = Path(claim["exp_dir"])
        # reuse the per-experiment claim trace, keyed on the raw nodeid the report stored
        sub = trace_fn(exp_dir, repo_root=home, claim_id=claim.get("id"))
        chain = sub["chains"][0] if sub["chains"] else {"path_to_raw": [], "breaks": []}
        terminals.append({
            "cite": cid,
            "claim_id": cands[0],
            "experiment": claim.get("exp_id"),
            "path_to_raw": chain.get("path_to_raw", []),
            "breaks": chain.get("breaks", []),
        })
        all_breaks.extend(chain.get("breaks", []))

    status = "GROUNDED" if not all_breaks else "BROKEN"
    return {
        "report": _repo_rel(rp, home),
        "terminals": terminals,
        "breaks": all_breaks,
        "status": status,
    }


def render_report_trace(result: dict[str, Any]) -> str:
    """Human-readable report-rooted trace, matching the per-experiment trace render."""
    lines = [f"report {result['report']}: {result['status']}"]
    for t in result["terminals"]:
        verdict = "GROUNDED" if not t["breaks"] else "BROKEN"
        label = t.get("claim_id") or t["cite"]
        lines.append(f"  [claim] {label}: {verdict}")
        if t["path_to_raw"]:
            lines.append(f"      chain: {' <- '.join(t['path_to_raw'])}")
        for b in t["breaks"]:
            lines.append(f"      ! {b['kind']}: {b['path']}")
    return "\n".join(lines)
