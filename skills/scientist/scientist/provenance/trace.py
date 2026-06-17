"""End-to-end traceability over the one provenance DAG — ``sci trace``.

A *pure* provenance walk: it reads only the experiment's ``experiment.yml`` ledger and,
when present, its ``grounding_report.json`` (the grounding claims). It re-hashes the
recorded inputs on disk (reusing :func:`provenance.staleness`) but NEVER opens the libkit
store and never re-runs an analysis — reproduction is out of scope. It answers one
question per terminal: *does this claim / artifact chain back to a raw measurement, and
where (if anywhere) is the chain broken?*

## The DAG

One ``provenance`` list per experiment holds every edge ``artifact <- inputs``:

* ``data/…``     ← raw source(s) + the extract recipe
* ``analysis/…`` ← data file(s) + the derive recipe
* ``README.md``  ← the in-folder data files (the review edge)

Plus, optionally, a layer above the ledger: each *claim* in ``grounding_report.json``
cites ``inputs`` (``data/`` / ``analysis/`` tables and ``doc`` reports) as its backing.

A terminal (a claim, or — with no report — a README/top artifact) is **GROUNDED** when it
walks back to at least one ``raw/`` source with no break along the way.

## Break categories (each names the offending file)

* ``missing``    — a recorded input file is absent on disk.
* ``drifted``    — a recorded input's bytes differ from its recorded sha (reuses staleness).
* ``unsourced``  — a ``data/`` edge with no ``raw/`` input, or an ``analysis/`` edge with no
                   ``data/`` input (a derived artifact with nothing measured under it).
* ``dangling``   — a claim/edge references an artifact or data file that no edge produces
                   and that isn't on disk (a citation into thin air).
* ``ungrounded`` — a claim whose inputs include no ``data/`` or ``analysis/`` artifact
                   (a pure assertion, grounded in nothing computed).

Stdlib + PyYAML only; pure, no keys, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import _load_raw, edges, staleness

GROUNDING_REPORT_NAME = "grounding_report.json"


# --------------------------------------------------------------------------- #
# path helpers
# --------------------------------------------------------------------------- #
def _repo_rel(path: Path | str, repo_root: Path) -> str:
    """Normalize a path to a repo-root-relative POSIX string.

    Ledger/claim inputs come in two forms: already-repo-relative (the common case, e.g.
    ``K1-…/raw/x.csv``) or absolute (a derivation whose repo_root differed via a
    ``/var`` vs ``/private/var`` symlink). An *absolute* path is resolved and made
    relative to ``repo_root``; a *relative* path is taken as already-repo-relative and
    only POSIX-normalized (never resolved against cwd, which would mangle it)."""
    p = Path(path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return p.name


def _under(rel: str, sub: str) -> bool:
    """A repo-relative path that lives under an experiment's ``<sub>/`` folder (e.g.
    ``raw`` / ``data`` / ``analysis``)."""
    return f"/{sub}/" in ("/" + rel)


def _is_raw(rel: str) -> bool:
    """A repo-relative input path that lives under an experiment's ``raw/`` folder."""
    return _under(rel, "raw")


def _kind_of(artifact: str) -> str:
    """Classify an artifact path by its prefix: ``data`` / ``analysis`` / ``readme``."""
    if artifact.startswith("data/"):
        return "data"
    if artifact.startswith("analysis/"):
        return "analysis"
    if artifact.startswith("README"):
        return "readme"
    return "other"


# --------------------------------------------------------------------------- #
# grounding report
# --------------------------------------------------------------------------- #
def find_report(exp_dir: Path, override: Path | str | None = None) -> Path | None:
    """Locate the grounding report: ``override`` if given, else
    ``<exp>/analysis/grounding_report.json`` then ``<exp>/grounding_report.json``."""
    if override is not None:
        p = Path(override)
        return p if p.is_file() else None
    exp = Path(exp_dir)
    for cand in (exp / "analysis" / GROUNDING_REPORT_NAME, exp / GROUNDING_REPORT_NAME):
        if cand.is_file():
            return cand
    return None


def _load_claims(report_path: Path | None) -> list[dict[str, Any]]:
    if report_path is None:
        return []
    data = json.loads(Path(report_path).read_text(encoding="utf-8"))
    return list(data.get("claims") or [])


# --------------------------------------------------------------------------- #
# the walk
# --------------------------------------------------------------------------- #
def trace(exp_dir: Path, repo_root: Path | None = None, *,
          report_path: Path | str | None = None, claim_id: str | None = None) -> dict[str, Any]:
    """Walk the provenance DAG for one experiment and report traceability.

    Returns ``{experiment, chains, breaks, status}`` where each chain is
    ``{terminal, kind, path_to_raw:[...], breaks:[...]}`` and ``status`` is
    ``"GROUNDED"`` (no break) or ``"BROKEN"``. See the module docstring for the break
    categories. Pure: reads the ledger + (optional) grounding report only.
    """
    exp = Path(exp_dir).resolve()
    home = Path(repo_root).resolve() if repo_root is not None else exp.parent
    # Read the ledger LENIENTLY: a provenance-only experiment.yml (no exp_id yet, as the
    # extractor stamps before metadata is filled) is a valid DAG to trace — don't require
    # a full schema validation. ``edges`` just needs the provenance list.
    sidecar = _load_raw(exp)
    prov = edges(sidecar)

    # artifact (repo-relative) -> {inputs:[rel...], raw_inputs, data_inputs, kind}
    edge_by_artifact: dict[str, dict[str, Any]] = {}
    produced: set[str] = set()          # repo-rel artifact paths an edge produces
    for e in prov:
        artifact = str(e.get("artifact", ""))
        art_rel = _repo_rel(exp / artifact, home)
        produced.add(art_rel)
        # Input paths in the ledger are usually repo-root-relative, but a derivation
        # whose repo_root differs (e.g. a /var vs /private/var symlink) records absolute
        # paths. Normalize every input to repo-relative so edges + claims match by key.
        in_rels = [_repo_rel(i["path"], home) for i in (e.get("inputs") or []) if i.get("path")]
        edge_by_artifact[art_rel] = {
            "artifact": artifact,
            "kind": _kind_of(artifact),
            "inputs": in_rels,
        }

    # Per-input drift/missing — reuse the staleness core so trace and audit agree.
    # Normalize its (recorded-as) paths the same way edge inputs are normalized.
    st = staleness(exp, repo_root=home)
    changed = {_repo_rel(p, home) for p in (st.get("changed") or [])}
    missing = {_repo_rel(p, home) for p in (st.get("missing") or [])}

    breaks: list[dict[str, str]] = []
    seen_breaks: set[tuple] = set()

    def add_break(kind: str, path: str, terminal: str | None = None) -> None:
        key = (kind, path, terminal)
        if key in seen_breaks:
            return
        seen_breaks.add(key)
        b = {"kind": kind, "path": path}
        if terminal is not None:
            b["terminal"] = terminal
        breaks.append(b)

    # ---- structural edge checks (independent of any claim) -----------------
    for art_rel, edge in edge_by_artifact.items():
        in_rels = edge["inputs"]
        if edge["kind"] == "data" and not any(_is_raw(p) for p in in_rels):
            add_break("unsourced", art_rel)
        if edge["kind"] == "analysis" and not any(_under(p, "data") for p in in_rels):
            add_break("unsourced", art_rel)
        for p in in_rels:
            if p in missing:
                add_break("missing", p)
            elif p in changed:
                add_break("drifted", p)

    def _resolve_to_raw(rel: str, terminal: str, chain: list[str]) -> None:
        """Walk one artifact/data input back toward raw, recording breaks. ``chain`` is
        the accumulating path-to-raw (repo-relative), terminal-first."""
        chain.append(rel)
        if _is_raw(rel):
            return
        edge = edge_by_artifact.get(rel)
        if edge is None:
            # not produced by any edge and not raw — dangling unless it's an on-disk
            # leaf the ledger simply doesn't track (then it's just an untraced input).
            if not (home / rel).is_file():
                add_break("dangling", rel, terminal)
            return
        # recurse into each upstream input (prefer raw/data edges; recipes are leaves)
        upstream = [p for p in edge["inputs"]]
        for p in upstream:
            if p in chain:                       # guard against cycles
                continue
            if _is_raw(p):
                chain.append(p)
                continue
            if p in edge_by_artifact:
                _resolve_to_raw(p, terminal, chain)
            # a recipe (.py) or other leaf input: not a measurement, not dangling.

    chains: list[dict[str, Any]] = []

    # ---- terminals from the grounding report (claims), if present ----------
    report = find_report(exp, report_path)
    all_claims = _load_claims(report)
    claims = all_claims
    if claim_id is not None:
        claims = [c for c in all_claims if c.get("id") == claim_id
                  or c.get("id", "").split("::")[-1] == claim_id]

    # A report present but no claim matching --claim: report nothing (don't silently
    # fall back to walking artifacts, which would mask a bad --claim).
    if all_claims and not claims:
        return {"experiment": _repo_rel(exp, home) if exp != home else exp.name,
                "report": _repo_rel(report, home) if report else None,
                "chains": [], "breaks": [], "status": "GROUNDED"}

    if claims:
        for c in claims:
            terminal = c.get("id", "?")
            cins = c.get("inputs") or []
            grounded_kinds = []
            chain: list[str] = []
            for ci in cins:
                kind = ci.get("kind")
                rel = _repo_rel(ci.get("path", ""), home)
                # a claim is "grounded" only by inputs that are computed artifacts
                # (data/ or analysis/); a doc/other input alone is a pure assertion.
                if kind in ("data", "analysis") or _under(rel, "data") or _under(rel, "analysis"):
                    grounded_kinds.append(kind)
                    # a claim citing a path that no edge produces and isn't on disk:
                    if rel not in produced and not (home / rel).is_file():
                        add_break("dangling", rel, terminal)
                        chain.append(rel)
                        continue
                    _resolve_to_raw(rel, terminal, chain)
            if not grounded_kinds:
                add_break("ungrounded", terminal, terminal)
            cl_breaks = [b for b in breaks
                         if b.get("terminal") == terminal
                         or (b.get("path") in chain and "terminal" not in b)]
            chains.append({"terminal": terminal, "kind": "claim",
                           "path_to_raw": chain, "breaks": cl_breaks})
    else:
        # ---- no claims: terminals are the README + top analysis artifacts -----
        terminals = [a for a in edge_by_artifact
                     if edge_by_artifact[a]["kind"] in ("readme", "analysis")]
        # if there are analysis edges, README is a parallel terminal; if only data
        # edges exist, those data artifacts are the terminals.
        if not terminals:
            terminals = list(edge_by_artifact)
        for art_rel in terminals:
            chain = []
            _resolve_to_raw(art_rel, art_rel, chain)
            t_breaks = [b for b in breaks
                        if b.get("terminal") == art_rel
                        or (b.get("path") in chain and "terminal" not in b)]
            chains.append({"terminal": art_rel,
                           "kind": edge_by_artifact[art_rel]["kind"],
                           "path_to_raw": chain, "breaks": t_breaks})

    status = "GROUNDED" if not breaks else "BROKEN"
    return {
        "experiment": _repo_rel(exp, home) if exp != home else exp.name,
        "report": _repo_rel(report, home) if report else None,
        "chains": chains,
        "breaks": breaks,
        "status": status,
    }


# --------------------------------------------------------------------------- #
# report-rooted trace — a report node atop the DAG
# --------------------------------------------------------------------------- #
def trace_report(report_path: Path, repo_root: Path | None = None) -> dict[str, Any]:
    """Walk the DAG *down from a report*: a report node sits atop the pipeline, walkable
    through each ``[claim:<id>]`` it cites to that claim's analysis → data → raw chain.

    The report is a terminal that fans in across experiments. We parse its citations,
    resolve each to a live claim (across every experiment's grounding report under
    ``repo_root``), and reuse the per-experiment :func:`trace` to chain each cited claim to
    raw. The report is **GROUNDED** only when every cited claim resolves *and* its chain is
    unbroken.

    Returns ``{report, terminals:[{cite, claim_id, experiment, path_to_raw, breaks}],
    breaks, status}``. Pure (ledger + grounding reports only); store-free, like
    :func:`trace`."""
    from . import report as R   # local import: avoid a module-load cycle

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
        sub = trace(exp_dir, repo_root=home, claim_id=claim.get("id"))
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
    """Human-readable report-rooted trace, matching the per-experiment :func:`render`."""
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


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render(result: dict[str, Any]) -> str:
    """Human-readable traceability status for one experiment's trace result."""
    lines = [f"{result['experiment']}: {result['status']}"]
    if result.get("report"):
        lines.append(f"  grounding report: {result['report']}")
    for ch in result["chains"]:
        term = ch["terminal"]
        short = term.split("::")[-1] if ch["kind"] == "claim" else term
        chain = ch["path_to_raw"]
        verdict = "GROUNDED" if not ch["breaks"] else "BROKEN"
        lines.append(f"  [{ch['kind']}] {short}: {verdict}")
        if chain:
            lines.append(f"      chain: {' <- '.join(chain)}")
        for b in ch["breaks"]:
            lines.append(f"      ! {b['kind']}: {b['path']}")
    extra = [b for b in result["breaks"]
             if not any(b in ch["breaks"] for ch in result["chains"])]
    for b in extra:
        lines.append(f"  ! {b['kind']}: {b['path']}")
    return "\n".join(lines)
