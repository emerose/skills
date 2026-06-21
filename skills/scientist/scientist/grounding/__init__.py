"""scientist.grounding — claim-grounding + analysis-provenance for the scientist skill.

The capture core, tracked loaders, quote-matching, judgment markers and the pytest plugin now
come from the standalone **grounding** package (``pip install pytest-grounding``). This package
is a thin **façade** over it that adds scientist's own layers and re-exports the combined
surface, so existing ``from scientist.grounding import …`` call sites keep working unchanged:

    grounding package  →  capture (Capture/record/current_capture/registry), tracked loaders
                          (load/data/doc/DocRef), statement()/evidence()/uses(), the markers
                          (strength/caveats/kind/reviewed), the bypass guard, quote matching.
    scientist adds     →  literature grounding (paper/source/converge + the support-verdict
                          cache), the analysis-derivation recorder, and the experiment-aware
                          companion plugin (see plugin.py).

**Statements:** a claim records its proposition with ``statement(...)`` (from the grounding
package), ideally computed from the data so it can't drift. The plugin no longer reads the
docstring as the statement — call ``statement()`` (the docstring is reviewer notes).
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# The grounding core (pytest-grounding) — re-exported verbatim.
# --------------------------------------------------------------------------- #
from grounding import (  # noqa: F401
    Capture,
    DocRef,
    EmptyExtraction,
    UnsupportedDocFormat,
    TRACKED_SUFFIXES,
    current_capture,
    record,
    registry,
    load,
    data,
    doc,
    evidence,
    statement,
    uses,
    strength,
    caveats,
    kind,
    reviewed,
    install_guard,
    match_phrase,
    sha256,
    collapse_ws,
    fold_match,
)
# The active-capture ContextVar — scientist's derivation recorder pushes its own Capture onto
# it so `experiments` table reads are captured during a derivation (same mechanism the plugin
# uses for claims). Private in the library; reached here because scientist is its first-party
# companion, pinned to a known version.
from grounding._capture import _CURRENT  # noqa: F401

# --------------------------------------------------------------------------- #
# scientist's own layers — kept here, layered on the core above.
# --------------------------------------------------------------------------- #
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
    # core (from the grounding package)
    "load", "data", "doc", "DocRef", "UnsupportedDocFormat", "EmptyExtraction",
    "statement", "evidence", "uses", "cross", "record",
    "Capture", "current_capture", "registry", "TRACKED_SUFFIXES",
    "strength", "caveats", "kind", "reviewed",
    "install_guard", "match_phrase", "sha256", "collapse_ws", "fold_match",
    # literature
    "paper", "source", "converge", "metric", "cited_by", "PaperRef", "LiteratureError",
    "JudgmentCache", "JUDGMENT_CACHE_NAME", "set_judgment_cache", "current_judgment_cache",
    # derivation
    "derivation", "Derivation", "DerivationAudit", "audit_derivations", "current_audit",
]


# --------------------------------------------------------------------------- #
# Literature support-verdict cache — read on the pytest path (the companion plugin loads it),
# written by the refresh step (`sci judge`). source(paraphrase=…) consults it; NEVER calls a
# model (none exists in the tool). An absent file → empty cache → needs-judgment (non-blocking).
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
# dependency so the (now-removed) reconcile lint wouldn't flag it. The lint is gone with the
# move to the grounding plugin; this keeps existing ``other = cross(k1_xxxxxx)`` call sites
# working by returning the study unchanged.
# --------------------------------------------------------------------------- #
def cross(study):
    """Compatibility shim: return ``study`` unchanged. (The cross-experiment reconcile lint it
    once fed no longer exists; cross-experiment composition still works via plain imports +
    ``uses``.)"""
    return study
