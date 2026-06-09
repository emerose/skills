"""Tests for the end-to-end traceability walk (provenance/trace.py) and the
store-decoupled `sci audit`.

Pure: synthetic experiment folders in tmp dirs, no keys, no libkit store. A trace
needs only an experiment.yml ledger (+ an optional grounding_report.json), so these
build those by hand and assert the GROUNDED/BROKEN verdict + break categories.
"""

import json
from pathlib import Path

import pytest
import yaml

import provenance as P
from provenance import trace as T


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _exp(tmp_path: Path, name: str = "K1-230101 - kd study") -> Path:
    """A clean raw -> data -> analysis chain on disk + the matching ledger.

    Layout (repo root = ``tmp_path``, experiment = ``tmp_path/<name>``):
        raw/measure.csv          (raw source)
        data/extract.py          (extract recipe)
        data/table.csv           (data artifact, from raw + recipe)
        analysis/derive.py       (derive recipe)
        analysis/tables/kd.csv   (analysis artifact, from data + recipe)
    """
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
    (exp / "experiment.yml").write_text(yaml.safe_dump(sidecar, sort_keys=False),
                                        encoding="utf-8")
    return exp


def _report(exp: Path, *, table: str = "analysis/tables/kd.csv") -> Path:
    """A grounding_report.json with one claim citing ``table`` (path absolute, as the
    analyst plugin records it). ``table`` is experiment-relative."""
    art = exp / table
    sha = P.sha256_file(art) if art.is_file() else "0" * 64
    report = {"claims": [{
        "id": "analysis/claims/test_kd.py::test_knockdown",
        "statement": "knockdown is 53%",
        "outcome": "passed",
        "kind": "result",
        "strength": "strong",
        "caveats": None,
        "evidence": {"kd_pct": 53},
        "inputs": [{"kind": "data", "path": str(art), "sha256": sha, "via": "tracked"}],
        "reconcile": [],
    }]}
    out = exp / "analysis" / "grounding_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# (a) clean chain + claim -> GROUNDED
# --------------------------------------------------------------------------- #
def test_clean_chain_is_grounded(tmp_path):
    exp = _exp(tmp_path)
    _report(exp)
    result = T.trace(exp, repo_root=tmp_path)
    assert result["status"] == "GROUNDED", result
    assert result["breaks"] == []
    assert len(result["chains"]) == 1
    ch = result["chains"][0]
    assert ch["kind"] == "claim"
    # the chain reaches the raw source
    assert any(T._is_raw(p) for p in ch["path_to_raw"]), ch["path_to_raw"]
    assert ch["breaks"] == []


# --------------------------------------------------------------------------- #
# (b) mutate a raw input -> drifted
# --------------------------------------------------------------------------- #
def test_drifted_raw_input(tmp_path):
    exp = _exp(tmp_path)
    _report(exp)
    # mutate the raw measurement after the ledger recorded its sha
    (exp / "raw" / "measure.csv").write_text("sample,cp\nA,99.9\nB,25.3\n", encoding="utf-8")
    result = T.trace(exp, repo_root=tmp_path)
    assert result["status"] == "BROKEN", result
    kinds = {(b["kind"], b["path"]) for b in result["breaks"]}
    assert ("drifted", "K1-230101 - kd study/raw/measure.csv") in kinds, result["breaks"]


# --------------------------------------------------------------------------- #
# (c) remove the raw input from a data/ edge -> unsourced
# --------------------------------------------------------------------------- #
def test_unsourced_data_edge(tmp_path):
    exp = _exp(tmp_path)
    _report(exp)
    # rewrite the ledger so the data edge has only the recipe (no raw source)
    sidecar = yaml.safe_load((exp / "experiment.yml").read_text())
    for e in sidecar["provenance"]:
        if e["artifact"] == "data/table.csv":
            e["inputs"] = [i for i in e["inputs"] if "/raw/" not in i["path"]]
    (exp / "experiment.yml").write_text(yaml.safe_dump(sidecar, sort_keys=False),
                                        encoding="utf-8")
    result = T.trace(exp, repo_root=tmp_path)
    assert result["status"] == "BROKEN", result
    kinds = {(b["kind"], b["path"]) for b in result["breaks"]}
    assert ("unsourced", "K1-230101 - kd study/data/table.csv") in kinds, result["breaks"]


# --------------------------------------------------------------------------- #
# (d) claim cites a nonexistent analysis table -> dangling
# --------------------------------------------------------------------------- #
def test_dangling_claim(tmp_path):
    exp = _exp(tmp_path)
    # report cites a table no edge produces and that isn't on disk
    art = exp / "analysis" / "tables" / "ghost.csv"
    report = {"claims": [{
        "id": "analysis/claims/test_x.py::test_ghost",
        "statement": "from a table that doesn't exist",
        "outcome": "passed", "kind": "result", "strength": "strong", "caveats": None,
        "evidence": {}, "reconcile": [],
        "inputs": [{"kind": "data", "path": str(art), "sha256": "0" * 64, "via": "tracked"}],
    }]}
    (exp / "analysis" / "grounding_report.json").write_text(json.dumps(report), encoding="utf-8")
    result = T.trace(exp, repo_root=tmp_path)
    assert result["status"] == "BROKEN", result
    kinds = {(b["kind"], b["path"]) for b in result["breaks"]}
    assert ("dangling", "K1-230101 - kd study/analysis/tables/ghost.csv") in kinds, result["breaks"]


# --------------------------------------------------------------------------- #
# ungrounded: a claim citing only a doc (no data/analysis artifact)
# --------------------------------------------------------------------------- #
def test_ungrounded_claim(tmp_path):
    exp = _exp(tmp_path)
    report = {"claims": [{
        "id": "analysis/claims/test_d.py::test_doc_only",
        "statement": "asserted from a slide deck only",
        "outcome": "passed", "kind": "external", "strength": "weak", "caveats": None,
        "evidence": {}, "reconcile": [],
        "inputs": [{"kind": "doc", "path": str(exp / "reports" / "cro.pptx"),
                    "sha256": "0" * 64, "via": "tracked"}],
    }]}
    (exp / "analysis" / "grounding_report.json").write_text(json.dumps(report), encoding="utf-8")
    result = T.trace(exp, repo_root=tmp_path)
    assert result["status"] == "BROKEN", result
    assert any(b["kind"] == "ungrounded" for b in result["breaks"]), result["breaks"]


# --------------------------------------------------------------------------- #
# --claim filter restricts the walk to one claim
# --------------------------------------------------------------------------- #
def test_claim_filter(tmp_path):
    exp = _exp(tmp_path)
    _report(exp)
    result = T.trace(exp, repo_root=tmp_path, claim_id="test_knockdown")
    assert len(result["chains"]) == 1
    assert result["chains"][0]["terminal"].endswith("test_knockdown")
    result_none = T.trace(exp, repo_root=tmp_path, claim_id="nope")
    assert result_none["chains"] == []


# --------------------------------------------------------------------------- #
# no report: README / analysis artifacts become the terminals
# --------------------------------------------------------------------------- #
def test_no_report_walks_artifacts(tmp_path):
    exp = _exp(tmp_path)
    result = T.trace(exp, repo_root=tmp_path)
    assert result["report"] is None
    assert result["status"] == "GROUNDED", result
    terminals = {c["terminal"] for c in result["chains"]}
    assert "K1-230101 - kd study/analysis/tables/kd.csv" in terminals


# --------------------------------------------------------------------------- #
# `sci audit <exp>` with NO store must not error and returns staleness
# --------------------------------------------------------------------------- #
def test_audit_without_store(tmp_path):
    exp = _exp(tmp_path)
    from store import cli as STORE_CLI

    # no .scientist store under tmp_path -> store_exists is False
    args = type("A", (), {"home": str(tmp_path), "experiment": str(exp), "json": True})()
    assert STORE_CLI.store_exists(args) is False
    report = STORE_CLI.audit_report(tmp_path, str(exp))
    assert len(report) == 1
    entry = report[0]
    assert entry["exp_id"] == "K1-230101"
    # clean chain -> up-to-date, and source_files is populated without a store
    assert entry["staleness"] == "up-to-date", entry
    assert any("raw/measure.csv" in s for s in entry["source_files"]), entry
