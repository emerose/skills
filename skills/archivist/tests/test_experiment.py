"""Unit tests for _experiment: the YAML sidecar schema + the provenance fingerprint.

The fingerprint tests pin down the documented algorithm so it can't drift into
"mysterious staleness": reproducible by hand, order/location independent, and blind
to the prose (README.md) and the sidecar itself.
"""

import hashlib

import pytest

import _experiment as E


def _exp(tmp_path):
    d = tmp_path / "K1-1 - Exp"
    (d / "data").mkdir(parents=True)
    (d / "raw").mkdir()
    (d / "data" / "kd.csv").write_text("a,b\n1,2\n")
    (d / "raw" / "plate.eds").write_bytes(b"\x00\x01raw")
    return d


# ---- fingerprint -----------------------------------------------------------
def test_fingerprint_matches_hand_computation(tmp_path):
    d = _exp(tmp_path)
    fp, n, manifest = E.compute_fingerprint(d)
    # rebuild the expected manifest by hand: "<sha>  <rel>\n", sorted by rel
    import _files
    entries = sorted([
        (_files.sha256_file(d / "data" / "kd.csv"), "data/kd.csv"),
        (_files.sha256_file(d / "raw" / "plate.eds"), "raw/plate.eds"),
    ], key=lambda e: e[1])
    expected_manifest = "".join(f"{sha}  {rel}\n" for sha, rel in entries)
    assert manifest == expected_manifest
    assert n == 2
    assert fp == "sha256:" + hashlib.sha256(expected_manifest.encode("utf-8")).hexdigest()


def test_fingerprint_deterministic_and_order_independent(tmp_path):
    d = _exp(tmp_path)
    fp1, _, _ = E.compute_fingerprint(d)
    fp2, _, _ = E.compute_fingerprint(d)
    assert fp1 == fp2                                  # stable across runs
    # adding a file in a different "creation order" still yields a path-sorted manifest
    (d / "data" / "aaa.csv").write_text("x\n")
    fp3, n3, _ = E.compute_fingerprint(d)
    assert fp3 != fp1 and n3 == 3                      # new evidence -> changes


def test_fingerprint_tracks_content_changes(tmp_path):
    d = _exp(tmp_path)
    fp1, _, _ = E.compute_fingerprint(d)
    (d / "data" / "kd.csv").write_text("a,b\n9,9\n")   # change bytes
    fp2, _, _ = E.compute_fingerprint(d)
    assert fp2 != fp1
    (d / "data" / "kd.csv").write_text("a,b\n1,2\n")   # restore exact bytes
    fp3, _, _ = E.compute_fingerprint(d)
    assert fp3 == fp1                                  # same bytes -> same fingerprint


def test_fingerprint_ignores_readme_and_sidecar(tmp_path):
    d = _exp(tmp_path)
    fp1, n1, _ = E.compute_fingerprint(d)
    (d / "README.md").write_text("# prose changes constantly\n")
    (d / "experiment.yml").write_text("exp_id: K1-1\nstatus: active\n")
    fp2, n2, _ = E.compute_fingerprint(d)
    assert fp2 == fp1 and n2 == n1                     # prose + sidecar are not evidence


def test_fingerprint_skips_zero_byte_files(tmp_path):
    d = _exp(tmp_path)
    fp1, n1, _ = E.compute_fingerprint(d)
    (d / "raw" / "empty.dat").write_bytes(b"")
    fp2, n2, _ = E.compute_fingerprint(d)
    assert fp2 == fp1 and n2 == n1                     # 0-byte files excluded


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


def test_stamp_provenance_records_fingerprint(tmp_path):
    d = _exp(tmp_path)
    stamped = E.stamp_provenance({"exp_id": "K1-1"}, d, today="2026-06-03")
    fp, n, _ = E.compute_fingerprint(d)
    assert stamped["provenance"]["data_fingerprint"] == fp
    assert stamped["provenance"]["n_inputs"] == n
    assert stamped["provenance"]["reviewed_at"] == "2026-06-03"
