"""Unit tests for _audit: redundant archives, structural flags, staleness."""

import zipfile

import _audit
import _meta


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


def test_staleness_via_deps_block(tmp_path):
    home = tmp_path
    (home / "K1-1" ).mkdir()
    src = home / "K1-1" / "kd.csv"
    src.write_text("a,b\n1,2\n")
    import _files
    sha = _files.sha256_file(src)
    text_ok = _meta.set_deps_block("# README\n", [{"path": "K1-1/kd.csv", "sha256": sha}])
    assert _audit.staleness(text_ok, home) == {"missing": [], "changed": []}
    # change the file -> stale
    src.write_text("a,b\n9,9\n")
    assert _audit.staleness(text_ok, home)["changed"] == ["K1-1/kd.csv"]
    # missing file -> stale
    text_missing = _meta.set_deps_block("# README\n", [{"path": "K1-1/gone.csv", "sha256": "z"}])
    assert _audit.staleness(text_missing, home)["missing"] == ["K1-1/gone.csv"]
    # no deps block -> None (can't judge this way)
    assert _audit.staleness("# README\n\nno deps\n", home) is None
