"""DocRef text-extraction + quote-matching, across the three [reports] formats.

Each format gets a tiny fixture written on the fly (no real CRO deliverables in the
repo): docx via python-docx, pptx via python-pptx, pdf via matplotlib (a pinned dep —
its PDF backend embeds real, extractable text). The point is to lock the contract that
``doc().text()/.contains()`` works uniformly so external claims stop hand-rolling
per-format extraction — and that ``.pptx`` (TC decks) is now groundable at all.

Run: ``uv run --with-editable skills/scientist pytest skills/scientist/tests/test_docref.py -q``.
"""
from __future__ import annotations

import pytest

import analyst
from analyst import DocRef, UnsupportedDocFormat, doc

QUOTE = "no mortality was observed nor did any animal reach the humane endpoint"
NOTE = "speaker-note prose lives off-slide"


@pytest.fixture
def docx_report(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Objective")
    d.add_paragraph(f"The report states: {QUOTE}.")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Group A"
    table.rows[0].cells[1].text = "n=12"
    out = tmp_path / "report.docx"
    d.save(str(out))
    return out


@pytest.fixture
def pptx_deck(tmp_path):
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # title-only layout
    slide.shapes.title.text = "TC10 Summary"
    # The verbatim QUOTE is split *across* two paragraphs (the deck-flakiness case:
    # a sentence broken over lines/runs), so only whitespace-normalized matching bridges it.
    box = slide.shapes.add_textbox(0, 0, 100, 100).text_frame
    box.text = "The report states: no mortality was observed nor did"
    box.add_paragraph().text = "any animal reach the humane endpoint criteria."
    # Prose that only exists in the speaker notes.
    slide.notes_slide.notes_text_frame.text = NOTE
    out = tmp_path / "TC10 deck.pptx"
    prs.save(str(out))
    return out


@pytest.fixture
def pdf_report(tmp_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(8, 4))
    # Matplotlib's PDF backend embeds selectable text, which pdfplumber extracts.
    fig.text(0.05, 0.5, f"The report states: {QUOTE}.")
    out = tmp_path / "report.pdf"
    fig.savefig(str(out))  # type: ignore[attr-defined]
    plt.close(fig)
    return out


def test_docx_text_and_contains(docx_report):
    ref = doc(docx_report)
    assert isinstance(ref, DocRef)
    assert QUOTE in ref.text()
    assert ref.contains(QUOTE)
    assert ref.contains("Group A") and ref.contains("n=12")  # table cells
    assert not ref.is_presentation


def test_pptx_text_notes_and_split_quote(pptx_deck):
    ref = doc(pptx_deck)
    # Verbatim quote is split across paragraphs; normalize_ws (default) bridges it.
    assert ref.contains(QUOTE)
    assert not ref.contains(QUOTE, normalize_ws=False)
    assert NOTE in ref.text()          # speaker notes are pulled in
    assert ref.is_presentation         # the strength-cap signal for reviewers


def test_pdf_text_and_contains(pdf_report):
    ref = doc(pdf_report)
    assert ref.contains(QUOTE)
    assert not ref.is_presentation


def test_text_is_cached(pptx_deck):
    ref = doc(pptx_deck)
    first = ref.text()
    assert ref.text() is first         # second call returns the cached string


def test_unsupported_suffix_raises(tmp_path):
    weird = tmp_path / "legacy.ppt"
    weird.write_bytes(b"not really a deck")
    ref = doc(weird)
    with pytest.raises(UnsupportedDocFormat) as exc:
        ref.text()
    assert ".ppt" in str(exc.value) or "legacy.ppt" in str(exc.value)


def test_doc_records_provenance(pdf_report):
    """doc() sha-pins the cited bytes into the active capture, like any tracked read."""
    cap = analyst.Capture(claim_id="t")
    token = analyst._CURRENT.set(cap)
    try:
        ref = doc(pdf_report)
    finally:
        analyst._CURRENT.reset(token)
    assert any(inp["path"] == str(pdf_report) and inp["sha256"] == ref.sha256
               for inp in cap.inputs)
