"""scientist.grounding.normalize — re-exports the one verbatim-quote normalizer.

The canonical fold now lives in the standalone ``grounding`` package (pip: pytest-grounding);
this module re-exports it so scientist and the library share ONE fold by construction. The
verdict-cache identity in :mod:`scientist.grounding.judgments` keys evidence by
``sha256(fold_match(span))``, so it must fold exactly the way quote matching does — sharing the
library's function guarantees that.
"""
from __future__ import annotations

from grounding import collapse_ws, fold_match  # noqa: F401  (re-exported)

__all__ = ["collapse_ws", "fold_match"]
