"""pytest plugin — collect claims, capture provenance, emit the grounding report.

A *claim* is a pytest test: its docstring is the statement, its node id is the stable
id, the fixtures it requests are its declared inputs, its body is the justification,
and its assert is the grounding/drift check. Markers carry the non-binary judgment
(``strength``/``caveats``/``kind``); lifecycle rides pytest states (``xfail`` =
contradicted/retracted, ``skip`` = unverifiable).

This plugin:
  * registers the markers (no "unknown mark" warnings),
  * wraps each test in an :class:`analyst.Capture` (autouse fixture) so every
    ``experiments``/``load``/``doc`` read is recorded, and installs the bypass guard,
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
    g.addoption("--check-drift", action="store_true", default=False,
                help="flag claims whose captured inputs changed since the commit that "
                     "last set their @strength marker (git-based; needs EXPERIMENTS_ROOT "
                     "to be the data repo). Off by default to keep runs fast + git-free.")


# --------------------------------------------------------------------------- #
# Experiment access — zero-boilerplate, resolved from the test's location.
# --------------------------------------------------------------------------- #
def _home_exp(node_path) -> str | None:
    """The K1-NNNNNN experiment code whose tree this test file lives in (its
    ``analysis/claims/`` dir is under ``<exp>/``). Found by walking up to the folder
    that holds an ``experiment.yml`` and is named ``K1-...``."""
    p = Path(str(node_path))
    for parent in p.parents:
        if parent.name.upper().startswith("K1-") and (parent / "experiment.yml").is_file():
            return parent.name.split(" ")[0].upper()
    return None


@pytest.fixture
def experiment(request):
    """The :class:`Study` whose ``analysis/claims/`` this test lives in — resolved from
    the test file's path, so **no per-experiment conftest is needed**. Use as
    ``def test_x(experiment): ...``. (Cross-experiment claims still import a specific
    other study via ``from experiments import k1_NNNNNN``.)"""
    import experiments as _exp
    code = _home_exp(request.node.path)
    if code is None:
        raise RuntimeError(
            f"no enclosing K1-* experiment for {request.node.path} "
            f"(is the claim under <exp>/analysis/claims/ next to an experiment.yml?)")
    return getattr(_exp, code.lower().replace("-", "_"))   # cached Study


# --------------------------------------------------------------------------- #
# Per-claim capture
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _analyst_capture(request):
    """Set up a fresh capture for each claim and attach it to the item so the report
    hook can read it. ``declared`` = the experiment codes the claim is expected to
    touch: its home experiment (from the test path) + any explicitly-named
    ``k1_NNNNNN`` fixtures (cross-experiment claims)."""
    cap = analyst.Capture(claim_id=request.node.nodeid)
    exps = set()
    home = _home_exp(request.node.path)
    if home:
        exps.add(home)
    for f in request.fixturenames:
        if f.lower().startswith("k1_"):
            exps.add(f.upper().replace("_", "-"))
    cap.declared = exps
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

    # reconcile lint: the claim's experiment was declared but nothing read from it, or
    # files were read from an experiment the claim didn't declare (undeclared input).
    # Skipped claims read nothing by design (unverifiable-from-this-data), so the
    # "empty claim?" check doesn't apply — don't cry wolf on them.
    reconcile = _reconcile(cap, skipped=(outcome == "skipped")) if cap else []

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
    if cap is not None and item.config.getoption("--check-drift"):
        rec["drift"] = _compute_drift(item, cap)
    item.config._analyst_records.append(rec)
    analyst.registry[item.nodeid] = rec  # enables uses(claim_id) for later claims


# --------------------------------------------------------------------------- #
# Drift — did a claim's inputs change since its belief (@strength) was last set?
# --------------------------------------------------------------------------- #
def _git(root, *args):
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None


def _strength_line(item) -> tuple | None:
    """(repo-relative test file, 1-based line of the @strength marker) for this claim,
    or the def line if it has no @strength. None if the source can't be located."""
    import inspect
    try:
        src_lines, start = inspect.getsourcelines(item.function)
    except (OSError, TypeError):
        return None
    off = next((i for i, ln in enumerate(src_lines) if ln.lstrip().startswith("@strength")), None)
    line = start + (off if off is not None else 0)
    return item.function.__code__.co_filename, line


def _compute_drift(item, cap) -> dict:
    """Compare each captured input to its state at the commit that last set this claim's
    @strength marker. Any changed input => the evidence moved since the belief was
    affirmed => stale (re-judge). Pure git; degrades gracefully when unavailable."""
    root = analyst._data_root()
    if root is None or not (Path(root) / ".git").exists():
        return {"checked": False, "note": "EXPERIMENTS_ROOT is not a git repo"}
    loc = _strength_line(item)
    if loc is None:
        return {"checked": False, "note": "claim source unavailable"}
    src_file, line = loc
    try:
        rel_file = Path(src_file).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return {"checked": False, "note": "claim file outside EXPERIMENTS_ROOT"}
    blame = _git(root, "blame", "-L", f"{line},{line}", "--porcelain", "--", str(rel_file))
    if blame is None or blame.returncode != 0 or not blame.stdout:
        return {"checked": False, "note": "git blame failed"}
    commit = blame.stdout.split(None, 1)[0]
    if set(commit) == {"0"}:
        return {"checked": True, "stale": False, "strength_commit": None,
                "changed_inputs": [], "note": "@strength edited but not yet committed"}
    changed = []
    for i in cap.inputs:
        try:
            rel = Path(i["path"]).resolve().relative_to(Path(root).resolve())
        except ValueError:
            continue
        diff = _git(root, "diff", "--quiet", commit, "--", str(rel))
        if diff is not None and diff.returncode == 1:   # 1 = differs from that commit
            changed.append(str(rel))
    return {"checked": True, "stale": bool(changed),
            "strength_commit": commit[:10], "changed_inputs": changed}


def _reconcile(cap: analyst.Capture, skipped: bool = False) -> list[str]:
    """Warn when the claim's declared experiments != the experiments it actually read
    from. ``cap.declared`` already holds experiment codes (home + named fixtures).
    Cheap, advisory. A ``skipped`` claim reads nothing by design, so the "empty claim?"
    half is suppressed for it (an undeclared-read or bypass would still be flagged)."""
    msgs = []
    declared_exps = set(cap.declared)
    captured_exps = set()
    for i in cap.inputs:
        for part in Path(i["path"]).parts:
            if part.upper().startswith("K1-"):
                captured_exps.add(part.split(" ")[0].upper())
    if not skipped:
        for e in declared_exps - captured_exps:
            msgs.append(f"claim is in/declares {e} but read no file from it (empty claim?)")
    for e in captured_exps - declared_exps:
        msgs.append(f"read files from {e} but the claim didn't declare it "
                    f"(undeclared cross-experiment input — name it via a k1_{e[3:]} fixture)")
    if cap.bypassed:
        msgs.append(f"{len(cap.bypassed)} untracked read(s) caught by the bypass guard")
    return msgs


# --------------------------------------------------------------------------- #
# Grounding report
# --------------------------------------------------------------------------- #
def _json_default(o):
    """JSON fallback for evidence values that are numpy/pandas scalars or arrays.
    Claim bodies often pass ``df[col].nunique()`` (numpy int64), ``float(...)`` aside —
    coerce those to native types so the report export never fails on a stray numpy type."""
    if hasattr(o, "item"):           # numpy scalar (int64/float64/bool_) -> python scalar
        try:
            return o.item()
        except (ValueError, TypeError):
            pass
    if hasattr(o, "tolist"):         # numpy array / pandas Index/Series -> list
        return o.tolist()
    return str(o)                    # last resort: a readable string


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
        json.dumps({"claims": records}, indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8")
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
            d = r.get("drift")
            if d and d.get("checked"):
                if d.get("stale"):
                    lines.append(f"\n**⚠️ drift:** inputs changed since the @strength commit "
                                 f"`{d['strength_commit']}` — re-judge: "
                                 + ", ".join(f"`{_short(c)}`" for c in d["changed_inputs"]))
                else:
                    note = d.get("note")
                    lines.append(f"\n**drift:** ✓ fresh"
                                 + (f" ({note})" if note else f" (vs `{d.get('strength_commit')}`)"))
            if r["longrepr"]:
                lines.append(f"\n```\n{r['longrepr'][:800]}\n```")
            lines.append("")
    return "\n".join(lines) + "\n"
