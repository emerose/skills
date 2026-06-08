"""Tests for the shared provenance core (experiment.yml sidecar + ledger).

Pure: no keys, no network, synthetic tmp dirs only. Covers round-trip sidecar,
status-synonym normalization, unknown-key rejection, provenance merge/dedup/sort,
and staleness detection of changed/missing/added inputs.
"""

import pytest

import provenance as P


# --------------------------------------------------------------------------- #
# validate / round-trip
# --------------------------------------------------------------------------- #
def test_roundtrip_sidecar(tmp_path):
    data = {
        "exp_id": "K1-230101",
        "name": "kd study",
        "status": "active",
        "assays": ["qpcr", "crc"],
    }
    P.write_sidecar(tmp_path, data)
    # deterministic: a second write is byte-identical
    first = (tmp_path / "experiment.yml").read_bytes()
    P.write_sidecar(tmp_path, data)
    assert (tmp_path / "experiment.yml").read_bytes() == first

    loaded = P.read_sidecar(tmp_path)
    assert loaded["exp_id"] == "K1-230101"
    assert loaded["name"] == "kd study"
    assert loaded["status"] == "active"
    assert loaded["assays"] == ["qpcr", "crc"]


def test_read_missing_sidecar_is_empty(tmp_path):
    assert P.read_sidecar(tmp_path) == {}


def test_status_synonym_normalization():
    assert P.validate({"exp_id": "X", "status": "completed"})["status"] == "complete"
    assert P.validate({"exp_id": "X", "status": "In Progress"})["status"] == "active"
    assert P.validate({"exp_id": "X", "status": "Cancelled"})["status"] == "terminated"
    # canonical values pass through
    assert P.validate({"exp_id": "X", "status": "draft"})["status"] == "draft"


def test_bad_status_rejected():
    with pytest.raises(P.SidecarError, match="not recognised"):
        P.validate({"exp_id": "X", "status": "wibble"})


def test_unknown_key_rejected():
    with pytest.raises(P.SidecarError, match="unknown field"):
        P.validate({"exp_id": "X", "bogus": 1})


def test_exp_id_required():
    with pytest.raises(P.SidecarError, match="exp_id"):
        P.validate({"name": "no id"})


def test_list_field_must_be_list():
    with pytest.raises(P.SidecarError, match="must be a YAML list"):
        P.validate({"exp_id": "X", "assays": "qpcr"})


# --------------------------------------------------------------------------- #
# provenance merge / dedup / sort / preserve
# --------------------------------------------------------------------------- #
def _entry(artifact, inputs, sha="aa", when="2026-01-01"):
    return {"artifact": artifact, "artifact_sha256": sha, "reviewed_at": when,
            "inputs": [{"path": p, "sha256": s} for p, s in inputs]}


def test_record_provenance_merge_dedup_sort_preserve(tmp_path):
    # seed with a README review edge + a data edge owned by an earlier run
    P.write_sidecar(tmp_path, {
        "exp_id": "E1",
        "provenance": [
            _entry("README.md", [("E1/raw/notes.txt", "n1")], sha="rr"),
            _entry("data/02_b.csv", [("E1/raw/old.xlsx", "o1")], sha="bb_old"),
        ],
    })

    # record two data edges: one new, one replacing data/02_b.csv
    P.record_provenance(tmp_path, [
        _entry("data/02_b.csv", [("E1/raw/new.xlsx", "n2")], sha="bb_new"),
        _entry("data/01_a.csv", [("E1/raw/a.xlsx", "a1")], sha="aa_new"),
    ])

    sc = P.read_sidecar(tmp_path)
    prov = sc["provenance"]
    arts = [e["artifact"] for e in prov]
    # sorted by artifact, README preserved, 02_b deduped (replaced not duplicated)
    assert arts == ["README.md", "data/01_a.csv", "data/02_b.csv"]
    b = next(e for e in prov if e["artifact"] == "data/02_b.csv")
    assert b["artifact_sha256"] == "bb_new"
    assert b["inputs"] == [{"path": "E1/raw/new.xlsx", "sha256": "n2"}]
    # README untouched
    readme = next(e for e in prov if e["artifact"] == "README.md")
    assert readme["inputs"] == [{"path": "E1/raw/notes.txt", "sha256": "n1"}]


def test_edges_prefix_filter(tmp_path):
    sc = {"provenance": [
        _entry("README.md", []),
        _entry("data/01_a.csv", []),
        _entry("analysis/fit.csv", []),
    ]}
    assert [e["artifact"] for e in P.edges(sc, "data/")] == ["data/01_a.csv"]
    assert [e["artifact"] for e in P.edges(sc, "analysis/")] == ["analysis/fit.csv"]
    assert [e["artifact"] for e in P.edges(sc, "README")] == ["README.md"]
    assert len(P.edges(sc)) == 3


# --------------------------------------------------------------------------- #
# staleness
# --------------------------------------------------------------------------- #
def _make_exp(tmp_path):
    """An experiment dir under a repo root, with raw/ + data/ files recorded."""
    repo = tmp_path
    exp = repo / "E1"
    (exp / "raw").mkdir(parents=True)
    (exp / "data").mkdir(parents=True)
    src = exp / "raw" / "a.xlsx"
    src.write_bytes(b"hello")
    art = exp / "data" / "01_a.csv"
    art.write_bytes(b"col\n1\n")
    return repo, exp, src, art


def test_staleness_up_to_date(tmp_path):
    repo, exp, src, art = _make_exp(tmp_path)
    P.write_sidecar(exp, {
        "exp_id": "E1",
        "provenance": [{
            "artifact": "data/01_a.csv",
            "artifact_sha256": P.sha256_file(art),
            "reviewed_at": "2026-01-01",
            "inputs": [{"path": "E1/raw/a.xlsx", "sha256": P.sha256_file(src)}],
        }],
    })
    st = P.staleness(exp, repo_root=repo)
    assert st["state"] == "up-to-date"


def test_staleness_no_provenance(tmp_path):
    repo, exp, src, art = _make_exp(tmp_path)
    P.write_sidecar(exp, {"exp_id": "E1"})
    assert P.staleness(exp, repo_root=repo)["state"] == "no-provenance"


def test_staleness_detects_changed(tmp_path):
    repo, exp, src, art = _make_exp(tmp_path)
    P.write_sidecar(exp, {
        "exp_id": "E1",
        "provenance": [{
            "artifact": "data/01_a.csv",
            "artifact_sha256": P.sha256_file(art),
            "reviewed_at": "2026-01-01",
            "inputs": [{"path": "E1/raw/a.xlsx", "sha256": P.sha256_file(src)}],
        }],
    })
    src.write_bytes(b"changed!")  # mutate the recorded input
    st = P.staleness(exp, repo_root=repo)
    assert st["state"] == "stale"
    assert st["changed"] == ["E1/raw/a.xlsx"]
    assert st["missing"] == []


def test_staleness_detects_missing(tmp_path):
    repo, exp, src, art = _make_exp(tmp_path)
    P.write_sidecar(exp, {
        "exp_id": "E1",
        "provenance": [{
            "artifact": "data/01_a.csv",
            "artifact_sha256": P.sha256_file(art),
            "reviewed_at": "2026-01-01",
            "inputs": [{"path": "E1/raw/a.xlsx", "sha256": P.sha256_file(src)}],
        }],
    })
    src.unlink()
    st = P.staleness(exp, repo_root=repo)
    assert st["state"] == "stale"
    assert st["missing"] == ["E1/raw/a.xlsx"]


def test_staleness_detects_added(tmp_path):
    repo, exp, src, art = _make_exp(tmp_path)
    P.write_sidecar(exp, {
        "exp_id": "E1",
        "provenance": [{
            "artifact": "data/01_a.csv",
            "artifact_sha256": P.sha256_file(art),
            "reviewed_at": "2026-01-01",
            "inputs": [{"path": "E1/raw/a.xlsx", "sha256": P.sha256_file(src)}],
        }],
    })
    # a brand-new in-folder data file not yet recorded under any artifact
    (exp / "raw" / "b.xlsx").write_bytes(b"new raw")
    st = P.staleness(exp, repo_root=repo)
    assert st["state"] == "stale"
    assert "E1/raw/b.xlsx" in st["added"]


def test_in_folder_inputs_excludes_readme_and_sidecar(tmp_path):
    exp = tmp_path / "E1"
    (exp / "raw").mkdir(parents=True)
    (exp / "raw" / "a.xlsx").write_bytes(b"x")
    (exp / "README.md").write_text("prose")
    (exp / "experiment.yml").write_text("exp_id: E1\n")
    (exp / "data").mkdir()
    (exp / "data" / "01_a.csv").write_bytes(b"c\n")
    names = {p.name for p in P.in_folder_inputs(exp)}
    assert names == {"a.xlsx", "01_a.csv"}


# --------------------------------------------------------------------------- #
# README review (artifact provenance with declared external inputs) — the layer
# the store's `review`/`fingerprint`/`audit` commands build on. Ported from the
# former archivist _experiment tests, rewritten against the shared core API.
# --------------------------------------------------------------------------- #
def _review_exp(tmp_path):
    """An experiment folder under a repo root (tmp_path). Returns (home, exp_dir)."""
    d = tmp_path / "K1-1 - Exp"
    (d / "data").mkdir(parents=True)
    (d / "raw").mkdir()
    (d / "data" / "kd.csv").write_text("a,b\n1,2\n")
    (d / "raw" / "plate.eds").write_bytes(b"\x00\x01raw")
    (d / "README.md").write_text("# prose\n")
    (d / "experiment.yml").write_text("exp_id: K1-1\n")
    return tmp_path, d


def test_in_folder_data_files_repo_relative_excludes_readme_and_sidecar(tmp_path):
    home, d = _review_exp(tmp_path)
    (d / "README.pdf").write_bytes(b"%PDF render")     # a render, must be excluded too
    paths = P.in_folder_data_files(home, d)
    assert paths == ["K1-1 - Exp/data/kd.csv", "K1-1 - Exp/raw/plate.eds"]  # repo-rel, sorted
    assert not any("README" in p or "experiment.yml" in p for p in paths)


def test_review_records_explicit_inputs_and_artifact_sha(tmp_path):
    home, d = _review_exp(tmp_path)
    sidecar, missing = P.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04")
    assert missing == []
    entry = sidecar["provenance"][0]
    assert entry["artifact"] == "README.md"
    assert entry["reviewed_at"] == "2026-06-04"
    assert {i["path"] for i in entry["inputs"]} == {"K1-1 - Exp/data/kd.csv", "K1-1 - Exp/raw/plate.eds"}
    assert all(len(i["sha256"]) == 64 for i in entry["inputs"])    # real hashes
    assert len(entry["artifact_sha256"]) == 64


def test_review_includes_and_preserves_external_inputs(tmp_path):
    home, d = _review_exp(tmp_path)
    ext = home / "Shared" / "deck.pptx"; ext.parent.mkdir(); ext.write_bytes(b"slides")
    sidecar, missing = P.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04",
                                extra_inputs=["Shared/deck.pptx"])
    assert "Shared/deck.pptx" in {i["path"] for i in sidecar["provenance"][0]["inputs"]}
    # a re-review (no extra_inputs given) preserves the external one
    sidecar2, _ = P.review(home, d, sidecar, today="2026-06-05")
    assert "Shared/deck.pptx" in {i["path"] for i in sidecar2["provenance"][0]["inputs"]}


def test_review_reports_missing_declared_input(tmp_path):
    home, d = _review_exp(tmp_path)
    _, missing = P.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04",
                          extra_inputs=["Shared/gone.pptx"])
    assert missing == ["Shared/gone.pptx"]


def test_resolve_inputs(tmp_path):
    home, d = _review_exp(tmp_path)
    inputs, missing = P.resolve_inputs(home, d, ["Shared/gone.pptx"])
    assert [i["path"] for i in inputs] == ["K1-1 - Exp/data/kd.csv", "K1-1 - Exp/raw/plate.eds"]
    assert missing == ["Shared/gone.pptx"]


def test_review_staleness_reports_reviewed_at(tmp_path):
    home, d = _review_exp(tmp_path)
    sidecar, _ = P.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04")
    P.write_sidecar(d, sidecar)
    assert P.staleness(d, repo_root=home)["state"] == "up-to-date"
    (d / "data" / "kd.csv").write_text("a,b\n9,9\n")    # an input changed
    st = P.staleness(d, repo_root=home)
    assert st["state"] == "stale"
    assert st["changed"] == ["K1-1 - Exp/data/kd.csv"]
    assert st["reviewed_at"] == "2026-06-04"            # surfaced for "last reviewed"


def test_review_staleness_artifact_edit(tmp_path):
    home, d = _review_exp(tmp_path)
    sidecar, _ = P.review(home, d, {"exp_id": "K1-1"}, today="2026-06-04")
    P.write_sidecar(d, sidecar)
    (d / "README.md").write_text("# edited prose\n")    # the artifact itself changed
    st = P.staleness(d, repo_root=home)
    assert st["state"] == "stale" and st["artifact_changed"] is True
