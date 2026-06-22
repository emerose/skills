"""The report-rooted trace — :mod:`reportkit.trace`.

``trace_report`` walks a report down through its ``[claim:<id>]`` citations, resolving each to
a live claim and delegating the per-claim → raw chain to an injected ``trace_fn`` (the host
skill's experiment-rooted tracer). These tests build a grounding report + a citing report and
inject a stub ``trace_fn`` so the report-rooted aggregation (resolve, dangling, break roll-up)
is exercised without any experiment-ledger code.
"""

import json
from pathlib import Path

from reportkit import trace as T


def _grounding(home: Path, *, node="test_kd") -> Path:
    exp = home / "K1-230101 - kd"
    (exp / "analysis").mkdir(parents=True)
    report = {"claims": [{
        "id": f"analysis/claims/test_kd.py::{node}",
        "statement": "knockdown is 53%", "outcome": "passed", "kind": "result",
        "strength": "strong", "evidence": {"kd_pct": 53}, "inputs": []}]}
    (exp / "analysis" / "grounding_report.json").write_text(
        json.dumps(report), encoding="utf-8")
    return exp


def _report(home: Path, body: str) -> Path:
    d = home / "program" / "reports" / "top"
    d.mkdir(parents=True)
    md = d / "report.md"
    md.write_text(body, encoding="utf-8")
    return md


def test_report_grounded_when_every_cited_chain_is_clean(tmp_path):
    _grounding(tmp_path)
    md = _report(tmp_path, "# top\n\nWe saw 53% [claim:test_kd].\n")

    def trace_fn(exp_dir, repo_root=None, claim_id=None):
        return {"chains": [{"path_to_raw": ["K1-230101/raw/x.csv"], "breaks": []}]}

    res = T.trace_report(md, repo_root=tmp_path, trace_fn=trace_fn)
    assert res["status"] == "GROUNDED"
    assert res["terminals"][0]["claim_id"].endswith("::test_kd")
    assert res["terminals"][0]["path_to_raw"] == ["K1-230101/raw/x.csv"]


def test_report_broken_when_a_cited_chain_breaks(tmp_path):
    _grounding(tmp_path)
    md = _report(tmp_path, "# top\n\n[claim:test_kd].\n")

    def trace_fn(exp_dir, repo_root=None, claim_id=None):
        return {"chains": [{"path_to_raw": [], "breaks": [{"kind": "drifted", "path": "raw/x.csv"}]}]}

    res = T.trace_report(md, repo_root=tmp_path, trace_fn=trace_fn)
    assert res["status"] == "BROKEN"
    assert res["breaks"][0]["kind"] == "drifted"


def test_unresolvable_citation_is_dangling(tmp_path):
    _grounding(tmp_path)
    md = _report(tmp_path, "# top\n\n[claim:test_ghost].\n")

    def trace_fn(exp_dir, repo_root=None, claim_id=None):  # pragma: no cover - not reached
        raise AssertionError("trace_fn must not be called for an unresolvable cite")

    res = T.trace_report(md, repo_root=tmp_path, trace_fn=trace_fn)
    assert res["status"] == "BROKEN"
    assert res["terminals"][0]["breaks"][0]["kind"] == "dangling"


def test_render_report_trace_is_readable(tmp_path):
    _grounding(tmp_path)
    md = _report(tmp_path, "# top\n\n[claim:test_kd].\n")

    def trace_fn(exp_dir, repo_root=None, claim_id=None):
        return {"chains": [{"path_to_raw": ["K1-230101/raw/x.csv"], "breaks": []}]}

    res = T.trace_report(md, repo_root=tmp_path, trace_fn=trace_fn)
    out = T.render_report_trace(res)
    assert "GROUNDED" in out
    assert "chain: K1-230101/raw/x.csv" in out
