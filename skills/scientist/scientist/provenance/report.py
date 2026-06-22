"""The report phase — ``sci report`` (build / audit / render).

A thin compatibility shim over the generic grounded-report engine. The engine — parse / audit /
render / advisories / scope — lives in the in-repo library :mod:`reportkit.report`
(``skills/reportkit``), consumed via a ``sys.path`` reach (see
:func:`scientist._bootstrap_reportkit`). This module re-exports its public surface so
``scientist.provenance.report.<name>`` keeps resolving for every existing caller (``sci`` / the
store / trace).

scientist is now **literature-free**: the ``[lit:]`` / ``[litreview:]`` citation layer lives in a
separate ``research`` skill (``skills/research``), which registers those schemes with the engine on
import. scientist does not depend on research; ``sci report`` *optionally* imports research's
resolvers at audit time so an experiment report that cites ``[lit:]`` / ``[litreview:]`` resolves
when research is installed — and when it isn't, the engine surfaces a non-blocking
``unregistered-scheme`` warning rather than silently dropping the citation. The two skills meet only
at the engine's citation registry; neither imports the other.
"""

from __future__ import annotations

# The generic engine. ``import *`` brings its public surface; the underscore-prefixed helpers
# below are the ones existing callers (and tests) reach for through this module.
from reportkit.report import *  # noqa: F401,F403
from reportkit.report import (  # noqa: F401
    _CITE_RE,
    _EMBED_RE,
    _REPORT_RE,
    _claim_quantities,
    _front_matter,
    _infer_home,
    _quantities,
    _rel_or_name,
    _resolve_home,
    _review_note,
    _section_bodies,
    _short_claim_id,
    _strip_front_matter_keys,
    _to_pct,
)
