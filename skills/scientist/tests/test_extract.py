"""Unit tests for _extract: controlled-vocabulary normalizers (stdlib only).

Fixtures here are synthetic/generic (placeholder vendor "Vendor A", synthetic ids)
— the public repo carries no program-specific names. Real vendor vocabularies are
loaded privately at runtime (see test_load_vocab_*).

Reading a README and deciding *which* metadata applies is the agent's job (see
references/search-index.md); these helpers only canonicalize the tokens it identifies
so cross-referencing stays consistent.
"""

import json

from scientist.store import _extract


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


def test_find_related_excludes_self():
    text = "Follow-up to K1-000001; see also K1-000002 and K1-000000 itself."
    assert _extract.find_related(text, exclude="K1-000000") == ["K1-000001", "K1-000002"]


def test_match_vocab_canonicalizes():
    # an alias / full vendor name folds onto its canonical vocabulary entry
    assert _extract.match_vocab("Vendor A Discovery Services (Somecity)",
                                _extract.CRO_VOCAB) == ["Vendor A"]
    assays = _extract.match_vocab("Ran RT-qPCR and a Luminex panel",
                                  _extract.ASSAY_VOCAB)
    assert set(assays) == {"qPCR", "Luminex"}
    assert _extract.match_vocab("nothing relevant", _extract.CRO_VOCAB) == []


def test_load_vocab_defaults_are_generic():
    cro, pats = _extract.load_vocab(home=None)
    assert "Vendor A" in cro
    # no real vendor names baked into the public defaults
    assert all("charles" not in k.lower() for k in cro)
    assert pats == _extract.DEFAULT_STUDY_ID_PATTERNS


def test_load_vocab_merges_private_config(tmp_path):
    # JSON keeps this test dependency-free; scientist also accepts vocab.yml
    (tmp_path / "vocab.json").write_text(json.dumps({
        "cros": {"Real CRO Inc.": ["real cro", r"\bRCI\b"]},
        "study_id_patterns": [r"\bRCI-\d{6}\b"],
    }))
    cro, pats = _extract.load_vocab(home=tmp_path)
    assert cro["Real CRO Inc."] == ["real cro", r"\bRCI\b"]
    assert "Vendor A" in cro                        # defaults preserved
    assert r"\bRCI-\d{6}\b" in pats
    # and the merged vocab actually drives canonicalization + id matching
    assert _extract.match_vocab("Run at Real CRO Inc.", cro) == ["Real CRO Inc."]
    assert _extract.find_study_ids("study RCI-012345", pats) == ["RCI-012345"]
