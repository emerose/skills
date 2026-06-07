"""pytest plugin — collect claims, capture provenance, emit the grounding report.

A *claim* is a pytest test: its docstring is the statement, its node id is the stable
id, the fixtures it requests are its declared inputs, its body is the justification,
and its assert is the grounding/drift check. Markers carry the non-binary judgment
(``strength``/``caveats``/``kind``); lifecycle rides pytest states (``xfail`` =
contradicted/retracted, ``skip`` = unverifiable).

This plugin:
  * registers the markers (no "unknown mark" warnings),
  * wraps each test in an :class:`analyst.Capture` (autouse fixture) so every
    ``kicho``/``load``/``doc`` read is recorded, and installs the bypass guard,
  * runs the reconcile lint (declared fixtures vs captured inputs),
  * collects ``{id, statement, outcome, evidence, inputs+shas, strength, caveats,
    kind}`` per claim and writes ``grounding_report.md`` + ``.json``.

Auto-loaded via the ``pytest11`` entry point (see pyproject.toml), so a bare
``pytest analysis/claims/`` Just Works once the package is installed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import analyst

_MARKERS = {
    "strength": "strength(level): how strongly the evidence supports the claim",
    "caveats": "caveats(text): scope/limits to keep in mind",
    "kind": "kind(category): result|design|external|interpretive",
}


def pytest_configure(config):
    for name, help_ in _MARKERS.items():
        config.addinivalue_line("markers", help_)
    analyst.install_guard()
    config._analyst_records = []


def pytest_addoption(parser):
    g = parser.getgroup("analyst")
    g.addoption("--grounding-out", action="store", default=None,
                help="directory for grounding_report.{md,json} (default: rootdir)")


# --------------------------------------------------------------------------- #
# Per-claim capture
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _analyst_capture(request):
    """Set up a fresh capture for each claim and attach it to the item so the report
    hook can read it. Declared inputs = the non-internal fixtures the claim requested."""
    cap = analyst.Capture(claim_id=request.node.nodeid)
    cap.declared = {f for f in request.fixturenames
                    if not f.startswith("_") and f not in ("request", "tmp_path", "capsys")}
    token = analyst._CURRENT.set(cap)
    request.node._analyst_cap = cap
    try:
        yield cap
    finally:
        analyst._CURRENT.reset(token)


def _marker_val(item, name, default=None):
    m = item.get_closest_marker(name)
    if m is None:
        return default
    return m.args[0] if m.args else default


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    out = yield
    rep = out.get_result()
    if rep.when != "call":
        return
    cap = getattr(item, "_analyst_cap", None)
    # outcome: passed | failed | xfail | xpass | skipped
    outcome = rep.outcome
    if hasattr(rep, "wasxfail"):
        outcome = "xpass" if rep.passed else "xfail"
    elif rep.skipped and call.excinfo and call.excinfo.errisinstance(pytest.xfail.Exception):
        outcome = "xfail"

    statement = (item.function.__doc__ or "").strip() if hasattr(item, "function") else ""
    evidence = dict(cap.evidence) if cap else {}
    inputs = list(cap.inputs) if cap else []

    # reconcile lint: a kicho fixture requested but nothing from its experiment read,
    # or files captured whose experiment isn't among the declared fixtures.
    reconcile = _reconcile(cap) if cap else []

    rec = {
        "id": item.nodeid,
        "statement": statement,
        "outcome": outcome,
        "kind": _marker_val(item, "kind", "unspecified"),
        "strength": _marker_val(item, "strength", "unspecified"),
        "caveats": _marker_val(item, "caveats"),
        "evidence": evidence,
        "inputs": inputs,
        "bypassed": list(cap.bypassed) if cap else [],
        "reconcile": reconcile,
        "longrepr": str(rep.longrepr) if rep.failed and not getattr(rep, "wasxfail", None) else None,
    }
    item.config._analyst_records.append(rec)
    analyst.registry[item.nodeid] = rec  # enables uses(claim_id) for later claims


def _reconcile(cap: analyst.Capture) -> list[str]:
    """Warn when declared fixtures != captured inputs. Cheap, advisory."""
    msgs = []
    # kicho study fixtures look like k1_NNNNNN; their experiment id is the fixture name.
    declared_exps = {f.upper().replace("_", "-") for f in cap.declared
                     if f.lower().startswith("k1_")}
    captured_paths = [Path(i["path"]) for i in cap.inputs]
    captured_exps = set()
    for p in captured_paths:
        for part in p.parts:
            if part.upper().startswith("K1-"):
                captured_exps.add(part.split(" ")[0].upper())
    for e in declared_exps - captured_exps:
        msgs.append(f"declared fixture for {e} but read no file from it (dead fixture?)")
    for e in captured_exps - declared_exps:
        if declared_exps:  # only flag cross-experiment reads when fixtures were declared
            msgs.append(f"read files from {e} but no fixture declared it (undeclared input)")
    if cap.bypassed:
        msgs.append(f"{len(cap.bypassed)} untracked read(s) caught by the bypass guard")
    return msgs


# --------------------------------------------------------------------------- #
# Grounding report
# --------------------------------------------------------------------------- #
_OUTCOME_LABEL = {
    "passed": "✅ grounded", "failed": "❌ DRIFT", "xfail": "⊘ contradicted",
    "xpass": "⚠️ unexpectedly grounded", "skipped": "… unverifiable",
}


def pytest_sessionfinish(session):
    config = session.config
    records = getattr(config, "_analyst_records", [])
    if not records:
        return
    out_dir = Path(config.getoption("--grounding-out") or config.rootpath)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "grounding_report.json").write_text(
        json.dumps({"claims": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "grounding_report.md").write_text(_render_md(records), encoding="utf-8")
    config._analyst_report_path = out_dir / "grounding_report.md"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    p = getattr(config, "_analyst_report_path", None)
    if p is not None:
        terminalreporter.write_sep("-", "analyst grounding report")
        terminalreporter.write_line(f"  {p}")


def _short(path: str) -> str:
    """Trim a long absolute path to <experiment>/<rest> for the report."""
    parts = Path(path).parts
    for i, part in enumerate(parts):
        if part.upper().startswith("K1-"):
            return "/".join(parts[i:])
    return Path(path).name


def _render_md(records: list[dict]) -> str:
    from collections import Counter
    tally = Counter(r["outcome"] for r in records)
    lines = ["# Grounding report", ""]
    lines.append("| outcome | n |")
    lines.append("|---|---|")
    for k, v in tally.items():
        lines.append(f"| {_OUTCOME_LABEL.get(k, k)} | {v} |")
    lines.append("")
    by_kind: dict[str, list[dict]] = {}
    for r in records:
        by_kind.setdefault(r["kind"], []).append(r)
    for kind in sorted(by_kind):
        lines.append(f"## kind: {kind}")
        lines.append("")
        for r in by_kind[kind]:
            lines.append(f"### {_OUTCOME_LABEL.get(r['outcome'], r['outcome'])} — `{r['id'].split('::')[-1]}`")
            if r["statement"]:
                lines.append(f"> {r['statement']}")
            meta = f"**strength:** {r['strength']}"
            if r["caveats"]:
                meta += f" · **caveats:** {r['caveats']}"
            lines.append("")
            lines.append(meta)
            if r["evidence"]:
                ev = ", ".join(f"`{k}={v}`" for k, v in r["evidence"].items())
                lines.append(f"\n**evidence:** {ev}")
            if r["inputs"]:
                lines.append("\n**inputs:**")
                for i in r["inputs"]:
                    via = "" if i["via"] == "tracked" else f" _({i['via']})_"
                    lines.append(f"- `{i['kind']}` {_short(i['path'])} — `{i['sha256'][:12]}`{via}")
            if r["reconcile"]:
                lines.append("\n**reconcile:** " + "; ".join(r["reconcile"]))
            if r["longrepr"]:
                lines.append(f"\n```\n{r['longrepr'][:800]}\n```")
            lines.append("")
    return "\n".join(lines) + "\n"
