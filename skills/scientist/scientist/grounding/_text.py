"""scientist.grounding._text — re-exports the shared text helpers from the grounding package.

The sha256 hasher and the verbatim-quote matcher now come from the standalone ``grounding``
package (pip: pytest-grounding), so scientist and the library share one canonical fold +
matcher (the verdict-cache identity in :mod:`scientist.grounding.judgments` depends on it).
Underscore aliases preserve the names scientist's callers already import.
"""
from __future__ import annotations

from grounding import match_phrase as _match_phrase, sha256 as _sha256  # noqa: F401
from .normalize import collapse_ws as _collapse_ws, fold_match as _fold_match  # noqa: F401
