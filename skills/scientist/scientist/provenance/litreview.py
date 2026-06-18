"""The litreview phase — ``sci litreview`` (audit / must-confront / render / trace).

A *litreview* (``kind=litreview``) is a neutral, thesis-independent survey of the **third-party
literature** on one sub-question: an organized, assessed map of what the field reports, how strong
each piece is, and where it disagrees or is silent. It draws **no program conclusions**. A *report*
argues toward a recommendation; a litreview only lays out the evidence a report argues *from*. See
``references/litreview.md`` for the full discipline.

This module is the litreview's **own** audit — distinct from the ``[litreview:]`` *omissions* audit
that lives in :mod:`provenance.report` (a property of the *consuming* report). It is store-free and
**reuses** :mod:`provenance.report` wholesale: a ``review.md`` is ``[lit:]``-only report-shaped
Markdown, so citation parsing and the ``[lit:]`` verdict are not re-implemented here. On top of
``report.audit`` it adds the litreview-specific contract:

* **literature-only** — a ``[claim:]`` (Kicho data), ``[report:]``, or nested ``[litreview:]``
  citation is a blocking finding (Kicho data meets the literature only in the citing report);
* **structure** — a *gaps / open-questions* section is mandatory (the first place incompleteness
  shows up by its absence); a *controversies* section is reported when present;
* **must-confront** — the litreview's obligation set (claims tagged ``@must_confront`` in its
  ``test_litreview_<slug>.py`` module) is surfaced; an empty set is an advisory (a survey that
  obliges a citing report to confront nothing is usually under-assessed).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import report as REPORT

# A litreview must close with a gaps / open-questions section; a controversies section is reported
# when present (competing claims belong side by side, not flattened). Heading-level (## / ###) only.
_GAPS_HEADING_RE = re.compile(
    r"^#{2,3}\s+.*\b(gaps?|open\s+questions?|unknowns?|what.+not\s+settle)\b",
    re.IGNORECASE | re.MULTILINE)
_CONTROVERSY_HEADING_RE = re.compile(
    r"^#{2,3}\s+.*\b(controvers|disagree|conflict|contested|unresolved)\b",
    re.IGNORECASE | re.MULTILINE)


def audit(review_path: Path, home: Path | None = None) -> dict[str, Any]:
    """Audit a ``review.md``: every ``[lit:]`` claim backed (via :func:`report.audit`), the
    literature-only contract, and the structural requirements. Returns the
    :func:`report.audit` result augmented with ``{kind, must_confront, has_controversy_section}``
    and a recomputed ``status`` (``GROUNDED`` iff no blocking finding)."""
    rp = Path(review_path).resolve()
    home = REPORT._resolve_home(home, rp)
    base = REPORT.audit(rp, home=home)
    text = rp.read_text(encoding="utf-8")
    findings: list[dict[str, Any]] = list(base["findings"])

    # literature-only: a litreview surveys third-party work, nothing else.
    for c in base.get("citations", []):
        findings.append({
            "kind": "kicho-data-in-litreview", "line": c["line"], "cite": c["id"],
            "detail": "a litreview surveys only third-party literature — use [lit:], not [claim:] "
                      "(Kicho data meets the literature in the citing report, not here)"})
    for rc in base.get("report_cites", []):
        findings.append({
            "kind": "report-cite-in-litreview", "line": rc["line"], "cite": rc["id"],
            "detail": "a litreview does not rest on a report's conclusion — survey the literature "
                      "directly with [lit:]"})
    for lv in base.get("litreview_cites", []):
        findings.append({
            "kind": "nested-litreview", "line": lv["line"], "cite": lv["id"],
            "detail": "litreviews do not nest — keep each survey self-contained"})

    # structure: a gaps / open-questions section is mandatory.
    if not _GAPS_HEADING_RE.search(text):
        findings.append({
            "kind": "missing-gaps-section", "line": 0,
            "detail": "a litreview must close with a gaps / open-questions section — what the "
                      "literature does NOT settle (its analog of a report's assumptions section)"})

    # must-confront obligation set (advisory when empty — usually under-assessed, not always wrong).
    claim_index = REPORT.index_claims(home)
    mc = REPORT.litreview_must_confront(rp, home, claim_index)
    advisories: list[dict[str, Any]] = list(base.get("advisories", []))
    if not mc:
        advisories.append({
            "kind": "empty-must-confront", "line": 0, "cites": [],
            "detail": "no claim is tagged @must_confront — a survey that obliges a citing report "
                      "to confront nothing is usually under-assessed; mark the pivotal / contested "
                      "/ disconfirming claims"})

    status = "GROUNDED" if not findings else "BROKEN"
    return {**base, "kind": "litreview", "status": status, "findings": findings,
            "advisories": advisories, "must_confront": sorted(mc),
            "has_controversy_section": bool(_CONTROVERSY_HEADING_RE.search(text))}


def must_confront_listing(review_path: Path, home: Path | None = None) -> list[dict[str, Any]]:
    """The litreview's must-confront obligation set as ``[{claim_id, statement, strength, outcome,
    reason}]`` — the claims any citing report must address (cite or waive). ``reason`` is the
    ``@must_confront(...)`` text."""
    rp = Path(review_path).resolve()
    home = REPORT._resolve_home(home, rp)
    mc = REPORT.litreview_must_confront(rp, home, REPORT.index_claims(home))
    return [{"claim_id": cid, "statement": c.get("statement"), "strength": c.get("strength"),
             "outcome": c.get("outcome"), "reason": c.get("must_confront")}
            for cid, c in sorted(mc.items())]


def delta(review_path: Path, baseline: Path, home: Path | None = None) -> dict[str, Any]:
    """The claim-set delta of a litreview's module — current vs a ``baseline``
    grounding_report.json — for the cheap-update delta-judge. ``baseline`` is just an older copy of
    the grounding report (the git part stays the caller's: ``git show <ref>:program/analysis/
    grounding_report.json > baseline.json``), so this is a pure function of two files.

    Returns ``{added, removed, must_confront_added, must_confront_removed, drifted}`` — claim ids
    that appeared, disappeared, entered/left the must-confront set, or whose drift signature
    (outcome/strength/quote/paraphrase/retraction) changed. A delta touching the must-confront set
    or a claim the citing report relies on is what escalates to the fresh-context judge; an empty
    delta means nothing for a citing report to re-examine."""
    rp = Path(review_path).resolve()
    home = REPORT._resolve_home(home, rp)
    prefix = REPORT.litreview_module_prefix(rp, home)
    scope_id = prefix.split("::", 1)[0]
    cur = {cid: c for cid, c in REPORT.index_claims(home).items() if cid.startswith(prefix)}

    base: dict[str, dict[str, Any]] = {}
    data = json.loads(Path(baseline).read_text(encoding="utf-8"))
    for c in data.get("claims", []) if isinstance(data, dict) else []:
        if not isinstance(c, dict):
            continue
        fid = REPORT.claim_id_for(scope_id, c.get("id") or "")
        if fid.startswith(prefix):
            base[fid] = c

    cur_ids, base_ids = set(cur), set(base)
    mc = lambda d: {cid for cid, c in d.items() if c.get("must_confront")}  # noqa: E731
    return {
        "added": sorted(cur_ids - base_ids),
        "removed": sorted(base_ids - cur_ids),
        "must_confront_added": sorted(mc(cur) - mc(base)),
        "must_confront_removed": sorted(mc(base) - mc(cur)),
        "drifted": sorted(cid for cid in (cur_ids & base_ids)
                          if REPORT._claim_drift_sig(cur[cid]) != REPORT._claim_drift_sig(base[cid])),
    }


def render_delta(d: dict[str, Any]) -> str:
    rows = [("added", d["added"]), ("removed", d["removed"]),
            ("must-confront +", d["must_confront_added"]),
            ("must-confront -", d["must_confront_removed"]), ("drifted", d["drifted"])]
    out = []
    for label, ids in rows:
        if ids:
            out.append(f"  {label}: " + ", ".join(REPORT._short_claim_id(c) for c in ids))
    if not out:
        return "no change (nothing for a citing report to re-examine)"
    head = "litreview delta — escalate to the delta-judge if any cited/must-confront claim moved:"
    return head + "\n" + "\n".join(out)


def render_audit(result: dict[str, Any]) -> str:
    """Human-readable litreview audit — the :func:`report.render_audit` body plus the litreview's
    must-confront set and structural status."""
    out = REPORT.render_audit(result)
    mc = result.get("must_confront") or []
    if mc:
        names = ", ".join(REPORT._short_claim_id(c) for c in mc)
        mc_line = f"  must-confront: {len(mc)} claim(s) — {names}"
    else:
        mc_line = "  must-confront: 0 (none tagged — under-assessed?)"
    ctrl = "present" if result.get("has_controversy_section") else "absent"
    return out + "\n" + mc_line + f"\n  controversies section: {ctrl}"
