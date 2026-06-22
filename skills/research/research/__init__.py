"""research — the literature layer: reviews, paper-claims, and bibliometric claims.

A user-facing skill that owns everything *literature* in the scientific-data pipeline, split
out of ``scientist`` (which now manages only experiments). It holds:

  * **literature grounding** — ``paper()`` / ``source()`` / ``converge()`` / ``metric()`` /
    ``cited_by()`` (verify a verbatim quote against a paper in the bibliographer library, or read
    a stored OpenAlex metric), plus the support-verdict cache (``res judge``);
  * the ``[lit:]`` / ``[litreview:]`` **citation layer** (:mod:`research.literature_cites`) that
    plugs into the generic report engine via the citation-resolver registry;
  * **literature reviews** (PROSPERO/PRISMA surveys, the review-node tree) and **paper-claims**
    (a paper's pre-extracted attributed claim set).

research is independent of scientist: neither imports the other. They compose only through the
shared in-repo report engine (:mod:`reportkit`), whose citation registry research's
``literature_cites`` registers ``[lit:]`` / ``[litreview:]`` on. An experiment report
(``sci report``) that cites ``[lit:]`` works when research is installed (scientist optionally
imports research's resolvers at audit time); when it isn't, the engine warns rather than
silently dropping the citation.

research depends on three things, none of them scientist:

  * **pytest-grounding** (PyPI ``grounding`` package) — the claim-grounding core;
  * **reportkit** (the in-repo report engine) — reached via a ``sys.path`` walk, exactly as
    scientist reaches it (:func:`_bootstrap_reportkit`), since there is no workspace ``pyproject``;
  * **bibliographer** (the paper library) — reached via a ``sys.path`` walk in
    :mod:`research.literature` (``BIBLIOGRAPHER_HOME``), read-only/keyless.
"""
from __future__ import annotations


def _bootstrap_reportkit() -> None:
    """Put the in-repo ``reportkit`` package on ``sys.path`` if it is not already importable.

    Mirrors ``scientist._bootstrap_reportkit``: walks up from this package to the sibling
    ``skills/reportkit`` and inserts it on ``sys.path`` so ``import reportkit`` resolves whether
    ``research`` is ``pip install -e``'d or run via ``uv run``. A standalone ``pip install
    reportkit`` (its own test env) resolves normally and this is a no-op."""
    import importlib.util

    if importlib.util.find_spec("reportkit") is not None:
        return
    import sys
    from pathlib import Path

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "reportkit" / "reportkit" / "__init__.py").is_file():
            p = str(parent / "reportkit")
            if p not in sys.path:
                sys.path.insert(0, p)
            return


_bootstrap_reportkit()

from .judgments import JudgmentCache, JUDGMENT_CACHE_NAME  # noqa: E402,F401
from .literature import (  # noqa: E402,F401
    LiteratureError, PaperRef, paper, source, converge, metric, cited_by,
    _load_paper, _credibility_from_rec, _import_bibliostore, _load_dotenv_for,
    _bib_home, _record_source, _record_metric, _PAPER_CACHE, _BIBLIOSTORE,
)

__all__ = [
    # literature grounding
    "paper", "source", "converge", "metric", "cited_by", "PaperRef", "LiteratureError",
    # support-verdict cache
    "JudgmentCache", "JUDGMENT_CACHE_NAME", "set_judgment_cache", "current_judgment_cache",
]


# --------------------------------------------------------------------------- #
# Literature support-verdict cache — read on the pytest path (the companion plugin loads it),
# written by the refresh step (`res judge`). source(paraphrase=…) consults it; NEVER calls a
# model. An absent file → empty cache → needs-judgment (non-blocking). Moved verbatim from
# scientist.grounding when the literature layer split out.
# --------------------------------------------------------------------------- #
_JUDGMENT_CACHE: "JudgmentCache | None" = None


def set_judgment_cache(cache: "JudgmentCache | None") -> None:
    """Install the literature support-verdict cache for the session (called by the plugin)."""
    global _JUDGMENT_CACHE
    _JUDGMENT_CACHE = cache


def current_judgment_cache() -> "JudgmentCache | None":
    return _JUDGMENT_CACHE
