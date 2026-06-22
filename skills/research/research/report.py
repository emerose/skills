"""research's report façade — the generic engine + research's literature citation layer.

A thin compatibility shim, the research-side analogue of what scientist used to carry. The
generic grounded-report engine — parse / audit / render / advisories / scope — lives in the
in-repo library :mod:`reportkit.report` (``skills/reportkit``), consumed via a ``sys.path`` reach
(see :func:`research._bootstrap_reportkit`). research's *literature* citation layer
(``[lit:]`` / ``[litreview:]``: verdicts, the paper-claims/bibliometric resolvers, the
PROSPERO/PRISMA protocol pin, citation labels) lives beside it in
:mod:`research.literature_cites`, which registers those schemes with the engine *on import*.

This module re-exports both so ``research.report.<name>`` resolves the engine surface AND the
literature surface for research's own modules (``litreview`` / ``reviewtree`` / ``res``) — and,
by importing :mod:`literature_cites`, guarantees the literature resolvers are registered whenever
research audits a review. (scientist keeps an equivalent engine-only shim of its own; the two
skills never import each other — they meet only at the engine's citation registry.)
"""

from __future__ import annotations

# The generic engine. ``import *`` brings its public surface; the underscore-prefixed helpers
# below are the ones research's callers (and the moved tests) reach for through this module.
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

# The literature citation layer. Importing it registers the [lit:]/[litreview:] schemes with the
# engine; ``import *`` re-exports its public surface (verdicts, [litreview:] path/protocol helpers,
# citation labels) so ``report.<name>`` keeps resolving.
from .literature_cites import *  # noqa: F401,F403
from .literature_cites import (  # noqa: F401
    _LIT_RE,
    _LITREVIEW_RE,
    _PROTOCOL_HEADINGS,
    _asof_age_days,
    _bucket_metric,
    _claim_drift_sig,
    _paperclaims,
)
