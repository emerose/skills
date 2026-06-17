"""Extraction package: the per-experiment recipe engine (``Extraction`` / ``x``),
the ``extract`` runner, and the ``audit`` / ``cellcov`` checks.

Reads via :mod:`labfiles`, records ``data/`` provenance edges via :mod:`provenance`.
The ``x`` public method API (``x.sheet`` / ``x.crc_long`` / ``x.table`` / ``x.xlsx`` /
``x.pzfx`` / ``x.docx_tables`` / ``x.pdf_pages``) is the contract per-experiment
``data/extract.py`` recipes depend on.
"""

from .audit import audit
from .cellcov import cellcov
from .engine import Extraction, extract, load_build, run_build

__all__ = ["Extraction", "extract", "audit", "cellcov", "load_build", "run_build"]
