"""scientist.grounding — scientist's grounding-layer additions, on top of the pytest-grounding package.

The claim-grounding **core** lives in the standalone ``grounding`` package
(``pip install pytest-grounding``) — import it **directly**, there is no façade here::

    from grounding import statement, evidence, uses, load, data, doc, DocRef, \
                          strength, caveats, kind, reviewed, Capture, record, current_capture

This package holds only scientist's own EXPERIMENT layers and exposes them:

  * the analysis-**derivation** recorder,
  * (``plugin.py``) the experiment-aware companion pytest plugin (the grounding package's plugin
    is the single capture + report engine; this one only adds the ``experiment`` fixture and a
    couple of guard tweaks).

The **literature** layer — ``paper()`` / ``source()`` / ``converge()`` / ``metric()`` /
``cited_by()``, the support-verdict cache, and ``res judge`` — moved out to the separate
``research`` skill (``skills/research``) in the scientist/research split; import it from
``research`` (``from research import source``). scientist no longer depends on it.
"""
from __future__ import annotations

from .derivation import (  # noqa: F401
    Derivation, derivation, DerivationAudit, audit_derivations, current_audit,
)

__all__ = [
    # derivation
    "derivation", "Derivation", "DerivationAudit", "audit_derivations", "current_audit",
    # compatibility shim
    "cross",
]


# --------------------------------------------------------------------------- #
# cross() — compatibility passthrough. It previously declared an intentional cross-experiment
# dependency for the (now-removed) reconcile lint; it now returns the study unchanged so older
# ``other = cross(k1_xxxxxx)`` call sites keep working. Cross-experiment composition still works
# via plain imports + ``uses`` (from the grounding package). This is about cross-EXPERIMENT claims,
# not literature — so it stays in scientist.
# --------------------------------------------------------------------------- #
def cross(study):
    """Compatibility shim: return ``study`` unchanged."""
    return study
