"""scientist.grounding.plugin — the scientist-specific COMPANION to the grounding plugin.

The standalone **grounding** package (pip: pytest-grounding) ships the single capture + report
engine: it owns ``--grounding-out``/``--grounding-fresh``, the per-claim :class:`Capture`, the
bypass guard, the judgment markers, and ``grounding_report.{json,md}``. This companion adds
ONLY scientist's experiment extras and deliberately does **not** redefine those options, so the
plugins coexist with no conflict:

  * the zero-boilerplate ``experiment`` fixture (the :class:`Study` resolved from the test's
    path, so no per-experiment conftest is needed),

and, at import time, two adjustments so the library's bypass guard covers the scientist tree
exactly as before:

  * extend the guard's tracked suffixes with GraphPad Prism ``.pzfx``,
  * bridge ``GROUNDING_ROOT`` ← ``SCIENTIST_HOME`` (the library guard roots at the former;
    scientist's data tree is the latter).

Auto-loaded via the ``pytest11`` entry point alongside the grounding plugin.

The **literature support-verdict cache** (and ``--judge-cache``) moved out to the ``research``
skill's companion plugin (``research.plugin``) in the scientist/research split — when the data
repo runs literature claims, that third plugin loads the cache so ``source(paraphrase=…)`` can read
it. This companion no longer touches it.

Dropped in the move to the grounding plugin (were advisory/unused-downstream): the
docstring-as-statement behavior (call ``statement()`` now), the K1 ``reconcile`` lint, and the
``--check-drift`` git-blame drift check.
"""
from __future__ import annotations

import os
from pathlib import Path

import grounding
import pytest

# --- import-time guard adjustments (run once, before any pytest_configure) ----------------- #
# scientist tracks GraphPad Prism XML alongside the library's generic data/doc formats, so an
# untracked .pzfx read is flagged by the bypass guard. TRACKED_SUFFIXES is the shared set the
# guard consults; mutating it here extends coverage for the whole session.
grounding.TRACKED_SUFFIXES.add(".pzfx")
# The library's bypass guard roots at $GROUNDING_ROOT; scientist's data tree is $SCIENTIST_HOME.
# Bridge them so reads under the data tree are guarded exactly as before.
if os.environ.get("SCIENTIST_HOME") and not os.environ.get("GROUNDING_ROOT"):
    os.environ["GROUNDING_ROOT"] = os.environ["SCIENTIST_HOME"]


# --------------------------------------------------------------------------- #
# Experiment access — zero-boilerplate, resolved from the test's location.
# --------------------------------------------------------------------------- #
def _home_exp(node_path) -> str | None:
    """The K1-NNNNNN experiment code whose tree this test file lives in (its
    ``analysis/claims/`` dir is under ``<exp>/``). Found by walking up to the folder that holds
    an ``experiment.yml`` and is named ``K1-...``."""
    p = Path(str(node_path))
    for parent in p.parents:
        if parent.name.upper().startswith("K1-") and (parent / "experiment.yml").is_file():
            return parent.name.split(" ")[0].upper()
    return None


@pytest.fixture
def experiment(request):
    """The :class:`Study` whose ``analysis/claims/`` this test lives in — resolved from the
    test file's path, so **no per-experiment conftest is needed**. Use as
    ``def test_x(experiment): ...``. (Cross-experiment claims still import a specific other
    study via ``from scientist.experiments import k1_NNNNNN``.)"""
    from .. import experiments as _exp
    code = _home_exp(request.node.path)
    if code is None:
        raise RuntimeError(
            f"no enclosing K1-* experiment for {request.node.path} "
            f"(is the claim under <exp>/analysis/claims/ next to an experiment.yml?)")
    return getattr(_exp, code.lower().replace("-", "_"))   # cached Study
