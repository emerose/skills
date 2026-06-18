"""scientist.grounding._text — shared text / identifier helpers (leaf module).

The small, dependency-light helpers that several grounding submodules need: the sha256
hasher, the two verbatim-quote normalizers (re-exported from :mod:`normalize` so there is
still ONE canonical fold — see that module's docstring on why fold == cache key), the
single phrase matcher both ``DocRef.contains`` and ``PaperRef.contains`` delegate to, and
the identifier-column preservation used by :func:`load`.

This is a *leaf*: it imports only ``normalize`` (itself pure-stdlib) and, lazily, pandas
inside :func:`_preserve_identifier`. Nothing here imports back up into the grounding
package, so the other submodules (``literature``, ``derivation``, the package
``__init__``) can import from here without any risk of a cycle.
"""
from __future__ import annotations

import hashlib
import re as _re

# The one verbatim-quote normalizers, shared with judgments.py via normalize.py so the
# verdict-cache identity (sha of the folded span) matches what quote-matching considers the
# same evidence. See normalize.py for why these MUST be the same function everywhere.
from .normalize import collapse_ws as _collapse_ws, fold_match as _fold_match


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _match_phrase(phrase: str, text: str, *, normalize_ws: bool = True) -> bool:
    """Substring-check ``phrase`` against ``text`` for verbatim-quote matching. With
    ``normalize_ws`` (default) fold both sides first (NFKC + Unicode-dash fold + Markdown
    emphasis strip + whitespace-collapse, via :func:`fold_match`) so a correct quote isn't
    defeated by an en-dash, a ligature, stored Markdown, or an extractor that split it
    across runs/lines/cells; case is preserved. The single matcher both
    :meth:`DocRef.contains` and :meth:`PaperRef.contains` delegate to."""
    if normalize_ws:
        return _fold_match(phrase) in _fold_match(text)
    return phrase in text


_INT_LIKE = _re.compile(r"^-?\d+$")


def _preserve_identifier(col, str_col):
    """Keep a column as faithful strings when pandas' numeric inference would corrupt
    identifiers. Fires only when every non-blank value is a plain integer string AND
    inference would alter it — i.e. a leading zero is present (``"01"`` -> ``1``) or the
    column was floated by blank cells (``"73"`` -> ``73.0``, NaN for the blanks). Real
    measurement columns (any decimal point, sign-less floats, clean blank-free integers
    like counts/indices) are left numeric and untouched."""
    import pandas as pd

    if not (pd.api.types.is_integer_dtype(col.dtype) or pd.api.types.is_float_dtype(col.dtype)):
        return col  # already object/string
    nonblank = str_col[str_col != ""]
    if not len(nonblank) or not nonblank.map(lambda v: bool(_INT_LIKE.match(v))).all():
        return col  # has decimals / non-integer text -> a real measurement column
    has_leading_zero = nonblank.map(lambda v: len(v) > 1 and v.lstrip("-").startswith("0")).any()
    has_blanks = (str_col == "").any()
    if has_leading_zero or has_blanks:
        return str_col  # identifier-like; keep the exact text
    return col          # clean blank-free integers (counts, indices) stay numeric
