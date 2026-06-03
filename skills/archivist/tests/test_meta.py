"""Unit tests for the pure model helpers in _meta (no libkit, no network)."""

import _meta


def test_parse_experiment_dirname_basic():
    p = _meta.parse_experiment_dirname("K1-230901 - Rat IT Dose-Response (C0790222)")
    assert p == {"exp_id": "K1-230901",
                 "name": "Rat IT Dose-Response (C0790222)",
                 "cro_study_id_guess": "C0790222"}


def test_parse_experiment_dirname_no_cro():
    p = _meta.parse_experiment_dirname("K1-210301 - ASO Design")
    assert p["exp_id"] == "K1-210301"
    assert p["name"] == "ASO Design"
    assert p["cro_study_id_guess"] is None


def test_parse_experiment_dirname_draft_and_separators():
    assert _meta.parse_experiment_dirname("K1-DRAFT01 - NHP IT PK-Tox")["exp_id"] == "K1-DRAFT01"
    # an em-dash separator still parses
    assert _meta.parse_experiment_dirname("K1-220804 — Immunotox Round 3")["exp_id"] == "K1-220804"
    # a parenthetical that's prose, not a code, is not mistaken for a study id
    p = _meta.parse_experiment_dirname("K1-220901 - F-Dup ASO Validation (Terminated)")
    assert p["cro_study_id_guess"] is None


def test_parse_experiment_dirname_rejects_non_experiment():
    assert _meta.parse_experiment_dirname("Shared") is None
    assert _meta.parse_experiment_dirname("Attic") is None


def test_classify_ext():
    assert _meta.classify_ext(".PDF") == "narrative"
    assert _meta.classify_ext(".docx") == "narrative"
    assert _meta.classify_ext(".csv") == "tabular"
    assert _meta.classify_ext(".xlsx") == "tabular"
    assert _meta.classify_ext(".pzfx") == "tabular"
    assert _meta.classify_ext(".eds") == "binary"
    assert _meta.classify_ext(".cram") == "binary"
    assert _meta.classify_ext(".png") == "binary"


def test_role_for_path_parts():
    assert _meta.role_for_path_parts((), "README.md") == "readme"
    assert _meta.role_for_path_parts(("raw",), "x.csv") == "raw"
    assert _meta.role_for_path_parts(("raw", "MEA data"), "y.spk") == "raw"
    assert _meta.role_for_path_parts(("reports",), "deck.pptx") == "report"
    assert _meta.role_for_path_parts(("analysis", "Run 1"), "a.ipynb") == "analysis"
    assert _meta.role_for_path_parts(("data",), "clean.csv") == "data"
    assert _meta.role_for_path_parts(("protocol",), "sow.pdf") == "protocol"
    assert _meta.role_for_path_parts(("mystery",), "z.txt") == "other"


def test_record_to_metadata_drops_empty_and_runtime():
    rec = {"exp_id": "K1-1", "name": "x", "tags": [], "cro": None, "note": "",
           "_page_count": 3, "document_id": "abc", "asos": ["ASO-154"]}
    meta = _meta.record_to_metadata(rec)
    assert meta == {"exp_id": "K1-1", "name": "x", "asos": ["ASO-154"]}


def test_experiment_card_deterministic_and_complete():
    rec = {"exp_id": "K1-230901", "title": "Rat IT Dose-Response",
           "cro": "Charles River", "cro_study_ids": ["C0790222"],
           "status": "complete", "assays": ["Luminex", "QuantiGene"],
           "asos": ["ASO-154"], "related": ["K1-241101", "K1-230402"],
           "synopsis": "Three IT dose levels in rats."}
    a = _meta.experiment_card_markdown(rec)
    b = _meta.experiment_card_markdown(dict(rec))
    assert a == b                       # deterministic
    assert "K1-230901" in a
    assert "C0790222" in a
    assert "ASO-154" in a
    assert "Charles River" in a
    # related list is sorted regardless of input order
    assert a.index("K1-230402") < a.index("K1-241101")


def test_file_card_with_schema_and_preview():
    rec = {"exp_id": "K1-1", "role": "data", "file_type": "csv",
           "path": "K1-1/data/x.csv", "filename": "x.csv", "size": 2048}
    schema = {"columns": [{"name": "animal_id"}, {"name": "weight", "unit": "g"}],
              "n_rows": 24}
    card = _meta.file_card_markdown(rec, schema=schema, preview="animal_id,weight\n1,310")
    assert "x.csv" in card
    assert "`animal_id`" in card
    assert "[g]" in card
    assert "24 rows" in card
    assert "animal_id,weight" in card


def test_catalog_markdown_escapes_pipes():
    # a stray pipe in any field must not corrupt the table (the K1-211101 bug)
    exps = [{"exp_id": "K1-1", "name": "A | B", "cro": "X", "status": "complete",
             "cro_study_ids": ["Title | Relationship"], "assays": ["qPCR"], "asos": ["ASO-1"]}]
    import re
    md = _meta.catalog_markdown(exps)
    row = [ln for ln in md.splitlines() if ln.startswith("| K1-1 ")][0]
    delimiters = re.findall(r"(?<!\\)\|", row)       # pipes NOT escaped = column delimiters
    assert len(delimiters) == 8                      # 7 columns -> exactly 8 delimiters
    assert "A \\| B" in row and "Title \\| Relationship" in row   # data pipes escaped
