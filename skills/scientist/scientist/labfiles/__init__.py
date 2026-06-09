"""Deterministic readers for CRO/lab source files → tidy rows.

Pure functions of the source bytes (re-running on the same input yields identical
output, so extractions are diffable). Formats: ``.xlsx``/``.xls`` (spreadsheets),
GraphPad Prism (``.pzfx`` XML / ``.prism`` zip, content-sniffed; legacy binary
``PCFFGRA4`` raises a clear re-export error), Word ``.docx`` table extraction, and
PDF per-page text lines.

``docx_tables`` / ``pdf_pages`` extract TABLES/grid text (not prose), so they belong
here next to the spreadsheet/Prism readers — they are the raw measurement source when
a CRO ships data only as a Word or PDF report.

Public surface (names preserved from the extractor's ``_readers``):
``read_xlsx_sheet``, ``read_pzfx``, ``read_pzfx_structured``, ``read_prism``,
``read_prism_structured``, ``read_docx_tables``, ``read_pdf_pages``.
"""

from ._readers import (  # noqa: F401
    read_docx_tables,
    read_docx_text,
    read_pdf_pages,
    read_pdf_text,
    read_pptx_text,
    read_prism,
    read_prism_structured,
    read_pzfx,
    read_pzfx_structured,
    read_xlsx_sheet,
    _prism_sniff,
    _PRISM_BINARY_MSG,
)

__all__ = [
    "read_xlsx_sheet",
    "read_pzfx",
    "read_pzfx_structured",
    "read_prism",
    "read_prism_structured",
    "read_docx_tables",
    "read_pdf_pages",
    "read_pdf_text",
    "read_docx_text",
    "read_pptx_text",
]
