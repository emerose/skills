from pathlib import Path

from regulator import importer, meta


def test_classify_accessdata_filename(tmp_path: Path):
    home = tmp_path
    f = home / "Other Programs" / "FDA-NDA-CNS-ASOs" / "03_Eteplirsen_Exondys51" / "206488Orig1s000MedR.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"%PDF-1.4 test")
    rec = importer.classify_path(f, home)
    assert rec["doc_type"] == "drugsfda"
    assert rec["application_number"] == "206488"
    assert rec["submission"] == "s000"
    assert rec["review_type"] == "medical"
    assert rec["active_ingredient"] == "Eteplirsen"
    assert rec["brand_name"] == "Exondys51"
    assert rec["program"] == "03_Eteplirsen_Exondys51"
    assert rec["file_path"].endswith("206488Orig1s000MedR.pdf")
    assert rec["imported"] is True
    # citekey is derived for a drugsfda record
    assert meta.make_citekey(rec) == "206488-s000-medical"


def test_classify_other_file(tmp_path: Path):
    home = tmp_path
    f = home / "Pre-IND Briefing Book" / "Kicho Pre-IND Briefing Document.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"%PDF-1.4 test")
    rec = importer.classify_path(f, home)
    assert rec["doc_type"] == "other"
    assert rec["title"] == "Kicho Pre-IND Briefing Document"
    assert rec["program"] == "Pre-IND Briefing Book"
    assert "imported" in rec["tags"]
    ck = meta.make_citekey(rec)
    assert ck.startswith("doc-")


def test_brand_and_drug():
    assert importer._brand_and_drug("03_Eteplirsen_Exondys51") == ("Eteplirsen", "Exondys51")
    assert importer._brand_and_drug("01_Nusinersen_Spinraza") == ("Nusinersen", "Spinraza")
    assert importer._brand_and_drug(None) == (None, None)


def test_walk_skips_dotfiles_and_store(tmp_path: Path):
    home = tmp_path
    (home / "Other Programs").mkdir(parents=True)
    (home / "Other Programs" / "a.pdf").write_bytes(b"x")
    (home / "b.md").write_text("hi")
    (home / "docs").mkdir()
    (home / "docs" / "fetched.pdf").write_bytes(b"x")        # skill-managed: skipped
    (home / ".download").mkdir()
    (home / ".download" / "tmp.pdf").write_bytes(b"x")        # store tmp: skipped
    (home / ".DS_Store").write_bytes(b"x")
    (home / "notes.xlsx").write_bytes(b"x")                   # not ingestible ext
    got = {p.name for p in importer.walk(home)}
    assert got == {"a.pdf", "b.md"}
    # include-docs widens to the managed tree
    got2 = {p.name for p in importer.walk(home, skip_dirs=())}
    assert "fetched.pdf" in got2
