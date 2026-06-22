"""scientist — consolidated scientific-data package.

Umbrella for the skill's runtime: provenance core, lab-file readers, the
raw→data extractor, the libkit-backed store, the typed experiment accessor,
and the claim-grounding harness. Subpackages are imported explicitly
(``from scientist.experiments import k1_000000``, ``from scientist.grounding
import strength``); this marker re-exports nothing.

The generic report engine lives in the sibling in-repo library ``reportkit``
(``skills/reportkit``), consumed via a ``sys.path`` reach rather than a pip dependency —
matching how this repo wires cross-skill imports (there is no workspace/root ``pyproject``).
:func:`_bootstrap_reportkit` walks up from this package to find ``skills/reportkit`` and puts
it on ``sys.path``, so ``import reportkit`` resolves whether ``scientist`` is
``pip install -e``'d or run via ``uv run --with-editable skills/scientist``. A standalone
``pip install reportkit`` (its own test env) resolves normally and this is a no-op.
"""


def _bootstrap_reportkit() -> None:
    """Put the in-repo ``reportkit`` package on ``sys.path`` if it is not already importable."""
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
