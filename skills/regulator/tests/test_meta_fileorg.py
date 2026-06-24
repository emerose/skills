from pathlib import Path

from regulator import meta, fileorg


def test_citekey_drugsfda():
    rec = {"doc_type": "drugsfda", "application_number": "NDA205834",
           "submission": "s000", "review_type": "medical"}
    assert meta.make_citekey(rec) == "NDA205834-s000-medical"


def test_citekey_guidance():
    rec = {"doc_type": "guidance",
           "title": "Rare Diseases: Natural History Studies for Drug Development",
           "issue_date": "03/24/2022"}
    ck = meta.make_citekey(rec)
    assert ck.startswith("guidance-2022-")
    assert "rare" in ck and "diseases" in ck


def test_citekey_adcomm():
    rec = {"doc_type": "adcomm", "committee_abbr": "ODAC",
           "meeting_date": "2024-07-25", "material_type": "briefing"}
    assert meta.make_citekey(rec) == "odac-2024-07-25-briefing"


def test_citekey_personnel():
    assert meta.make_citekey({"doc_type": "personnel", "name": "John J Jenkins"}) == "person-john-j-jenkins"


def test_fileorg_drugsfda_tree(tmp_path: Path):
    rec = {"doc_type": "drugsfda", "application_number": "NDA205834",
           "brand_name": "SOVALDI", "submission": "s000", "review_type": "medical",
           "citekey": "NDA205834-s000-medical"}
    dest = fileorg.plan_path(tmp_path, rec, ".pdf")
    assert dest.parent.name == "NDA205834 SOVALDI"
    assert dest.parent.parent.name == "drugsfda"
    assert dest.name.startswith("NDA205834 s000 medical")


def test_fileorg_guidance_tree(tmp_path: Path):
    rec = {"doc_type": "guidance", "title": "Expedited Programs", "fda_org": "CDER",
           "issue_date": "2014-05-01"}
    dest = fileorg.plan_path(tmp_path, rec, ".pdf")
    assert dest.parent.parts[-2:] == ("guidance", "CDER")
    assert "(2014)" in dest.name


def test_record_metadata_roundtrip_drops_empty():
    rec = {"doc_type": "guidance", "title": "X", "topic": None, "tags": [], "status": "Final"}
    md = meta.record_to_metadata(rec)
    assert md == {"doc_type": "guidance", "title": "X", "status": "Final"}
