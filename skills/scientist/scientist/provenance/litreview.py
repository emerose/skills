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
  shows up by its absence); a *contested-status* treatment is reported as a content-based
  **advisory** — satisfied by competing accounts OR an explicit "no genuine controversy" finding,
  read off the prose, never by a heading title, never blocking;
* **must-confront** — the litreview's obligation set (claims tagged ``@must_confront`` in its
  ``test_litreview_<slug>.py`` module) is surfaced. The set keys off that module-name convention,
  so two empty-set cases are distinguished: a module that contributes **no claims at all** under
  the expected prefix is a *misnamed module* (or stale grounding) and a **loud blocking finding**
  (otherwise the obligation set silently dies — fails open); a correctly-named module *with* claims
  but none tagged is the softer "under-assessed?" **advisory**.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import report as REPORT

# A litreview must close with a gaps / open-questions section. Heading-level (## / ###) only.
_GAPS_HEADING_RE = re.compile(
    r"^#{2,3}\s+.*\b(gaps?|open\s+questions?|unknowns?|what.+not\s+settle)\b",
    re.IGNORECASE | re.MULTILINE)
_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")

# Contested-status treatment — content-based and ADVISORY (never blocking, never satisfiable by a
# heading title alone). A litreview owes an honest read of whether the field genuinely splits;
# that obligation is met EITHER by competing accounts laid side by side OR by an explicit finding
# of *no* genuine controversy (the real fault line is single-lab dependence; the evidence
# converges). So this matches both vocabularies and is read off the **prose**, with heading lines
# stripped — an author must never retitle a section to trip a regex. The real bar is engaging the
# must-confront set, not the presence of a "Controversies" heading.
_CONTESTED_STATUS_RE = re.compile(
    r"\b(controvers|disagree|conflict|contested|unresolved|competing|diverg|"
    r"converg|consensus|single[- ]lab|one lab|fault line)\b",
    re.IGNORECASE)


def _addresses_contested_status(text: str) -> bool:
    """Does the prose engage contested status at all — competing accounts OR an explicit
    no-controversy finding? Heading lines are stripped first, so a bare "## Controversies" with no
    discussion under it does NOT count (content, not a title)."""
    body = "\n".join(ln for ln in text.splitlines() if not _ANY_HEADING_RE.match(ln))
    return bool(_CONTESTED_STATUS_RE.search(body))


def _gaps_section_lines(text: str) -> set[int]:
    """The 1-based line numbers inside the gaps / open-questions section (its heading through the
    line before the next same-or-higher-level heading, or EOF). Used to exempt that section from the
    ``unsupported-quantity`` advisory: a number in an absence section ("regimes below ~25/50/75% are
    unmeasured") is an *illustrative hypothetical*, not an asserted quantity to back."""
    lines = text.splitlines()
    out: set[int] = set()
    in_gaps = False
    gaps_level = 0
    for i, line in enumerate(lines, start=1):
        m = _ANY_HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            if _GAPS_HEADING_RE.match(line):
                in_gaps, gaps_level = True, level
                out.add(i)
                continue
            if in_gaps and level <= gaps_level:
                in_gaps = False
        if in_gaps:
            out.add(i)
    return out


def _litreview_advisories(text: str,
                          claim_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The recall-aid advisories tuned for a **litreview** (vs a report — ``sci report`` keeps the
    report behavior). Two litreview-specific differences, both because a survey draws no conclusion
    so nothing *leans* on any one claim:

    * ``unsupported-quantity`` — skip the gaps / open-questions section: numbers there are
      illustrative hypotheticals (an absence is being described), not assertions to back.
    * ``weak-load-bearing`` — collapse to a **single** summary advisory, de-duplicated across the
      cited claims. In a conclusion-free survey a single-group / moderate claim is the *expected
      norm*, so the per-bound finding is pure noise; one line ("N bound(s) rest only on non-robust
      claims — expected in a survey") is enough."""
    gaps = _gaps_section_lines(text)
    advisories: list[dict[str, Any]] = [
        a for a in REPORT.prose_quantity_advisories(text, claim_index)
        if a["line"] not in gaps]

    wlb = REPORT.incommensurate_evidence_advisories(text, claim_index)
    if wlb:
        cites = sorted({c for a in wlb for c in a.get("cites", [])})
        advisories.append({
            "kind": "weak-load-bearing-survey",
            "line": min(a["line"] for a in wlb),
            "cites": cites,
            "count": len(wlb),
            "detail": f"{len(wlb)} bound(s) rest only on non-robust (single-group/moderate) "
                      "claim(s) — the expected norm in a conclusion-free survey (nothing leans on "
                      "them); not flagged per-bound. Cited: " + (", ".join(cites) or "—")})
    return advisories


def audit(review_path: Path, home: Path | None = None) -> dict[str, Any]:
    """Audit a ``review.md``: every ``[lit:]`` claim backed (via :func:`report.audit`), the
    literature-only contract, and the structural requirements. Returns the
    :func:`report.audit` result augmented with ``{kind, must_confront, contested_status_addressed}``
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

    # must-confront obligation set. The set keys off the convention `program/claims/
    # test_litreview_<slug>.py` (slug hyphens → underscores). A *misnamed* module silently
    # contributes zero claims under that prefix, which would otherwise fail OPEN (an empty
    # obligation set downgraded to a mild "under-assessed?" advisory). Distinguish the two cases:
    #   * the expected module contributes NO claims at all → LOUD finding (misnamed module / stale
    #     grounding); the obligation machinery is silently dead, so block;
    #   * the module DOES contribute claims but none are @must_confront → soft advisory (a
    #     genuinely uncontested, correctly-named survey).
    claim_index = REPORT.index_claims(home)
    prefix = REPORT.litreview_module_prefix(rp, home)
    module_path = REPORT.litreview_module_path(rp, home)
    module_claims = {cid: c for cid, c in claim_index.items() if cid.startswith(prefix)}
    mc = REPORT.litreview_must_confront(rp, home, claim_index)
    # Litreview-tuned recall aids (NOT base's report-tuned advisories): suppress weak-load-bearing
    # to a single summary line, and exempt the gaps section from unsupported-quantity. See
    # _litreview_advisories. `sci report` keeps the report behavior (base.audit's advisories).
    advisories: list[dict[str, Any]] = _litreview_advisories(text, claim_index)

    if not module_claims:
        on_disk = ("present on disk" if module_path.is_file()
                   else "not found on disk")
        findings.append({
            "kind": "missing-claims-module", "line": 0,
            "detail": f"expected claims module {REPORT._rel_or_name(module_path, home)} contributes "
                      f"no claims to the grounding report ({on_disk}) — misnamed module? the "
                      f"must-confront set + omissions audit key off the prefix '{prefix}'; rename "
                      f"the module to test_litreview_<slug>.py (hyphens→underscores) or re-run "
                      f"pytest --grounding-out"})
    elif not mc:
        advisories.append({
            "kind": "empty-must-confront", "line": 0, "cites": [],
            "detail": "no claim is tagged @must_confront — a survey that obliges a citing report "
                      "to confront nothing is usually under-assessed; mark the pivotal / contested "
                      "/ disconfirming claims"})

    status = "GROUNDED" if not findings else "BROKEN"
    return {**base, "kind": "litreview", "status": status, "findings": findings,
            "advisories": advisories, "must_confront": sorted(mc),
            "claims_module": REPORT._rel_or_name(module_path, home),
            "contested_status_addressed": _addresses_contested_status(text)}


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


def scaffold(home: Path, slug: str, *, title: str | None = None,
             scope: str = "program") -> dict[str, Any]:
    """Scaffold a new litreview under ``home`` — the highest-risk manual step removed (the claim
    module name). Mirrors the experiment ``new`` pattern; lays out, **without overwriting**:

    * ``<scope>/litreviews/<slug>/review.md`` — a MINIMAL stub (front matter + a pointer to
      ``references/litreview.md``). The supporting/contradicting/equivocal/absent structure is
      deliberately NOT baked in (the canonical structure is being redesigned — see references);
    * ``<scope>/litreviews/<slug>/prompt.md`` — a conclusion-free generation-brief stub;
    * ``<scope>/claims/test_litreview_<slug>.py`` — the **correctly-named** ``[lit:]`` claim module
      (slug hyphens → underscores), so the must-confront set / omissions audit key off it (the
      :func:`report.litreview_module_prefix` convention) by construction.

    ``scope`` is ``program`` (program/, the usual case) or an experiment folder name. Returns
    ``{slug, scope, created: [rel…], skipped: [rel…], module}`` — pure file I/O, store-free."""
    home = Path(home).resolve()
    slug_mod = slug.replace("-", "_")
    scope_dir = home / scope
    lr_dir = scope_dir / "litreviews" / slug
    claims_dir = scope_dir / "claims"
    module = claims_dir / f"test_litreview_{slug_mod}.py"
    review = lr_dir / "review.md"
    prompt = lr_dir / "prompt.md"

    title = title or slug.replace("-", " ")
    stubs = {
        review: (
            f"---\ntitle: \"{title}\"\ndate: \"\"          # YYYY-MM-DD\n"
            f"classification: \"\"\n---\n\n"
            f"<!-- A litreview is a neutral, thesis-independent survey of the third-party\n"
            f"     literature on ONE sub-question. [lit:] claims only — no [claim:]/[report:].\n"
            f"     STRUCTURE TBD — see references/litreview.md for the canonical sections\n"
            f"     (it is being redesigned; do not copy an old template here). A gaps /\n"
            f"     open-questions section is mandatory. Ground load-bearing assertions as\n"
            f"     [lit:] claims in {module.name}; tag the pivotal/contested/disconfirming\n"
            f"     ones @must_confront. -->\n\n"
            f"## Question & scope\n\n## Gaps / open questions\n"),
        prompt: (
            f"# Generation brief — litreview `{slug}`\n\n"
            f"A **conclusion-free** survey of the literature on this sub-question: map what the\n"
            f"field reports, who reports it, how strong each piece is, where it disagrees or is\n"
            f"silent. Draw NO program conclusion (no dose/target/lead call). See\n"
            f"references/litreview.md and references/report-authoring.md.\n\n"
            f"## Sub-question\n\n## Must cover\n\n## Known controversies / disconfirmers\n"),
        module: (
            f'"""[lit:] claim module for litreview `{slug}`.\n\n'
            f"Correctly named so the must-confront set and the [litreview:] omissions audit key\n"
            f"off it (test_litreview_<slug>.py, slug hyphens→underscores). Author each load-bearing\n"
            f"assertion as a grounded [lit:] claim per references/report.md; tag the pivotal /\n"
            f"contested / disconfirming ones @must_confront. Run with --grounding-out to emit the\n"
            f'grounding report the audit reads.\n"""\n'
            f"from scientist.grounding import kind, strength, must_confront, source  # noqa: F401\n\n\n"
            f"# @kind"
            f'("literature")\n'
            f"# @strength"
            f'("moderate")\n'
            f"# @must_confront"
            f'("one line on why any report in this area must address this")\n'
            f"# def test_example_pivotal_finding():\n"
            f'#     """A one-line statement of the surveyed assertion."""\n'
            f"#     source(citekey=\"author2020\", quote=\"verbatim span from the paper\",\n"
            f"#            paraphrase=\"what the claim asserts\")\n"),
    }

    created: list[str] = []
    skipped: list[str] = []
    for path, content in stubs.items():
        if path.exists():
            skipped.append(REPORT._rel_or_name(path, home))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(REPORT._rel_or_name(path, home))
    return {"slug": slug, "scope": scope, "created": created, "skipped": skipped,
            "module": REPORT._rel_or_name(module, home)}


def render_audit(result: dict[str, Any]) -> str:
    """Human-readable litreview audit — the :func:`report.render_audit` body plus the litreview's
    must-confront set and structural status."""
    out = REPORT.render_audit(result)
    mc = result.get("must_confront") or []
    misnamed = any(f.get("kind") == "missing-claims-module" for f in result.get("findings", []))
    if mc:
        names = ", ".join(REPORT._short_claim_id(c) for c in mc)
        mc_line = f"  must-confront: {len(mc)} claim(s) — {names}"
    elif misnamed:
        # Don't launder a dead obligation set as a mild "under-assessed?" — the loud finding above
        # already says why (misnamed module / stale grounding); point back at it.
        mc_line = (f"  must-confront: 0 — claims module {result.get('claims_module')} contributes "
                   f"no claims (see missing-claims-module finding above)")
    else:
        mc_line = "  must-confront: 0 (none tagged — under-assessed?)"
    ctrl = ("addressed" if result.get("contested_status_addressed")
            else "not evident in the prose")
    return (out + "\n" + mc_line +
            f"\n  contested-status: {ctrl} (advisory — met by competing accounts OR an explicit "
            f"'no genuine controversy; the contested axis is X' finding; the real bar is engaging "
            f"the must-confront set, not a heading)")
