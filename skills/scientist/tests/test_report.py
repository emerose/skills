"""The report phase as scientist drives it — the report-rooted trace integration + the
store-contract check.

The GENERIC report engine (parse / audit / render / scope) lives in ``reportkit`` and is tested
there (skills/reportkit/tests). The ``[lit:]`` literature citation layer moved to the ``research``
skill in the scientist/research split and is tested there (skills/research/tests/test_report_lit.py).
What remains here is scientist's own EXPERIMENT surface, reached through the ``provenance.report``
shim: the ``provenance.trace.trace_report`` wrapper (report -> claim -> raw through the real
experiment ledger), and the claim_id contract with the store.

Pure: synthetic experiment folders in tmp dirs — no keys, no libkit store, no ``$SCIENTIST_HOME``.
"""

import json
from pathlib import Path

import yaml

import scientist.provenance as P
from reportkit import report as R
from scientist.provenance import trace as T


def _exp(tmp_path: Path, name: str = "K1-230101 - kd study") -> Path:
    """A clean raw -> data -> analysis chain + ledger (mirrors test_trace._exp)."""
    exp = tmp_path / name
    for sub in ("raw", "data", "analysis/tables"):
        (exp / sub).mkdir(parents=True, exist_ok=True)
    raw = exp / "raw" / "measure.csv"
    raw.write_text("sample,cp\nA,20.1\nB,25.3\n", encoding="utf-8")
    erec = exp / "data" / "extract.py"
    erec.write_text("def build(x):\n    return x\n", encoding="utf-8")
    data = exp / "data" / "table.csv"
    data.write_text("sample,dcp\nA,1.0\nB,5.2\n", encoding="utf-8")
    drec = exp / "analysis" / "derive.py"
    drec.write_text("# derive\n", encoding="utf-8")
    ana = exp / "analysis" / "tables" / "kd.csv"
    ana.write_text("metric,value\nkd_pct,53\n", encoding="utf-8")

    def rel(p: Path) -> str:
        return p.resolve().relative_to(tmp_path.resolve()).as_posix()

    def inp(p: Path) -> dict:
        return {"path": rel(p), "sha256": P.sha256_file(p)}

    sidecar = {
        "exp_id": "K1-230101",
        "name": "kd study",
        "provenance": [
            {"artifact": "data/table.csv", "artifact_sha256": P.sha256_file(data),
             "reviewed_at": "2026-06-08", "inputs": [inp(raw), inp(erec)]},
            {"artifact": "analysis/tables/kd.csv", "artifact_sha256": P.sha256_file(ana),
             "reviewed_at": "2026-06-08", "inputs": [inp(data), inp(drec)]},
        ],
    }
    (exp / "experiment.yml").write_text(yaml.safe_dump(sidecar, sort_keys=False), encoding="utf-8")
    return exp


def _report_json(exp: Path, *, outcome="passed", strength="strong",
                 node="test_knockdown", table="analysis/tables/kd.csv") -> Path:
    art = exp / table
    sha = P.sha256_file(art) if art.is_file() else "0" * 64
    report = {"claims": [{
        "id": f"analysis/claims/test_kd.py::{node}",
        "statement": "knockdown is 53% at the top dose",
        "outcome": outcome, "kind": "result", "strength": strength, "caveats": None,
        "evidence": {"kd_pct": 53},
        "inputs": [{"kind": "data", "path": str(art), "sha256": sha, "via": "tracked"}],
        "reconcile": [],
    }]}
    out = exp / "analysis" / "grounding_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out


def _report_md(exp: Path, body: str, slug: str = "summary") -> Path:
    d = exp / "reports" / slug
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text(body, encoding="utf-8")
    return md


_GOOD_BODY = """\
# Knockdown summary

We observed sustained knockdown of 53% at the top dose [claim:test_knockdown].

![knockdown table](../../analysis/tables/kd.csv)
"""


def test_trace_report_grounded(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)

    result = T.trace_report(md, repo_root=tmp_path)
    assert result["status"] == "GROUNDED", result
    assert len(result["terminals"]) == 1
    term = result["terminals"][0]
    assert term["claim_id"] == "K1-230101::test_kd.py::test_knockdown"
    assert term["experiment"] == "K1-230101"
    assert any(T._is_raw(p) for p in term["path_to_raw"]), term["path_to_raw"]


def test_trace_report_broken_on_drift(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, _GOOD_BODY)
    # drift a raw input under the cited claim's chain
    (exp / "raw" / "measure.csv").write_text("sample,cp\nA,99.9\nB,25.3\n", encoding="utf-8")
    result = T.trace_report(md, repo_root=tmp_path)
    assert result["status"] == "BROKEN", result
    assert any(b["kind"] == "drifted" for b in result["breaks"])


def test_trace_report_missing_cite(tmp_path):
    exp = _exp(tmp_path)
    _report_json(exp)
    md = _report_md(exp, "# X\n\n[claim:test_ghost]\n")
    result = T.trace_report(md, repo_root=tmp_path)
    assert result["status"] == "BROKEN"
    assert any(b["kind"] == "dangling" for b in result["breaks"])


def test_claim_id_matches_store_meta():
    from scientist.store import _meta as M
    nodeid = "/abs/K1-230101 - x/analysis/claims/test_kd.py::test_knockdown"
    assert R.claim_id_for("K1-230101", nodeid) == M.claim_id_for("K1-230101", nodeid)
