"""Unit tests for _intake: placement plan (mechanical) + agent-supplied routes.

A document's *role* (protocol vs reports vs raw) is the agent's content judgment,
passed in via `routes`; this module only does the deterministic placement — keep an
already-organised subfolder, honour the agent's route, else a format/`raw` default.
"""

from scientist.store import _intake


def test_classify_incoming_fallback_is_deterministic():
    # format-determined binaries -> raw (mechanical, role fixed by format)
    assert _intake.classify_incoming("20260312_Sync_Lumbar.eds") == "raw"
    assert _intake.classify_incoming("baseline_D30.spk") == "raw"
    assert _intake.classify_incoming("V1234567 graphs.pzfx") == "raw"
    # documents are NOT classified by keyword any more — they fall back to the
    # conservative LAYOUT default (raw); the agent re-routes via plan_intake routes.
    assert _intake.classify_incoming("V1234567 Final Study Protocol.pdf") == "raw"
    assert _intake.classify_incoming("V1234567 Draft Report.docx") == "raw"
    assert _intake.classify_incoming("random_deck.pptx") == "raw"


def test_plan_intake_default_and_routes(tmp_path):
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
    # the agent's content judgment: route the two documents
    routes = {"Final Study Protocol.pdf": "protocol", "Draft Report.docx": "reports"}
    plan = _intake.plan_intake(sources, exp, routes=routes)
    by_name = {p["src"].name: p for p in plan}

    assert ".DS_Store" not in by_name
    assert by_name["Final Study Protocol.pdf"]["subdir"] == "protocol"
    assert by_name["Final Study Protocol.pdf"]["routed_by"] == "agent"
    assert by_name["Final Study Protocol.pdf"]["exists"] is True       # collision flagged
    assert by_name["Draft Report.docx"]["subdir"] == "reports"
    assert by_name["Draft Report.docx"]["dest"] == exp / "reports" / "Draft Report.docx"
    # the instrument binary is placed by format, no route needed
    assert by_name["instrument.eds"]["subdir"] == "raw"
    assert by_name["instrument.eds"]["routed_by"] == "ext"


def test_plan_intake_unrouted_document_defaults_and_is_flagged(tmp_path):
    src = tmp_path / "delivery"
    src.mkdir()
    (src / "Mystery.docx").write_text("?")
    exp = tmp_path / "K1-3 - Exp"
    exp.mkdir()
    plan = _intake.plan_intake([src / "Mystery.docx"], exp)   # no routes
    assert plan[0]["subdir"] == "raw"
    assert plan[0]["routed_by"] == "default"        # an unreviewed guess the dry-run marks


def test_plan_intake_preserves_existing_subdir_structure(tmp_path):
    # a source already organised as raw/Run 2/x.eds keeps that structure
    src = tmp_path / "delivery"
    (src / "raw" / "Run 2").mkdir(parents=True)
    (src / "raw" / "Run 2" / "x.eds").write_bytes(b"\x00")
    exp = tmp_path / "K1-2 - Exp"
    exp.mkdir()
    plan = _intake.plan_intake([src / "raw" / "Run 2" / "x.eds"], exp)
    assert plan[0]["subdir"] == "raw"
    assert plan[0]["routed_by"] == "path"
    assert plan[0]["dest"] == exp / "raw" / "Run 2" / "x.eds"
