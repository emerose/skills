"""Tests for the program-level traceability rollup (provenance/program.py, ROADMAP §4).

Pure: a synthetic data tree (clean + broken experiments, grounded + broken reports)
in a tmp dir, no keys, no libkit store. We assert the rolled-up GROUNDED/BROKEN
verdict and that the worklist names the offenders. Builders mirror test_trace.py.
"""

import json
from pathlib import Path

import yaml

import scientist.provenance as P
from scientist.provenance import program as PROG


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _exp(home: Path, name: str, exp_id: str, node: str, *, break_raw: bool = False) -> Path:
    """A clean raw -> data -> analysis chain + ledger + a grounding report whose one
    claim (node id ``node``) cites the analysis table. ``break_raw`` mutates the raw
    source after the ledger recorded its sha, drifting the data edge -> BROKEN."""
    exp = home / name
    for sub in ("raw", "data", "analysis/tables", "analysis/claims"):
        (exp / sub).mkdir(parents=True, exist_ok=True)
    raw = exp / "raw" / "m.csv"; raw.write_text("s,cp\nA,20.1\n", encoding="utf-8")
    erec = exp / "data" / "extract.py"; erec.write_text("def build(x):\n    return x\n", encoding="utf-8")
    data = exp / "data" / "t.csv"; data.write_text("s,dcp\nA,1.0\n", encoding="utf-8")
    drec = exp / "analysis" / "derive.py"; drec.write_text("# derive\n", encoding="utf-8")
    ana = exp / "analysis" / "tables" / "kd.csv"; ana.write_text("metric,value\nkd_pct,53\n", encoding="utf-8")

    def rel(p: Path) -> str:
        return p.resolve().relative_to(home.resolve()).as_posix()

    def inp(p: Path) -> dict:
        return {"path": rel(p), "sha256": P.sha256_file(p)}

    sidecar = {
        "exp_id": exp_id,
        "name": name,
        "provenance": [
            {"artifact": "data/t.csv", "artifact_sha256": P.sha256_file(data),
             "reviewed_at": "2026-06-08", "inputs": [inp(raw), inp(erec)]},
            {"artifact": "analysis/tables/kd.csv", "artifact_sha256": P.sha256_file(ana),
             "reviewed_at": "2026-06-08", "inputs": [inp(data), inp(drec)]},
        ],
    }
    (exp / "experiment.yml").write_text(yaml.safe_dump(sidecar, sort_keys=False), encoding="utf-8")

    report = {"claims": [{
        "id": f"analysis/claims/test_kd.py::{node}",
        "statement": "knockdown is 53%", "outcome": "passed", "kind": "result",
        "strength": "strong", "caveats": None, "evidence": {"kd_pct": 53},
        "inputs": [{"kind": "data", "path": str(ana), "sha256": P.sha256_file(ana), "via": "tracked"}],
        "reconcile": [],
    }]}
    (exp / "analysis" / "grounding_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    if break_raw:
        raw.write_text("s,cp\nA,99.9\n", encoding="utf-8")  # drift the raw input after the sha was recorded
    return exp


def _report(home: Path, slug: str, cite_node: str) -> Path:
    """A program report citing one claim by its bare node name."""
    d = home / "program" / "reports" / slug
    d.mkdir(parents=True, exist_ok=True)
    md = d / "report.md"
    md.write_text(f"# {slug}\n\nThe knockdown is 53% [claim:{cite_node}].\n", encoding="utf-8")
    return md


# --------------------------------------------------------------------------- #
# (a) every experiment + report clean -> GROUNDED
# --------------------------------------------------------------------------- #
def test_all_grounded(tmp_path):
    _exp(tmp_path, "K1-230101 - a", "K1-230101", "test_kd_a")
    _exp(tmp_path, "K1-230102 - b", "K1-230102", "test_kd_b")
    _report(tmp_path, "story", "test_kd_a")

    res = PROG.program_trace(tmp_path)
    assert res["status"] == "GROUNDED", res
    assert res["n_experiments"] == 2 and res["n_reports"] == 1
    assert res["n_broken_experiments"] == 0 and res["n_broken_reports"] == 0
    # the report folder (program/) has no experiment.yml, so it isn't counted as an experiment
    assert {e["experiment"] for e in res["experiments"]} == {"K1-230101 - a", "K1-230102 - b"}
    md = PROG.render(res)
    assert "GROUNDED" in md and "| target |" not in md  # no worklist table when clean


# --------------------------------------------------------------------------- #
# (b) a drifted experiment -> BROKEN, named in the worklist
# --------------------------------------------------------------------------- #
def test_broken_experiment(tmp_path):
    _exp(tmp_path, "K1-230101 - clean", "K1-230101", "test_kd_a")
    _exp(tmp_path, "K1-230102 - drifted", "K1-230102", "test_kd_b", break_raw=True)

    res = PROG.program_trace(tmp_path)
    assert res["status"] == "BROKEN", res
    assert res["n_experiments"] == 2 and res["n_broken_experiments"] == 1
    broken = [e for e in res["experiments"] if e["status"] != "GROUNDED"]
    assert [e["experiment"] for e in broken] == ["K1-230102 - drifted"]
    assert any(b["kind"] == "drifted" for b in broken[0]["breaks"]), broken
    md = PROG.render(res)
    assert "BROKEN" in md and "K1-230102 - drifted" in md and "drifted" in md


# --------------------------------------------------------------------------- #
# (c) a report citing a nonexistent claim -> dangling -> program BROKEN
# --------------------------------------------------------------------------- #
def test_broken_report_dangling(tmp_path):
    _exp(tmp_path, "K1-230101 - a", "K1-230101", "test_kd_a")
    _report(tmp_path, "bad-story", "test_missing")  # no such claim anywhere

    res = PROG.program_trace(tmp_path)
    assert res["status"] == "BROKEN", res
    assert res["n_experiments"] == 1 and res["n_broken_experiments"] == 0
    assert res["n_reports"] == 1 and res["n_broken_reports"] == 1
    rep = res["reports"][0]
    assert rep["status"] == "BROKEN"
    assert any(b["kind"] == "dangling" for b in rep["breaks"]), rep
    assert "program/reports/bad-story/report.md" in PROG.render(res)


# --------------------------------------------------------------------------- #
# (d) a report citing a clean claim -> GROUNDED report
# --------------------------------------------------------------------------- #
def test_report_grounded_via_clean_claim(tmp_path):
    _exp(tmp_path, "K1-230101 - a", "K1-230101", "test_kd_a")
    _report(tmp_path, "good-story", "test_kd_a")

    res = PROG.program_trace(tmp_path)
    assert res["status"] == "GROUNDED", res
    assert res["n_reports"] == 1 and res["reports"][0]["status"] == "GROUNDED"
    assert res["reports"][0]["n_cited"] == 1


# --------------------------------------------------------------------------- #
# (e) empty tree -> vacuously GROUNDED, JSON-serializable
# --------------------------------------------------------------------------- #
def test_empty_tree_is_grounded(tmp_path):
    res = PROG.program_trace(tmp_path)
    assert res["status"] == "GROUNDED"
    assert res["n_experiments"] == 0 and res["n_reports"] == 0
    json.dumps(res)  # the rollup is machine-readable
