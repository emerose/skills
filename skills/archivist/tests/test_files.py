"""Unit tests for _files: walking + classification + tabular schema (stdlib only)."""

import _files


def _make_experiment(root):
    (root / "README.md").write_text("# exp\n")
    (root / "raw").mkdir()
    (root / "raw" / "instrument.eds").write_bytes(b"\x00\x01binary")
    (root / "data").mkdir()
    (root / "data" / "weights.csv").write_text(
        "animal_id,weight,sex\n1,310,M\n2,295,F\n3,330,M\n4,288,F\n5,341,M\n6,300,F\n")
    (root / "reports").mkdir()
    (root / "reports" / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    # cruft that must be ignored
    (root / ".DS_Store").write_bytes(b"junk")
    (root / "~$report.docx").write_bytes(b"lockfile")     # Office temp/lock file
    (root / "._hidden.csv").write_bytes(b"appledouble")   # macOS AppleDouble
    venv = root / "analysis" / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "thing.py").write_text("x = 1\n")


def test_iter_experiment_files_classifies_and_ignores(tmp_path):
    _make_experiment(tmp_path)
    files = {f["filename"]: f for f in _files.iter_experiment_files(tmp_path)}
    assert set(files) == {"README.md", "instrument.eds", "weights.csv", "report.pdf"}
    assert files["README.md"]["role"] == "readme"
    assert files["README.md"]["classification"] == "narrative"
    assert files["instrument.eds"]["role"] == "raw"
    assert files["instrument.eds"]["classification"] == "binary"
    assert files["weights.csv"]["role"] == "data"
    assert files["weights.csv"]["classification"] == "tabular"
    assert files["report.pdf"]["role"] == "report"
    # nothing from inside the .venv leaked in
    assert "thing.py" not in files


def test_csv_schema_and_preview(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("animal_id,weight,sex\n1,310,M\n2,295,F\n3,330,M\n4,288,F\n5,341,M\n6,300,F\n")
    schema, preview = _files.schema_and_preview(p)
    assert [c["name"] for c in schema["columns"]] == ["animal_id", "weight", "sex"]
    assert schema["n_rows"] == 6
    assert preview.splitlines()[0] == "animal_id,weight,sex"
    assert len(preview.splitlines()) == 6      # header + 5 sample rows


def test_schema_for_unparseable_is_graceful(tmp_path):
    p = tmp_path / "weird.pzfx"
    p.write_bytes(b"PK\x03\x04 not really")
    assert _files.schema_and_preview(p) == (None, None)


def test_sha256_file_stable(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    import hashlib
    assert _files.sha256_file(p) == hashlib.sha256(b"hello").hexdigest()
