"""Unit tests for _audit: redundant archives, structural flags, staleness."""

import zipfile

import _audit


def _exp(tmp_path):
    exp = tmp_path / "K1-1 - Exp"
    (exp / "raw").mkdir(parents=True)
    (exp / "data").mkdir()
    return exp


def test_redundant_archive_detected(tmp_path):
    exp = _exp(tmp_path)
    # extracted copies present in-folder
    (exp / "raw" / "a.eds").write_bytes(b"\x00")
    (exp / "raw" / "b.xlsx").write_bytes(b"\x01")
    z = exp / "raw" / "delivery.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("raw/a.eds", "x")
        zf.writestr("raw/b.xlsx", "y")
        zf.writestr("__MACOSX/._a.eds", "junk")     # cruft ignored
    arcs = {a["zip"]: a for a in _audit.redundant_archives(exp)}
    assert arcs["raw/delivery.zip"]["redundant"] is True


def test_archive_with_unique_member_not_redundant(tmp_path):
    exp = _exp(tmp_path)
    (exp / "raw" / "a.eds").write_bytes(b"\x00")
    z = exp / "raw" / "delivery.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.eds", "x")
        zf.writestr("only_in_zip.eds", "z")          # not extracted in-folder
    arcs = {a["zip"]: a for a in _audit.redundant_archives(exp)}
    assert arcs["raw/delivery.zip"]["redundant"] is False


def test_structural_flags(tmp_path):
    exp = _exp(tmp_path)
    (exp / "data" / "kd.csv").write_text("a,b\n1,2\n")
    (exp / "stray.txt").write_text("loose at root")     # layout flag
    home = tmp_path
    rel = lambda p: str(p.resolve().relative_to(home.resolve()))
    file_records = [
        {"path": rel(exp / "data" / "kd.csv"), "role": "data", "sha256": "x"},
        {"path": rel(exp / "gone.csv"), "role": "data", "sha256": "y"},   # file-missing
    ]
    flags = _audit.structural_flags(home, exp, {"exp_id": "K1-1"}, file_records)
    assert "missing:readme" in flags
    assert any(f.startswith("file-missing:") for f in flags)
    assert "thin-metadata" in flags                      # no cro/assays/asos
    assert any(f.startswith("unindexed:") for f in flags)  # stray.txt + csv not all indexed
    assert any("layout:root-file:stray.txt" == f for f in flags)


def test_duplicate_path_not_flagged_unindexed(tmp_path):
    exp = _exp(tmp_path)
    (exp / "protocol").mkdir()
    (exp / "protocol" / "a.md").write_text("dup")
    (exp / "reports").mkdir()
    (exp / "reports" / "b.md").write_text("dup")        # both on disk
    home = tmp_path
    rel = lambda p: str(p.resolve().relative_to(home.resolve()))
    # one record with the second path tracked as a duplicate
    recs = [{"path": rel(exp / "protocol" / "a.md"), "role": "protocol", "cro": "X",
             "other_paths": [rel(exp / "reports" / "b.md")]}]
    flags = _audit.structural_flags(home, exp, {"exp_id": "K1-1", "cro": "X"}, recs)
    assert not any(f.startswith("unindexed") for f in flags)   # dup path counts as indexed


