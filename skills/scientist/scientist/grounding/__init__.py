"""scientist.grounding — scientist's grounding-layer additions, on top of the pytest-grounding package.

The claim-grounding **core** lives in the standalone ``grounding`` package
(``pip install pytest-grounding``) — import it **directly**, there is no façade here::

    from grounding import statement, evidence, uses, load, data, doc, DocRef, \
                          strength, caveats, kind, reviewed, Capture, record, current_capture

This package holds only scientist's own layers and exposes them:

  * **literature** grounding — ``paper()``/``source()``/``converge()`` + the support-verdict cache,
  * the analysis-**derivation** recorder,
  * (``plugin.py``) the experiment-aware companion pytest plugin (the grounding package's plugin
    is the single capture + report engine; this one only adds the ``experiment`` fixture, the
    judgment cache, and a couple of guard tweaks).
"""
from __future__ import annotations

from .judgments import JudgmentCache, JUDGMENT_CACHE_NAME  # noqa: F401
from .literature import (  # noqa: F401
    LiteratureError, PaperRef, paper, source, converge, metric, cited_by,
    _load_paper, _credibility_from_rec, _import_bibliostore, _load_dotenv_for,
    _bib_home, _record_source, _record_metric, _PAPER_CACHE, _BIBLIOSTORE,
)
from .derivation import (  # noqa: F401
    Derivation, derivation, DerivationAudit, audit_derivations, current_audit,
)

__all__ = [
    # literature
    "paper", "source", "converge", "metric", "cited_by", "PaperRef", "LiteratureError",
    "JudgmentCache", "JUDGMENT_CACHE_NAME", "set_judgment_cache", "current_judgment_cache",
    # derivation
    "derivation", "Derivation", "DerivationAudit", "audit_derivations", "current_audit",
    # compatibility shim
    "cross",
]


# --------------------------------------------------------------------------- #
# Literature support-verdict cache — read on the pytest path (the companion plugin loads it),
# written by the refresh step (`sci judge`). source(paraphrase=…) consults it; NEVER calls a
# model. An absent file → empty cache → needs-judgment (non-blocking).
# --------------------------------------------------------------------------- #
_JUDGMENT_CACHE: "JudgmentCache | None" = None


def set_judgment_cache(cache: "JudgmentCache | None") -> None:
    """Install the literature support-verdict cache for the session (called by the plugin)."""
    global _JUDGMENT_CACHE
    _JUDGMENT_CACHE = cache


def current_judgment_cache() -> "JudgmentCache | None":
    return _JUDGMENT_CACHE


# --------------------------------------------------------------------------- #
# cross() — compatibility passthrough. It previously declared an intentional cross-experiment
# dependency for the (now-removed) reconcile lint; it now returns the study unchanged so older
# ``other = cross(k1_xxxxxx)`` call sites keep working. Cross-experiment composition still works
# via plain imports + ``uses`` (from the grounding package).
# --------------------------------------------------------------------------- #
def cross(study):
    """Compatibility shim: return ``study`` unchanged."""
    return study
