"""The litreview phase — ``sci litreview`` (audit / ingest-discover / render / trace / delta).

A *litreview* (``kind=litreview``) is a neutral, thesis-independent survey of the **third-party
literature** on one sub-question: an organized, assessed map of what the field reports, how strong
each piece is, and where it disagrees or is silent. It draws **no program conclusions**. A *report*
argues toward a recommendation; a litreview only lays out the evidence a report argues *from*. See
``references/litreview.md`` for the full discipline.

This module is the litreview's **own** audit. It is store-free and **reuses**
:mod:`provenance.report` wholesale: a ``review.md`` is ``[lit:]``-only report-shaped Markdown, so
citation parsing and the ``[lit:]`` verdict are not re-implemented here. On top of ``report.audit``
it adds the litreview-specific contract:

* **literature-only** — a ``[claim:]`` (Kicho data), ``[report:]``, or nested ``[litreview:]``
  citation is a blocking finding (Kicho data meets the literature only in the citing report);
* **structure** — a *gaps / open-questions* section is mandatory (the first place incompleteness
  shows up by its absence); a *contested-status* treatment is reported as a content-based
  **advisory** — satisfied by competing accounts OR an explicit "no genuine controversy" finding,
  read off the prose, never by a heading title, never blocking;
* **PROSPERO/PRISMA discipline** — two committed artifacts beside ``review.md`` make the survey's
  *method and screening* auditable, replacing the old hand-tagged ``@must_confront`` obligation set
  (show your work, don't tag which findings matter):

  * ``protocol.md`` — pre-registration: front matter ``slug``/``as_of``/``sources`` + the four
    headings *Question & scope* / *Search queries* / *Inclusion criteria* / *Exclusion criteria*,
    each with a non-empty body. A missing file is ``missing-protocol``; a missing/empty key or
    heading is ``missing-protocol-field`` (both blocking). Content *quality* is the completeness
    critic's job, not the tool's.
  * ``screening.jsonl`` — the PRISMA flow: one JSON object per candidate, tracked to
    *included* (with a ``citekey``) or *excluded* (with a ``reason``). A malformed row is
    ``malformed-screening-row``; an excluded row with no reason is ``excluded-without-reason``
    (both blocking). The PRISMA funnel (identified → included / excluded-by-reason) is derived.

* **coverage cross-check** — the integrity core: every ``[lit:]``-cited paper (by citekey) must
  appear as an ``included`` screening row (else blocking ``cited-paper-unscreened``); an
  ``included`` row no claim cites is the advisory ``included-but-uncited``.
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
# stripped — an author must never retitle a section to trip a regex.
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


# --------------------------------------------------------------------------- #
# PROSPERO/PRISMA — protocol validation + screening parse + the funnel/cross-check
# --------------------------------------------------------------------------- #
def validate_protocol(review_path: Path, home: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate the ``protocol.md`` beside ``review.md``. Returns ``(parsed, findings)`` where
    ``parsed`` is :func:`report.parse_protocol`'s result and ``findings`` carries
    ``missing-protocol`` (file absent) or ``missing-protocol-field`` (a missing/empty front-matter
    key or one of the four required headings). Presence + non-emptiness only — whether the criteria
    are *good* is the completeness critic's call, not the tool's."""
    proto = REPORT.parse_protocol(REPORT.litreview_protocol_path(review_path))
    if not proto["present"]:
        return proto, [{
            "kind": "missing-protocol", "line": 0,
            "detail": "no protocol.md beside review.md — pre-register the survey (question & scope, "
                      "search queries, inclusion + exclusion criteria) BEFORE screening; "
                      "`sci new-litreview` scaffolds it"}]

    findings: list[dict[str, Any]] = []
    fm = proto["front_matter"]
    for key in ("slug", "as_of", "sources"):
        val = fm.get(key)
        empty = (val is None
                 or (isinstance(val, (str, list, dict, tuple)) and len(val) == 0)
                 or (isinstance(val, str) and not val.strip()))
        if empty:
            findings.append({
                "kind": "missing-protocol-field", "line": 0, "field": key,
                "detail": f"protocol.md front matter is missing or empty `{key}` "
                          f"(required: slug, as_of, sources)"})
    for heading in REPORT._PROTOCOL_HEADINGS:
        body = proto["headings"].get(heading.lower())
        if not body or not str(body).strip():
            findings.append({
                "kind": "missing-protocol-field", "line": 0, "field": heading,
                "detail": f"protocol.md has no non-empty `## {heading}` section "
                          f"(all four headings are required)"})
    return proto, findings


def parse_screening(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse ``screening.jsonl`` into ``(rows, findings)`` — one well-formed candidate object per
    non-blank line. ``findings``:

    * ``missing-screening`` (blocking) — the file is absent;
    * ``malformed-screening-row`` (blocking) — a line that is not a JSON object, has no ``id``,
      carries a ``decision`` other than ``included|excluded``, or is ``included`` with no
      ``citekey``;
    * ``excluded-without-reason`` (blocking) — an ``excluded`` row with no non-empty ``reason``.

    A row with ``decision`` unset is a *pending* candidate (e.g. just ingested via
    ``--ingest-discover``), neither malformed nor a finding — the author fills it in by hand."""
    p = Path(path)
    if not p.is_file():
        return [], [{
            "kind": "missing-screening", "line": 0,
            "detail": "no screening.jsonl beside review.md — account for the full retrieved set "
                      "(every candidate → included|excluded-with-reason); seed it with "
                      "`sci litreview <review.md> --ingest-discover <discover.json>`"}]

    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for i, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            findings.append({"kind": "malformed-screening-row", "line": i,
                             "detail": f"line {i} is not valid JSON"})
            continue
        if not isinstance(obj, dict):
            findings.append({"kind": "malformed-screening-row", "line": i,
                             "detail": f"line {i} is not a JSON object"})
            continue
        rid = obj.get("id")
        if not rid or not str(rid).strip():
            findings.append({"kind": "malformed-screening-row", "line": i,
                             "detail": f"row {i} has no `id` (required; doi:/arxiv:/pmid: form)"})
            continue
        rid = str(rid)
        decision = obj.get("decision")
        if decision is not None and decision not in ("included", "excluded"):
            findings.append({"kind": "malformed-screening-row", "line": i,
                             "detail": f"row {rid} has decision={decision!r} "
                                       f"(must be included|excluded, or unset for a pending row)"})
        elif decision == "excluded" and not str(obj.get("reason") or "").strip():
            findings.append({"kind": "excluded-without-reason", "line": i,
                             "detail": f"excluded row {rid} has no `reason` "
                                       f"(an exclusion must be accounted for)"})
        elif decision == "included" and not str(obj.get("citekey") or "").strip():
            findings.append({"kind": "malformed-screening-row", "line": i,
                             "detail": f"included row {rid} has no `citekey` "
                                       f"(required to cross-check against [lit:] citations)"})
        rows.append(obj)
    return rows, findings


def prisma_funnel(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive the PRISMA flow from screening rows: ``{identified, included, excluded, pending,
    excluded_by_reason}``. ``identified`` is every candidate row; ``pending`` is the rows with
    ``decision`` still unset."""
    included = sum(1 for r in rows if r.get("decision") == "included")
    excluded = [r for r in rows if r.get("decision") == "excluded"]
    pending = sum(1 for r in rows if r.get("decision") not in ("included", "excluded"))
    by_reason: dict[str, int] = {}
    for r in excluded:
        key = str(r.get("reason") or "—").strip() or "—"
        by_reason[key] = by_reason.get(key, 0) + 1
    return {"identified": len(rows), "included": included, "excluded": len(excluded),
            "pending": pending, "excluded_by_reason": by_reason}


def _cited_citekeys(text: str, claim_index: dict[str, dict[str, Any]]) -> set[str]:
    """The set of paper citekeys the review's ``[lit:]`` citations resolve to (across each cited
    claim's ``lit_sources``/``metric_sources``). The unit the coverage cross-check compares against
    the ``included`` screening rows."""
    cks: set[str] = set()
    parsed = REPORT.parse_report(text)
    for lc in parsed.get("lit_cites", []):
        cands = REPORT.resolve_citation(lc["id"], claim_index)
        if len(cands) != 1:
            continue
        ev = claim_index[cands[0]].get("evidence") or {}
        for field in ("lit_sources", "metric_sources"):
            for s in (ev.get(field) or []):
                if isinstance(s, dict) and str(s.get("citekey") or "").strip():
                    cks.add(str(s["citekey"]))
    return cks


def _coverage_crosscheck(
        text: str, claim_index: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The integrity core. Returns ``(findings, advisories)``:

    * ``cited-paper-unscreened`` (blocking) — a ``[lit:]``-cited paper that is not an ``included``
      screening row: a citation that never passed (or was never recorded in) the funnel;
    * ``included-but-uncited`` (advisory) — an ``included`` paper no ``[lit:]`` claim cites: either
      screened-in-but-not-yet-written, or a candidate to drop."""
    cited = _cited_citekeys(text, claim_index)
    included = {str(r.get("citekey")).strip() for r in rows
                if r.get("decision") == "included" and str(r.get("citekey") or "").strip()}
    findings: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    for ck in sorted(cited - included):
        findings.append({
            "kind": "cited-paper-unscreened", "line": 0, "cite": ck,
            "detail": f"[lit:]-cited paper `{ck}` is not an `included` row in screening.jsonl — "
                      f"every cited paper must be tracked through screening (screen it in, or drop "
                      f"the citation)"})
    for ck in sorted(included - cited):
        advisories.append({
            "kind": "included-but-uncited", "line": 0, "cites": [ck],
            "detail": f"included paper `{ck}` is screened-in but cited by no [lit:] claim — write "
                      f"it into the survey or drop it from screening"})
    return findings, advisories


def audit(review_path: Path, home: Path | None = None) -> dict[str, Any]:
    """Audit a ``review.md``: every ``[lit:]`` claim backed (via :func:`report.audit`), the
    literature-only contract, the mandatory gaps section, and the PROSPERO/PRISMA discipline
    (protocol + screening + coverage cross-check). Returns the :func:`report.audit` result
    augmented with ``{kind, funnel, protocol_present, screening_rows,
    contested_status_addressed}`` and a recomputed ``status`` (``GROUNDED`` iff no blocking
    finding)."""
    rp = Path(review_path).resolve()
    home = REPORT._resolve_home(home, rp)
    base = REPORT.audit(rp, home=home)
    text = rp.read_text(encoding="utf-8")
    findings: list[dict[str, Any]] = list(base["findings"])
    claim_index = REPORT.index_claims(home)
    # Litreview-tuned recall aids (NOT base's report-tuned advisories): suppress weak-load-bearing
    # to a single summary line, and exempt the gaps section from unsupported-quantity. See
    # _litreview_advisories. `sci report` keeps the report behavior (base.audit's advisories).
    advisories: list[dict[str, Any]] = _litreview_advisories(text, claim_index)

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

    # PROSPERO/PRISMA: the committed method + screening artifacts.
    proto, proto_findings = validate_protocol(rp, home)
    findings.extend(proto_findings)
    rows, screen_findings = parse_screening(REPORT.litreview_screening_path(rp))
    findings.extend(screen_findings)
    funnel = prisma_funnel(rows)
    cov_findings, cov_advisories = _coverage_crosscheck(text, claim_index, rows)
    findings.extend(cov_findings)
    advisories.extend(cov_advisories)

    status = "GROUNDED" if not findings else "BROKEN"
    return {**base, "kind": "litreview", "status": status, "findings": findings,
            "advisories": advisories, "funnel": funnel,
            "protocol_present": proto["present"], "screening_rows": len(rows),
            "contested_status_addressed": _addresses_contested_status(text)}


# --------------------------------------------------------------------------- #
# --ingest-discover — seed screening.jsonl from `bib discover --json`
# --------------------------------------------------------------------------- #
def _row_id(result: dict[str, Any]) -> str | None:
    """A prefixed identifier for a discover result: ``doi:``/``arxiv:``/``pmid:`` of the first
    present id, or None if the result carries none (skipped — an ``id`` is required)."""
    for field, prefix in (("doi", "doi:"), ("arxiv_id", "arxiv:"), ("pmid", "pmid:")):
        val = result.get(field)
        if val and str(val).strip():
            return prefix + str(val).strip()
    return None


def ingest_discover(review_path: Path, discover_path: Path, *, home: Path | None = None,
                    query: str | None = None) -> dict[str, Any]:
    """Append candidate rows to ``screening.jsonl`` from a ``bib discover --json`` payload
    (see references/litreview.md → *Gathering* and SPEC §9). Each result maps to a row with
    ``decision`` **unset** (the author screens by hand afterward): ``id`` from doi/arxiv_id/pmid,
    ``source`` = ``found_in``, the rank signals copied through, and ``citekey`` from
    ``library_citekey`` when ``in_library``. De-duped by ``id`` against the existing file — a result
    whose id is already present is skipped. ``sci`` never calls the search API; a re-discover is
    re-fed through here. Returns ``{appended, skipped_duplicate, skipped_no_id, screening}``."""
    rp = Path(review_path).resolve()
    home = REPORT._resolve_home(home, rp)
    screening = REPORT.litreview_screening_path(rp)

    data = json.loads(Path(discover_path).read_text(encoding="utf-8"))
    results = data.get("results", []) if isinstance(data, dict) else []
    eff_query = query if query is not None else (data.get("query") if isinstance(data, dict) else None)

    existing_ids: set[str] = set()
    if screening.is_file():
        for raw in screening.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict) and obj.get("id"):
                existing_ids.add(str(obj["id"]))

    appended: list[dict[str, Any]] = []
    skipped_dup = skipped_no_id = 0
    for r in results:
        if not isinstance(r, dict):
            continue
        rid = _row_id(r)
        if rid is None:
            skipped_no_id += 1
            continue
        if rid in existing_ids:
            skipped_dup += 1
            continue
        existing_ids.add(rid)
        row: dict[str, Any] = {"id": rid}
        if r.get("title"):
            row["title"] = r["title"]
        if r.get("year"):
            row["year"] = r["year"]
        if r.get("found_in"):
            row["source"] = r["found_in"]
        if eff_query:
            row["query"] = eff_query
        for sig in ("citation_percentile", "fwci", "cited_by_count"):
            if r.get(sig) is not None:
                row[sig] = r[sig]
        if r.get("in_library") and r.get("library_citekey"):
            row["citekey"] = r["library_citekey"]
        appended.append(row)

    if appended:
        screening.parent.mkdir(parents=True, exist_ok=True)
        prefix = ""
        if screening.is_file() and screening.read_text(encoding="utf-8") and \
                not screening.read_text(encoding="utf-8").endswith("\n"):
            prefix = "\n"
        with screening.open("a", encoding="utf-8") as fh:
            fh.write(prefix + "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in appended))

    return {"appended": len(appended), "skipped_duplicate": skipped_dup,
            "skipped_no_id": skipped_no_id,
            "screening": REPORT._rel_or_name(screening, home)}


# --------------------------------------------------------------------------- #
# --delta — the cheap-update claim-set diff
# --------------------------------------------------------------------------- #
def delta(review_path: Path, baseline: Path, home: Path | None = None) -> dict[str, Any]:
    """The claim-set delta of a litreview's module — current vs a ``baseline``
    grounding_report.json — for the cheap-update delta-judge. ``baseline`` is just an older copy of
    the grounding report (the git part stays the caller's: ``git show <ref>:program/analysis/
    grounding_report.json > baseline.json``), so this is a pure function of two files.

    Returns ``{added, removed, drifted}`` — claim ids that appeared, disappeared, or whose drift
    signature (outcome/strength/quote/paraphrase/retraction) changed. A delta touching a claim the
    citing report relies on is what escalates to the fresh-context judge; an empty delta means
    nothing for a citing report to re-examine."""
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
    return {
        "added": sorted(cur_ids - base_ids),
        "removed": sorted(base_ids - cur_ids),
        "drifted": sorted(cid for cid in (cur_ids & base_ids)
                          if REPORT._claim_drift_sig(cur[cid]) != REPORT._claim_drift_sig(base[cid])),
    }


def render_delta(d: dict[str, Any]) -> str:
    rows = [("added", d["added"]), ("removed", d["removed"]), ("drifted", d["drifted"])]
    out = []
    for label, ids in rows:
        if ids:
            out.append(f"  {label}: " + ", ".join(REPORT._short_claim_id(c) for c in ids))
    if not out:
        return "no change (nothing for a citing report to re-examine)"
    head = "litreview delta — escalate to the delta-judge if any cited claim moved:"
    return head + "\n" + "\n".join(out)


# --------------------------------------------------------------------------- #
# scaffold — `sci new-litreview`
# --------------------------------------------------------------------------- #
def scaffold(home: Path, slug: str, *, title: str | None = None,
             scope: str = "program") -> dict[str, Any]:
    """Scaffold a new litreview under ``home`` — the highest-risk manual step removed (the claim
    module name + the committed protocol/screening artifacts). Lays out, **without overwriting**:

    * ``<scope>/litreviews/<slug>/review.md`` — a MINIMAL stub (front matter + a pointer to
      ``references/litreview.md``). The supporting/contradicting/equivocal/absent structure is
      deliberately NOT baked in (the canonical structure is being redesigned — see references);
    * ``<scope>/litreviews/<slug>/protocol.md`` — the PROSPERO pre-registration stub (front matter
      + the four required headings, bodies blank for the author to fill);
    * ``<scope>/litreviews/<slug>/screening.jsonl`` — an empty PRISMA screening log (seed it with
      ``--ingest-discover``, then screen each candidate by hand);
    * ``<scope>/litreviews/<slug>/prompt.md`` — a conclusion-free generation-brief stub;
    * ``<scope>/claims/test_litreview_<slug>.py`` — the **correctly-named** ``[lit:]`` claim module
      (slug hyphens → underscores).

    ``scope`` is ``program`` (program/, the usual case) or an experiment folder name. Returns
    ``{slug, scope, created: [rel…], skipped: [rel…], module}`` — pure file I/O, store-free."""
    home = Path(home).resolve()
    slug_mod = slug.replace("-", "_")
    scope_dir = home / scope
    lr_dir = scope_dir / "litreviews" / slug
    claims_dir = scope_dir / "claims"
    module = claims_dir / f"test_litreview_{slug_mod}.py"
    review = lr_dir / "review.md"
    protocol = lr_dir / "protocol.md"
    screening = lr_dir / "screening.jsonl"
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
            f"     open-questions section is mandatory. Pre-register the method in protocol.md and\n"
            f"     track every candidate in screening.jsonl BEFORE writing — every [lit:]-cited\n"
            f"     paper must be an `included` screening row. Ground load-bearing assertions as\n"
            f"     [lit:] claims in {module.name}. -->\n\n"
            f"## Question & scope\n\n## Gaps / open questions\n"),
        protocol: (
            f"---\nslug: {slug}\nas_of: \"\"          # YYYY-MM-DD — when the registered search was last run\n"
            f"sources: []        # e.g. [openalex, semantic-scholar, europepmc, pubmed, crossref, arxiv]\n"
            f"---\n\n"
            f"<!-- PROSPERO-style pre-registration: pin question / sources / queries / inclusion +\n"
            f"     exclusion criteria BEFORE screening, so scope can't be tuned to the answer. All\n"
            f"     four headings below need a non-empty body. -->\n\n"
            f"## Question & scope\n\n## Search queries\n\n## Inclusion criteria\n\n"
            f"## Exclusion criteria\n"),
        screening: "",
        prompt: (
            f"# Generation brief — litreview `{slug}`\n\n"
            f"A **conclusion-free** survey of the literature on this sub-question: map what the\n"
            f"field reports, who reports it, how strong each piece is, where it disagrees or is\n"
            f"silent. Draw NO program conclusion (no dose/target/lead call). See\n"
            f"references/litreview.md and references/report-authoring.md.\n\n"
            f"## Sub-question\n\n## Must cover\n\n## Search & screening\n"
            f"Pre-register the method in `protocol.md` first. Run `bib discover` per the\n"
            f"bibliographer literature-search protocol, then seed the PRISMA log:\n"
            f"`sci litreview <review.md> --ingest-discover <discover.json>`. Screen each candidate\n"
            f"to included|excluded(+reason) by hand in `screening.jsonl`.\n"),
        module: (
            f'"""[lit:] claim module for litreview `{slug}`.\n\n'
            f"Correctly named so the staleness pin and store card key off it (test_litreview_<slug>.py,\n"
            f"slug hyphens→underscores). Author each load-bearing assertion as a grounded [lit:]\n"
            f"claim per references/report.md. Run with --grounding-out to emit the grounding report\n"
            f'the audit reads.\n"""\n'
            f"from scientist.grounding import kind, strength, source  # noqa: F401\n\n\n"
            f"# @kind"
            f'("literature")\n'
            f"# @strength"
            f'("moderate")\n'
            f"# def test_example_finding():\n"
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
    PRISMA funnel, protocol status, and contested-status read."""
    out = REPORT.render_audit(result)
    proto = "present" if result.get("protocol_present") else "MISSING"
    f = result.get("funnel") or {}
    by_reason = f.get("excluded_by_reason") or {}
    reason_tail = ""
    if by_reason:
        reason_tail = " [" + ", ".join(f"{r}: {n}" for r, n in sorted(by_reason.items())) + "]"
    funnel_line = (f"  PRISMA funnel: {f.get('identified', 0)} identified → "
                   f"{f.get('included', 0)} included, {f.get('excluded', 0)} excluded"
                   + (f", {f.get('pending', 0)} pending" if f.get('pending') else "")
                   + reason_tail)
    ctrl = ("addressed" if result.get("contested_status_addressed")
            else "not evident in the prose")
    return (out + f"\n  protocol.md: {proto}\n" + funnel_line +
            f"\n  contested-status: {ctrl} (advisory — met by competing accounts OR an explicit "
            f"'no genuine controversy; the contested axis is X' finding; never satisfiable by a "
            f"heading alone)")
