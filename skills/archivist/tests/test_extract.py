"""Unit tests for _extract: README metadata + entity extraction (stdlib only)."""

import _extract

README = """# K1-230901: Rat IT Dose-Response Study (C0790222)

**Study Title:** An Intrathecal Study of ASO 154 in Sprague-Dawley Rats

## Study IDs & Dates

| Field | Value |
|-------|-------|
| **Internal ID** | K1-230901 |
| **External ID** | C0790222 |
| **CRO** | Charles River Discovery Services Finland (Kuopio) |
| **Species/Strain** | Sprague-Dawley rats |
| **Report Status** | Draft Report v03 |

## Study Overview

Follow-up to C0790122 which identified ASO 154 as the lead candidate. This study
evaluated ASO 154 at three intrathecal dose levels.

## Main Findings

QuantiGene showed strongest Ube3a knockdown in spinal cord. Luminex cytokine panel
was largely reassuring. LC-MS/MS biodistribution confirmed dose-dependent exposure.

## Related Studies

- **K1-230402:** Rat IT Screening Study 1
- **K1-241101:** Single Dose IT Tox
"""


def test_extract_core_fields():
    out = _extract.extract_from_readme(README, exp_id="K1-230901")
    assert out["title"].startswith("An Intrathecal Study")
    assert out["cro"] == "Charles River"            # full name canonicalised to vocab
    # the README table id is authoritative for THIS experiment; a predecessor's
    # id mentioned only in prose (C0790122) must NOT pollute this experiment's ids
    assert out["cro_study_ids"] == ["C0790222"]
    assert "C0790122" not in out["cro_study_ids"]
    assert out["model"] == "Sprague-Dawley rats"
    assert out["asos"] == ["ASO-154"]
    assert set(["QuantiGene", "Luminex", "LC-MS/MS"]).issubset(set(out["assays"]))
    assert set(out["related"]) == {"K1-230402", "K1-241101"}   # self excluded
    assert "K1-230901" not in out["related"]
    assert "lead candidate" in out["synopsis"]


def test_find_asos_normalization():
    assert _extract.find_asos("ASO 154, ASO-22 and ASO007") == ["ASO-7", "ASO-22", "ASO-154"]
    assert _extract.find_asos("no asos here") == []


def test_find_study_ids_shapes():
    text = "Studies C0790222, 1124-8851, 25P-KSO-001, Key 2738, SOW2, CRP Exp05."
    ids = _extract.find_study_ids(text)
    for want in ("C0790222", "1124-8851", "25P-KSO-001", "SOW2"):
        assert want in ids
    assert any("Key 273" in i for i in ids)


def test_status_only_from_explicit_field_not_prose():
    # prose mentioning failure must NOT mark the study failed (precision)
    prose = ("# K1-230901: Study\n\nThe lumbar punctures failed to deliver test "
             "article, so CNS data is unreliable.\n")
    assert "status" not in _extract.extract_from_readme(prose)
    # an explicit Status field IS read and normalised
    tabular = ("# K1-220901: F-Dup ASO Validation\n\n| Field | Value |\n|--|--|\n"
               "| **Status** | Terminated early |\n")
    assert _extract.extract_from_readme(tabular)["status"] == "terminated"


def test_parse_md_table_fields():
    f = _extract.parse_md_table_fields(README)
    assert f["internal id"] == "K1-230901"
    assert f["external id"] == "C0790222"
    assert "field" not in f          # header row skipped


def test_extract_returns_only_confident_keys():
    out = _extract.extract_from_readme("# K1-000000: Empty\n\nNothing structured here.\n")
    # no CRO/assays/asos invented from an empty doc
    assert "cro" not in out
    assert out.get("assays", []) == []
    assert out.get("asos", []) == []
