"""Unit tests for _intake: incoming-file classification + placement plan."""

import _intake


def test_classify_incoming_keywords_win():
    assert _intake.classify_incoming("V1234567 Final Study Protocol v07 SIGNED.pdf") == "protocol"
    assert _intake.classify_incoming("VendorA_Sync SoW2.docx") == "protocol"
    assert _intake.classify_incoming("V1234567 Draft Report_03.docx") == "reports"
    assert _intake.classify_incoming("V1234567 Draft Results_8Dec2023.pptx") == "reports"
    assert _intake.classify_incoming("01August2022_Sync_TC-05_Final.pptx") == "reports"
    assert _intake.classify_incoming("Histopathology Report.pdf") == "reports"


def test_classify_incoming_by_extension():
    assert _intake.classify_incoming("20260312_Sync_Lumbar.eds") == "raw"
    assert _intake.classify_incoming("baseline_D30.spk") == "raw"
    assert _intake.classify_incoming("V1234567 graphs.pzfx") == "raw"
    # an unmarked CRO data file defaults to raw (original measurements)
    assert _intake.classify_incoming("V1234567 BW results.xlsx") == "raw"
    assert _intake.classify_incoming("random_deck.pptx") == "reports"


def test_plan_intake_routes_and_collisions(tmp_path):
    src = tmp_path / "delivery"
    (src / "sub").mkdir(parents=True)
    (src / "Final Study Protocol.pdf").write_text("p")
    (src / "Draft Report.docx").write_text("r")
    (src / "sub" / "instrument.eds").write_bytes(b"\x00")
    (src / ".DS_Store").write_bytes(b"junk")           # cruft, skipped
    exp = tmp_path / "K1-1 - Exp"
    (exp / "protocol").mkdir(parents=True)
    (exp / "protocol" / "Final Study Protocol.pdf").write_text("old")  # collision

    sources = sorted(p for p in src.rglob("*") if p.is_file())
    plan = _intake.plan_intake(sources, exp)
    routes = {p["src"].name: (p["subdir"], p["exists"]) for p in plan}
    assert ".DS_Store" not in routes
    assert routes["Final Study Protocol.pdf"] == ("protocol", True)   # collision flagged
    assert routes["Draft Report.docx"][0] == "reports"
    assert routes["instrument.eds"][0] == "raw"
    # dest path is under the routed subdir
    rep = next(p for p in plan if p["src"].name == "Draft Report.docx")
    assert rep["dest"] == exp / "reports" / "Draft Report.docx"


def test_plan_intake_preserves_existing_subdir_structure(tmp_path):
    # a source already organised as raw/Run 2/x.eds keeps that structure
    src = tmp_path / "delivery"
    (src / "raw" / "Run 2").mkdir(parents=True)
    (src / "raw" / "Run 2" / "x.eds").write_bytes(b"\x00")
    exp = tmp_path / "K1-2 - Exp"
    exp.mkdir()
    plan = _intake.plan_intake([src / "raw" / "Run 2" / "x.eds"], exp)
    assert plan[0]["subdir"] == "raw"
    assert plan[0]["dest"] == exp / "raw" / "Run 2" / "x.eds"
