"""reportkit — the generic grounded-report engine.

The phase-terminal *report* machinery shared by every skill that turns sha-pinned,
grounded claims into a human-facing, git-diffable Markdown deliverable: parse a report's
inline citations + figure/table embeds, audit each against the live grounding evidence,
surface non-blocking review advisories, render a self-contained Markdown (footnoted
citations, inlined tables) and drive pandoc, and trace a report down through its citations
to the raw measurements.

The engine natively knows three citation kinds — ``[claim:<id>]`` (a grounded internal
claim), ``[report:<id>]`` (a lemma sub-report), and ``![..](..)`` embeds. Every OTHER
scheme plugs in through the citation-resolver registry (:func:`register_citation`): a
scheme supplies its regex (so the parser discovers it), an audit resolver, and optional
render hooks. That seam is what lets a downstream skill add a citation layer (e.g. a
literature ``[lit:]`` / ``[litreview:]`` layer) without the engine importing it — the
engine stays domain-generic and library-free.

Not a PyPI package: it knows the in-house ``[claim:]`` citation grammar, so it is only
useful inside this skills repo. Consumed via a ``sys.path`` reach from the host skill.

Stdlib + PyYAML (``*.csv`` table inlining uses the stdlib ``csv``; pandoc is an external
binary the render step shells out to).
"""

from __future__ import annotations

from . import report, trace
from .report import (
    audit,
    index_claims,
    parse_report,
    parse_sections,
    register_citation,
    render,
    render_audit,
    render_markdown,
    report_scope,
    resolve_citation,
)
from .trace import render_report_trace, trace_report

__all__ = [
    "report", "trace",
    "register_citation", "audit", "render", "render_markdown", "render_audit",
    "parse_report", "parse_sections", "index_claims", "resolve_citation", "report_scope",
    "trace_report", "render_report_trace",
]
