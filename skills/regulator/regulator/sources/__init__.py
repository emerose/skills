"""Per-source ingesters for regulator.

Each module wraps one FDA data source: how to discover/enumerate its documents,
how to download them, and the pure parsers that turn a source response into
regulator records. They depend only on stdlib + httpx (not libkit), so the
parsers unit-test offline.

* :mod:`regulator.sources.drugsfda`   — openFDA metadata + accessdata PDFs (GREEN)
* :mod:`regulator.sources.guidance`   — the FDA guidance-corpus JSON feed (YELLOW)
* :mod:`regulator.sources.adcomm`     — advisory-committee material pages (YELLOW)
* :mod:`regulator.sources.personnel`  — signature-block dossiers (RED, semi-auto)
"""
