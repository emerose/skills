"""Tests for the report phase (`scientist.report`, ROADMAP §5).

Pure: a synthetic data tree in a tmp dir — a couple of grounding_report.json claim
indexes, an experiment.yml ledger pinning one analysis artifact, and a report.md that
cites claims and embeds the artifact. Asserts the audit verdicts (grounded citation OK;
contradicted/unresolved citation blocking; tracked vs untracked vs drifted exhibit) and
that `--format md` rendering emits the Grounded-claims footnote appendix.
"""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import scientist.report as R


def _write_json(p: Path, claims: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"claims": claims}), encoding="utf-8")


def _claim(nodeid, outcome="passed", strength="strong", kind="result", statement="x"):
    return {"id": nodeid, "outcome": outcome, "strength": strength,
            "kind": kind, "statement": statement, "evidence": {}, "inputs": []}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A minimal home: one experiment with a grounded + a contradicted claim and a
    sha-pinned figure artifact, plus a program claim. Returns the home path."""
    home = tmp_path
    monkeypatch.setenv("SCIENTIST_HOME", str(home))

    exp = home / "K1-230101 - kd study"
    # grounding report: one grounded result, one contradicted (xfail)
    _write_json(exp / "analysis" / "grounding_report.json", [
        _claim("K1-230101 - kd study/analysis/claims/test_K1_230101.py::test_kd",
               outcome="passed", strength="strong",
               statement="ASO knocks down target by ~50%."),
        _claim("K1-230101 - kd study/analysis/claims/test_K1_230101.py::test_potent",
               outcome="xfail", strength="weak",
               statement="ASO is the most potent (contradicted)."),
    ])
    # program claim
    _write_json(home / "program" / "analysis" / "grounding_report.json", [
        _claim("program/claims/test_program_lead.py::test_lead",
               outcome="passed", strength="moderate",
               statement="X is the lead."),
    ])

    # a figure artifact + its ledger entry (sha-pinned)
    fig = exp / "analysis" / "fig" / "f.png"
    fig.parent.mkdir(parents=True, exist_ok=True)
    fig.write_bytes(b"\x89PNG\r\n\x1a\nFAKEFIGURE")
    sha = hashlib.sha256(fig.read_bytes()).hexdigest()
    (exp / "experiment.yml").write_text(yaml.safe_dump({
        "provenance": [{"artifact": "analysis/fig/f.png", "artifact_sha256": sha,
                        "inputs": []}]}), encoding="utf-8")
    return home


def _audit(home: Path, md: str):
    rep = home / "program" / "reports" / "r"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "report.md").write_text(md, encoding="utf-8")
    idx = R.build_claim_index(home)
    arts = R.build_artifact_index(home)
    return R.audit(rep / "report.md", idx, arts)


def test_grounded_citation_passes(tree):
    findings, summary = _audit(tree, "Result ~50% [claim:test_kd].\n")
    assert summary["n_unique_claims"] == 1
    assert not [f for f in findings if f.severity == "blocking"]


def test_full_claim_id_and_program_id_resolve(tree):
    md = ("A [claim:K1-230101::test_K1_230101.py::test_kd] and "
          "B [claim:program::test_program_lead.py::test_lead].\n")
    findings, summary = _audit(tree, md)
    assert summary["n_unique_claims"] == 2
    assert not [f for f in findings if f.severity == "blocking"]


def test_contradicted_citation_is_blocking(tree):
    findings, _ = _audit(tree, "Most potent ~9 nM [claim:test_potent].\n")
    block = [f for f in findings if f.severity == "blocking"]
    assert block and "not a grounded positive backing" in block[0].message


def test_unresolved_citation_is_blocking(tree):
    findings, _ = _audit(tree, "Claim ~5% [claim:test_does_not_exist].\n")
    assert any("unresolved" in f.message for f in findings if f.severity == "blocking")


def test_tracked_exhibit_ok_untracked_and_drift_blocking(tree, tmp_path):
    # tracked figure (sha matches the ledger) -> no exhibit finding
    md_ok = "![cap](../../../K1-230101 - kd study/analysis/fig/f.png)\n"
    findings, _ = _audit(tree, md_ok)
    assert not [f for f in findings if f.kind == "exhibit"]

    # untracked figure (exists, no provenance edge) -> blocking
    rep = tree / "program" / "reports" / "r"
    (rep / "adhoc.png").write_bytes(b"\x89PNGadhoc")
    findings, _ = _audit(tree, "![cap](adhoc.png)\n")
    assert any("not a tracked analysis artifact" in f.message
               for f in findings if f.kind == "exhibit")


def test_drifted_exhibit_is_blocking(tree):
    fig = tree / "K1-230101 - kd study" / "analysis" / "fig" / "f.png"
    fig.write_bytes(b"\x89PNG\r\n\x1a\nDIFFERENTBYTES")          # bytes drift from recorded sha
    findings, _ = _audit(tree, "![cap](../../../K1-230101 - kd study/analysis/fig/f.png)\n")
    assert any("drifted" in f.message for f in findings if f.kind == "exhibit")


def test_uncited_quantitative_paragraph_is_advisory(tree):
    findings, _ = _audit(tree, "The ASO reduced target by 50% with no citation here.\n")
    adv = [f for f in findings if f.severity == "advisory"]
    assert adv and adv[0].kind == "uncited"


def test_render_md_emits_grounded_claims_appendix(tree):
    rep = tree / "program" / "reports" / "r"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "report.md").write_text(
        "---\ntitle: T\n---\nResult [claim:test_kd].\n", encoding="utf-8")
    rc = R.run(str(rep), fmt="md", out=str(rep / "out.md"), audit_only=False)
    assert rc == 0
    out = (rep / "out.md").read_text(encoding="utf-8")
    assert "# T" in out                       # title rendered as H1
    assert "[^c1]" in out                     # citation -> footnote ref
    assert "Grounded claims" in out and "test_kd" in out
