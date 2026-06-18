"""scientist.grounding.normalize — the one verbatim-quote text normalizer.

A single, pure-stdlib place for the text normalization both *quote matching* and the
*verdict cache identity* depend on. Splitting it out is load-bearing: the support-verdict
cache keys evidence by ``sha256(_fold_match(span))`` (judgments.py), while quote matching
folds the same way (``PaperRef.contains`` in ``grounding/__init__``). If the two ever
diverged, two quotes the matcher treats as the SAME evidence (e.g. ``*Ube3a* gene
dosage…`` vs ``Ube3a gene dosage…``) would get DIFFERENT cache keys, and the cache's
one-canonical-verdict pruning would stale a perfectly good verdict cited from another
module. Sharing this module keeps fold == key, by construction.

This module is pure stdlib (no pandas, no libkit) so it is safe to import from
``judgments.py`` — which sits on the pytest path — without dragging in heavy deps or
creating an import cycle (``judgments`` and ``grounding/__init__`` both import *down*
into here; nothing here imports back up).
"""
from __future__ import annotations

import unicodedata

# Unicode dash/hyphen variants that publishers and PDF extractors use interchangeably with
# ASCII "-": en/em dashes, the Unicode hyphen, non-breaking hyphen, minus sign, etc. Folding
# them (plus NFKC, which normalizes ligatures/full-width/compatibility forms) makes a verbatim
# quote match a paper's stored text without the author having to reproduce the exact glyph —
# the single most common reason a real, correct quote fails a naive substring check.
_DASHES = "‐‑‒–—―⁃−﹘﹣－"
_DASH_MAP = {ord(c): "-" for c in _DASHES}


def collapse_ws(s: str) -> str:
    """Collapse every run of whitespace to a single space (and strip). External claims
    match *verbatim* phrases, but extractors split a sentence across runs/lines/cells
    (worst in pptx); normalizing both sides makes a short quote match reliably."""
    return " ".join(s.split())


def fold_match(s: str) -> str:
    """Normalize text for verbatim-quote matching: NFKC-normalize, fold Unicode dashes to
    ASCII ``-``, drop Markdown emphasis markers (``*``/``_`` — the library stores parsed
    Markdown, so a gene name reads ``*Xyz1*``; that's markup, not content), then collapse
    whitespace. Case is preserved (a quote is still verbatim).

    This is BOTH the quote matcher's normalizer and the verdict cache's identity normalizer
    (see module docstring): markdown / whitespace / dash variants of the same sentence fold
    to one form → one cache identity → one shared verdict."""
    folded = unicodedata.normalize("NFKC", s).translate(_DASH_MAP).replace("*", "").replace("_", "")
    return collapse_ws(folded)
