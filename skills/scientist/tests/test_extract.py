"""Unit tests for _extract: README metadata + entity extraction (stdlib only).

Fixtures here are synthetic/generic (placeholder vendor "Vendor A", synthetic ids)
— the public repo carries no program-specific names. Real vendor vocabularies are
loaded privately at runtime (see test_load_vocab_*).
"""

import json

from scientist.store import _extract

README = """# K1-000000: Rat IT Dose-Response Study (V1234567)

**Study Title:** An Intrathecal Study of ASO 7 in Sprague-Dawley Rats

## Study IDs & Dates

| Field | Value |
|-------|-------|
| **Internal ID** | K1-000000 |
| **External ID** | V1234567 |
| **CRO** | Vendor A Discovery Services (Somecity) |
| **Species/Strain** | Sprague-Dawley rats |
| **Report Status** | Draft Report v03 |

## Study Overview

Follow-up to V1234566 which identified ASO 7 as the lead candidate. This study
evaluated ASO 7 at three intrathecal dose levels.

## Main Findings

QuantiGene showed strongest GENE_X knockdown in spinal cord. Luminex cytokine panel
was largely reassuring. LC-MS/MS biodistribution confirmed dose-dependent exposure.

## Related Studies

- **K1-000001:** Rat IT Screening Study 1
- **K1-000002:** Single Dose IT Tox
"""


def test_extract_core_fields():
    out = _extract.extract_from_readme(README, exp_id="K1-000000")
    assert out["title"].startswith("An Intrathecal Study")
    assert out["cro"] == "Vendor A"                 # full name canonicalised to vocab
    # the README table id is authoritative for THIS experiment; a predecessor's
    # id mentioned only in prose (V1234566) must NOT pollute this experiment's ids
    assert out["cro_study_ids"] == ["V1234567"]
    assert "V1234566" not in out["cro_study_ids"]
    assert out["model"] == "Sprague-Dawley rats"
    assert out["asos"] == ["ASO-7"]
    assert set(["QuantiGene", "Luminex", "LC-MS/MS"]).issubset(set(out["assays"]))
    assert set(out["related"]) == {"K1-000001", "K1-000002"}   # self excluded
    assert "K1-000000" not in out["related"]
    assert "lead candidate" in out["synopsis"]


def test_find_asos_normalization():
    assert _extract.find_asos("ASO 12, ASO-5 and ASO003") == ["ASO-3", "ASO-5", "ASO-12"]
    assert _extract.find_asos("no asos here") == []


def test_find_study_ids_shapes():
    # generic, vendor-neutral default shapes
    text = "Studies V1234567, 1124-8851, SOW2."
    ids = _extract.find_study_ids(text)
    for want in ("V1234567", "1124-8851", "SOW2"):
        assert want in ids


def test_find_study_ids_custom_patterns():
    # a vendor-specific shape isn't recognised by the generic defaults...
    assert _extract.find_study_ids("Study 25P-ABC-001") == []
    # ...but is when supplied (as the private vocab file would supply it)
    pats = _extract.DEFAULT_STUDY_ID_PATTERNS + [r"\b25[PW]-[A-Z]{3}-\d{3}\b"]
    assert "25P-ABC-001" in _extract.find_study_ids("Study 25P-ABC-001", pats)


def test_load_vocab_defaults_are_generic():
    cro, pats = _extract.load_vocab(home=None)
    assert "Vendor A" in cro
    # no real vendor names baked into the public defaults
    assert all("charles" not in k.lower() for k in cro)
    assert pats == _extract.DEFAULT_STUDY_ID_PATTERNS


def test_load_vocab_merges_private_config(tmp_path):
    # JSON keeps this test dependency-free; arx also accepts vocab.yml
    (tmp_path / "vocab.json").write_text(json.dumps({
        "cros": {"Real CRO Inc.": ["real cro", r"\bRCI\b"]},
        "study_id_patterns": [r"\bRCI-\d{6}\b"],
    }))
    cro, pats = _extract.load_vocab(home=tmp_path)
    assert cro["Real CRO Inc."] == ["real cro", r"\bRCI\b"]
    assert "Vendor A" in cro                        # defaults preserved
    assert r"\bRCI-\d{6}\b" in pats
    # and the loaded vocab actually drives extraction
    readme = ("# K1-000000: Study\n\n| Field | Value |\n|--|--|\n"
              "| **CRO** | Real CRO Inc. |\n| **External ID** | RCI-012345 |\n")
    out = _extract.extract_from_readme(readme, home=tmp_path)
    assert out["cro"] == "Real CRO Inc."
    assert out["cro_study_ids"] == ["RCI-012345"]


def test_extract_with_injected_vocab():
    readme = "# K1-000000: Study\n\nRun at Acme Labs.\n"
    out = _extract.extract_from_readme(
        readme, cro_vocab={"Acme Labs": [r"acme labs"]}, study_id_patterns=[])
    assert out["cro"] == "Acme Labs"


def test_status_only_from_explicit_field_not_prose():
    # prose mentioning failure must NOT mark the study failed (precision)
    prose = ("# K1-000000: Study\n\nThe lumbar punctures failed to deliver test "
             "article, so CNS data is unreliable.\n")
    assert "status" not in _extract.extract_from_readme(prose)
    # an explicit Status field IS read and normalised
    tabular = ("# K1-000004: F-Dup ASO Validation\n\n| Field | Value |\n|--|--|\n"
               "| **Status** | Terminated early |\n")
    assert _extract.extract_from_readme(tabular)["status"] == "terminated"


def test_parse_md_table_fields():
    f = _extract.parse_md_table_fields(README)
    assert f["internal id"] == "K1-000000"
    assert f["external id"] == "V1234567"
    assert "field" not in f          # header row skipped


def test_extract_returns_only_confident_keys():
    out = _extract.extract_from_readme("# K1-000000: Empty\n\nNothing structured here.\n")
    # no CRO/assays/asos invented from an empty doc
    assert "cro" not in out
    assert out.get("assays", []) == []
    assert out.get("asos", []) == []
