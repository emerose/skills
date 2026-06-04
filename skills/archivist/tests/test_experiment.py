"""Unit tests for _experiment: the YAML sidecar schema + the explicit-inputs
provenance model (review records each input file + sha; audit reports per-file drift;
README renders + the sidecar are never counted as inputs)."""

import pytest

import _experiment as E


def _exp(tmp_path):
    """An experiment folder under a repo root (tmp_path). Returns (home, exp_dir)."""
    d = tmp_path / "K1-1 - Exp"
    (d / "data").mkdir(parents=True)
    (d / "raw").mkdir()
    (d / "data" / "kd.csv").write_text("a,b\n1,2\n")
    (d / "raw" / "plate.eds").write_bytes(b"\x00\x01raw")
    (d / "README.md").write_text("# prose\n")
    (d / "experiment.yml").write_text("exp_id: K1-1\n")
    return tmp_path, d


# ---- provenance: inputs ----------------------------------------------------
def test_in_folder_data_files_excludes_readme_and_sidecar(tmp_path):
    home, d = _exp(tmp_path)
    (d / "README.pdf").write_bytes(b"%PDF render")     # a render, must be excluded too
    paths = E.in_folder_data_files(home, d)
    assert paths == ["K1-1 - Exp/data/kd.csv", "K1-1 - Exp/raw/plate.eds"]  # repo-rel, sorted
    assert not any("README" in p or "experiment.yml" in p for p in paths)


def test_review_records_explicit_inputs_and_artifact_sha(tmp_path):
    home, d = _exp(tmp_path)
    sidecar, missing = E.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04")
    assert missing == []
    entry = sidecar["provenance"][0]
    assert entry["artifact"] == "README.md"
    assert entry["reviewed_at"] == "2026-06-04"
    assert {i["path"] for i in entry["inputs"]} == {"K1-1 - Exp/data/kd.csv", "K1-1 - Exp/raw/plate.eds"}
    assert all(len(i["sha256"]) == 64 for i in entry["inputs"])    # real hashes
    assert len(entry["artifact_sha256"]) == 64


def test_review_includes_and_preserves_external_inputs(tmp_path):
    home, d = _exp(tmp_path)
    ext = home / "Shared" / "deck.pptx"; ext.parent.mkdir(); ext.write_bytes(b"slides")
    sidecar, missing = E.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04",
                                extra_inputs=["Shared/deck.pptx"])
    assert "Shared/deck.pptx" in {i["path"] for i in sidecar["provenance"][0]["inputs"]}
    # a re-review (no extra_inputs given) preserves the external one
    sidecar2, _ = E.review(home, d, sidecar, today="2026-06-05")
    assert "Shared/deck.pptx" in {i["path"] for i in sidecar2["provenance"][0]["inputs"]}


def test_review_reports_missing_declared_input(tmp_path):
    home, d = _exp(tmp_path)
    _, missing = E.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04",
                          extra_inputs=["Shared/gone.pptx"])
    assert missing == ["Shared/gone.pptx"]


# ---- provenance: staleness -------------------------------------------------
def test_staleness_up_to_date_then_input_change(tmp_path):
    home, d = _exp(tmp_path)
    sidecar, _ = E.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04")
    assert E.staleness(home, d, sidecar)["state"] == "up-to-date"
    (d / "data" / "kd.csv").write_text("a,b\n9,9\n")    # an input changed
    st = E.staleness(home, d, sidecar)
    assert st["state"] == "stale" and st["changed"] == ["K1-1 - Exp/data/kd.csv"]


def test_staleness_detects_added_and_artifact_edit(tmp_path):
    home, d = _exp(tmp_path)
    sidecar, _ = E.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04")
    (d / "data" / "new.csv").write_text("x\n")          # new data not yet recorded
    st = E.staleness(home, d, sidecar)
    assert st["state"] == "stale" and "K1-1 - Exp/data/new.csv" in st["added"]
    # editing the README itself also flags stale
    sidecar2, _ = E.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04")  # re-record incl new.csv
    (d / "README.md").write_text("# edited prose\n")
    assert E.staleness(home, d, sidecar2)["artifact_changed"] is True


def test_staleness_no_provenance(tmp_path):
    home, d = _exp(tmp_path)
    assert E.staleness(home, d, {"exp_id": "K1-1"})["state"] == "no-provenance"


# ---- schema / validation ---------------------------------------------------
def test_validate_normalises_and_requires_exp_id():
    out = E.validate({"exp_id": "K1-1", "status": "Completed", "asos": ["ASO-1"],
                      "cro_study_ids": ["C0790222"], "model": None})
    assert out["status"] == "complete"                 # synonym normalised
    assert out["asos"] == ["ASO-1"]
    assert "model" not in out                           # None-valued known field dropped
    with pytest.raises(E.SidecarError):
        E.validate({"status": "active"})               # missing exp_id


def test_validate_rejects_unknown_field_and_bad_types():
    with pytest.raises(E.SidecarError):
        E.validate({"exp_id": "K1-1", "crooo": "typo"})        # unknown field
    with pytest.raises(E.SidecarError):
        E.validate({"exp_id": "K1-1", "assays": "qPCR"})       # list given a string
    with pytest.raises(E.SidecarError):
        E.validate({"exp_id": "K1-1", "status": "ongoingish"}) # bad status


def test_dump_then_read_roundtrip(tmp_path):
    data = E.validate({"exp_id": "K1-1", "cro": "Charles River", "status": "complete",
                       "assays": ["qPCR", "Transfection"], "asos": ["ASO-1"],
                       "cro_study_ids": ["CRL SOW 1, Experiment 14"]})
    p = tmp_path / "experiment.yml"
    p.write_text(E.dump_sidecar(data))
    back = E.read_sidecar(p)
    assert back == data
    assert "archivist structured metadata" in p.read_text()    # header comment present


def test_provenance_list_roundtrips_and_legacy_dropped(tmp_path):
    home, d = _exp(tmp_path)
    sidecar, _ = E.review(home, d, E.validate({"exp_id": "K1-1"}), today="2026-06-04")
    p = tmp_path / "out.yml"
    p.write_text(E.dump_sidecar(sidecar))
    back = E.read_sidecar(p)
    assert back["provenance"] == sidecar["provenance"]          # list survives roundtrip
    # a legacy dict-shaped provenance is dropped on validate (-> re-review needed)
    legacy = E.validate({"exp_id": "K1-1", "provenance": {"data_fingerprint": "sha256:x"}})
    assert "provenance" not in legacy
