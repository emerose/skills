"""research.plugin — the research COMPANION to the grounding plugin (the literature judge cache).

Three pytest plugins are active when the data repo runs its claims suite (which includes
literature claims using ``source()`` / ``paper()``):

  * the standalone **grounding** plugin (pip: pytest-grounding) — the single capture + report
    engine; it owns ``--grounding-out`` / ``--grounding-fresh``, the per-claim capture, the bypass
    guard, the judgment markers, and ``grounding_report.{json,md}``;
  * **scientist's** companion — the ``experiment`` fixture, the ``.pzfx`` tracked-suffix tweak, and
    the ``GROUNDING_ROOT ← SCIENTIST_HOME`` bridge;
  * **this** (research's) companion — loads the literature support-verdict cache so
    ``source(paraphrase=…)`` can read it, plus the ``--judge-cache`` option.

Like scientist's companion, this deliberately does **not** redefine ``--grounding-out`` (the
grounding plugin owns it); it only *reads* the option to locate the cache sidecar. The cache is
written only by ``res judge --record``; the pytest path here is read-only (no model ever runs).

Auto-loaded via the ``pytest11`` entry point (``research = "research.plugin"``) alongside the
other two.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import JUDGMENT_CACHE_NAME, JudgmentCache, set_judgment_cache


def pytest_addoption(parser):
    # Reuse the library plugin's "grounding" option group; add ONLY research's own option.
    g = parser.getgroup("grounding")
    g.addoption("--judge-cache", action="store", default=None,
                help="literature support-verdict cache (default: <grounding-out>/"
                     "lit_judgments.json). READ here; written only by `res judge`. The model "
                     "is never invoked on this path.")


def pytest_configure(config):
    # Load the literature support-verdict cache (read-only on this path) so a
    # source(paraphrase=…) can pin its cached, key-matched verdict. Written only by
    # `res judge --record`; no model is ever invoked. Absent file → empty cache → every
    # machine source reports needs-judgment (non-blocking).
    set_judgment_cache(JudgmentCache.load(_judge_cache_path(config)))


def _judge_cache_path(config) -> Path:
    """Where the literature verdict cache lives: ``--judge-cache``, else
    ``$SCIENTIST_JUDGE_CACHE``, else ``<--grounding-out or rootdir>/lit_judgments.json`` (next
    to the grounding report). ``--grounding-out`` is defined by the grounding plugin. (The env
    var keeps scientist's name because research operates on the same scientific-data tree.)"""
    explicit = config.getoption("--judge-cache", default=None) or os.environ.get("SCIENTIST_JUDGE_CACHE")
    if explicit:
        return Path(explicit)
    out = config.getoption("--grounding-out", default=None) or config.rootpath
    return Path(out) / JUDGMENT_CACHE_NAME
