"""The literature citation layer for the report engine — ``[lit:]`` and ``[litreview:]``.

The generic report engine (:mod:`reportkit.report`) natively knows ``[claim:]`` /
``[report:]`` / embeds. This module is scientist's *literature* citation layer, plugged into
the engine through the citation-resolver registry (:func:`reportkit.report.register_citation`):

* ``[lit:<id>]`` — grounds a third-party statement on a ``kind=literature`` /
  ``kind=bibliometric`` claim (a verbatim quote checked against a paper in the bibliographer
  library + a support review, or a stored OpenAlex metric), or on a pre-extracted
  **paper-claim** in the per-paper store;
* ``[litreview:<id>]`` — grounds a topic on a neutral PROSPERO/PRISMA literature survey
  (``kind=litreview``), with a protocol-keyed staleness pin.

Each scheme registers (at import) its regex, an audit resolver, render hooks (footnote text +
works-cited entries), an audit-output renderer, and — for ``[lit:]`` — opts into the
prose-quantity advisory pool. Importing this module is what *activates* those schemes; it is
imported on scientist's normal report paths (the :mod:`provenance.report` shim, which
:mod:`provenance.litreview` / :mod:`provenance.reviewtree` / ``sci`` all import), so the
resolvers are registered whenever scientist audits a report.

This is the seam the scientist/research split turns on: in a later phase this whole module
moves to a ``research`` skill that registers the same schemes, with no change to the engine.

Stdlib + PyYAML. The literature verdict helpers + labels were lifted verbatim from the old
``provenance.report`` (a test asserts behavior is unchanged)."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from reportkit.report import (
    GROUNDED_OUTCOMES,
    _AuditContext,
    _front_matter,
    _RenderContext,
    _rel_or_name,
    _report_title,
    _section_bodies,
    register_citation,
    report_scope,
    resolve_citation,
)

from . import paperclaims as _paperclaims

# Inline literature citation: [lit:<id>] — grounds a third-party statement on a *literature*
# claim (kind=literature) that verifies a verbatim quote against a paper in the bibliographer
# library and carries an agent support-review. Epistemically second-class to [claim:].
_LIT_RE = re.compile(r"\[lit:\s*([^\[\]]+?)\s*\]")
# Inline litreview citation: [litreview:<id>] — grounds a report on a *neutral literature survey*
# (kind=litreview). <id> is <exp-or-program>::<slug> (almost always program::<slug>) or a bare
# <slug>. NOT [report:] — a litreview has no conclusion to rest on; it points at the assessed
# evidence map and carries a protocol-keyed staleness boundary (see references/litreview.md).
_LITREVIEW_RE = re.compile(r"\[litreview:\s*([^\[\]]+?)\s*\]")


# --------------------------------------------------------------------------- #
# citation labels
# --------------------------------------------------------------------------- #
def _author_year(src: dict[str, Any]) -> str:
    """An author-year label for a literature source, parsed from its bibliographer citekey
    (``<lastname><year><word>`` → ``Lastname year``); falls back to citekey + recorded year."""
    ck = str(src.get("citekey") or "")
    m = re.match(r"^([a-z]+)(\d{4})", ck)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    yr = str(src.get("year") or "")
    return f"{ck} {yr}".strip() or "source"


def _short_authors(authors_text: str) -> str:
    """A compact ``First et al.`` lead from a stored ``authors_text`` ("Family, Given; …"):
    one name verbatim, two joined with ``&``, three-or-more as ``First et al.``. Empty when
    there are no names (the caller then falls back to the citekey-derived surname)."""
    names = [p.split(",")[0].strip() for p in authors_text.split(";") if p.strip()]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{names[0]} et al."


# Locator ladder → max eligible strength for a *machine-judged* literature source (mirrors
# scientist.grounding.source). A source's tier caps the claim's strength; the audit enforces the
# ceiling so a paragraph-spanning chunk locator can't be sold as a tier-1 "strong" quote.
_LIT_TIER_CEILING = {1: "strong", 2: "moderate", 3: "weak"}
_LIT_STRENGTH_RANK = {"weak": 1, "moderate": 2, "strong": 3}


def _machine_lit_sources(claim: dict[str, Any]) -> list[dict[str, Any]]:
    """The machine-judged sources of a literature claim — lit sources carrying a ``paraphrase``
    (i.e. authored with ``source(paraphrase=…)``). Empty for a legacy quote-only claim, which
    keeps the ``@reviewed`` path."""
    ev = claim.get("evidence") or {}
    return [s for s in (ev.get("lit_sources") or [])
            if isinstance(s, dict) and s.get("paraphrase")]


def _lit_strength_ceiling(machine_sources: list[dict[str, Any]]) -> str:
    """The claim's max eligible strength: the ceiling of its *weakest-located* machine source
    (highest tier number → lowest ceiling)."""
    worst = max((int(s.get("tier", 3)) for s in machine_sources), default=3)
    return _LIT_TIER_CEILING.get(worst, "weak")


def lit_verdict(claim: dict[str, Any]) -> tuple[str, str | None]:
    """Verdict for a ``[lit:<id>]`` citation. Literature grounding is two-layer: the tool check is
    the claim's pass/fail (the verbatim quote was present in the cited paper); the *support* is a
    judgment of whether the quote fairly backs the paraphrase. That support judgment is recorded
    one of two ways, and this function consumes both with the SAME downstream shape:

    * **machine-judged** (``source(paraphrase=…)``) — a re-runnable, cache-pinned entailment
      verdict the orchestrating agent records via ``res judge --record``. ``needs-judgment`` (not
      yet judged / paraphrase edited) and ``stale-judgment`` (quote / paraphrase / span drifted
      since judged) are the executable analogue of ``needs-review`` / ``stale-review``; an
      ``unsupported`` judgment blocks.
    * **legacy** (``@reviewed(support=…)``) — the hand-stamped human boolean, unchanged.

    Unlike a data claim, a *weak* (but supported) literature claim still backs its citation —
    single, suggestive, or secondary evidence is legitimately weak, not broken. Blocks only on:
    a failed quote check, a non-literature claim cited via ``[lit:]``, an un-judged/unsupported/
    stale source, or a strength that exceeds the locator ceiling. Returns ``(verdict, detail|None)``."""
    if str(claim.get("kind")) != "literature":
        return ("wrong-kind", "cited via [lit:] but is not a literature claim — use [claim:]")

    machine = _machine_lit_sources(claim)
    if machine:
        # MACHINE-JUDGED path. A cached `unsupported` verdict fails the claim's assert
        # (outcome != passed); distinguish that from a failed *quote* tripwire.
        if str(claim.get("outcome")) not in GROUNDED_OUTCOMES:
            if any(s.get("supported") is False for s in machine):
                return ("unsupported", "the support judge found the paraphrase NOT supported by "
                                       "the cited span — fix the paraphrase or re-source")
            return ("broken", f"the quote check did not pass (outcome={claim.get('outcome')}) — "
                              "the verbatim quote is not in the cited paper")
        if any(s.get("judge_status") == "stale" for s in machine):
            return ("stale-judgment", "the quote / paraphrase / span drifted since the verdict "
                                      "was cached — re-run `res judge` to re-judge and re-record")
        if any(s.get("judge_status") != "fresh" or "supported" not in s for s in machine):
            return ("needs-judgment", "no cached support verdict yet — run `res judge --list`, "
                                      "judge whether the span supports the paraphrase, and "
                                      "`res judge --record`")
        if any(not s.get("supported") for s in machine):
            return ("unsupported", "the support judge found the paraphrase NOT supported by the "
                                   "cited span")
        ceiling = _lit_strength_ceiling(machine)
        strength = str(claim.get("strength"))
        if _LIT_STRENGTH_RANK.get(strength, 0) > _LIT_STRENGTH_RANK.get(ceiling, 3):
            return ("over-strength", f"@strength={strength} exceeds the locator ceiling "
                                     f"'{ceiling}' (a tier-{max(int(s.get('tier', 3)) for s in machine)} "
                                     f"locator) — strengthen the locator (quote a sentence) or "
                                     f"lower @strength")
        return ("backed", None)

    # LEGACY path (hand-stamped @reviewed). Unchanged.
    if str(claim.get("outcome")) not in GROUNDED_OUTCOMES:
        return ("broken", f"the quote check did not pass (outcome={claim.get('outcome')}) — "
                          "the verbatim quote is not in the cited paper")
    reviewed = claim.get("reviewed")
    if not reviewed:
        return ("needs-review", "no agent support-review (@reviewed) yet — a human/agent must "
                                "confirm the quote supports the paraphrase before it backs a cite")
    if not reviewed.get("support", False):
        return ("unsupported", "agent review judged the source does NOT support the statement")
    return ("backed", None)


def lit_review_sha(claim: dict[str, Any]) -> str | None:
    """A combined sha over the claim's cited paper texts (each ``source()`` records a
    ``kind="paper"`` input pinned by its text sha). Stamp this in ``@reviewed(sha=…)``; the
    audit recomputes it and flags ``stale-review`` if a cited paper's library text has changed
    since the review — the literature analogue of input-drift, since the "input" is library
    content, not a repo file."""
    paps = sorted((str(i.get("path", "")), str(i.get("sha256", "")))
                  for i in (claim.get("inputs") or []) if i.get("kind") == "paper")
    if not paps:
        return None
    h = hashlib.sha256()
    for ck, sha in paps:
        h.update(f"{ck}:{sha}\n".encode())
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Bibliometric claims — a claim ABOUT the literature (e.g. "most-cited"), grounded on a stored
# OpenAlex metric via scientist.grounding.metric()/cited_by(), not a quote. The quote-in-paper
# verdict (lit_verdict) cannot represent it, so it gets its own verdict + staleness pin. A [lit:]
# citation dispatches here when the cited claim's kind is "bibliometric" (see the citation loop).
# --------------------------------------------------------------------------- #
def _metric_sources(claim: dict[str, Any]) -> list[dict[str, Any]]:
    """The recorded metric snapshots of a bibliometric claim (from ``metric()``/``cited_by()``)."""
    ev = claim.get("evidence") or {}
    return [s for s in (ev.get("metric_sources") or []) if isinstance(s, dict)]


def _bucket_metric(value: Any) -> str:
    """Bucket a metric to 2 significant figures so a count ticking +1 doesn't churn the review pin —
    the relation assert (the pytest) catches an actual flip; the pin only re-opens review when the
    data moved *materially*. Non-numeric values pin verbatim."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    if x == 0:
        return "0"
    return str(int(float(f"{x:.2g}")))


def metric_review_sha(claim: dict[str, Any]) -> str | None:
    """A combined sha over a bibliometric claim's metric snapshots — each
    ``(citekey, metric, bucketed value, as_of-month)``. Stamp in ``@reviewed(sha=…)``; the audit
    recomputes it and flags ``stale-review`` when a refreshed metric (or its ``as_of``) moves
    materially, so the *interpretation* is re-vetted. Bucketing gives tolerance to +1 noise
    (see :func:`_bucket_metric`); an exact flip of the asserted relation is caught by the pytest."""
    ms = _metric_sources(claim)
    if not ms:
        return None
    rows = sorted((str(s.get("citekey", "")), str(s.get("metric", "")),
                   _bucket_metric(s.get("value")), str(s.get("as_of") or "")[:7]) for s in ms)
    h = hashlib.sha256()
    for r in rows:
        h.update(("|".join(r) + "\n").encode())
    return h.hexdigest()


def paper_claim_verdict(pc: dict[str, Any]) -> tuple[str, str | None]:
    """Verdict for an ``[lit:<id>]`` that resolves to a pre-extracted **paper-claim** (Phase 2)
    rather than an internal literature claim. A paper-claim is ATTRIBUTED — pinned to what the
    paper says — so its audit is structural, not a re-run: it must be ``kind="attributed"`` and
    carry a non-empty ``evidence_sha`` (the integrity pin). The full quote-integrity re-check (the
    quote still located in the retained PDF) is ``res paper-claims verify`` — offline here, the
    audit only confirms the record exists and is well-formed enough to cite. Returns
    ``("attributed", None)`` when it backs the cite, else a blocking ``(verdict, detail)``."""
    if str(pc.get("kind")) != _paperclaims.KIND:
        return ("not-attributed", f"paper-claim is kind={pc.get('kind')!r}, not "
                                  f"'{_paperclaims.KIND}' — re-extract; never launder attribution")
    if not str(pc.get("evidence_sha") or "").strip():
        return ("no-evidence-sha", "paper-claim has no evidence_sha (the integrity pin) — "
                                   "re-run the extractor / `res paper-claims validate`")
    return ("attributed", None)


def bibliometric_verdict(claim: dict[str, Any]) -> tuple[str, str | None]:
    """Verdict for a ``[lit:]`` citation to a ``kind="bibliometric"`` claim. The pytest assert is
    the metric relation (a flip → ``outcome != passed`` → ``broken``); a recorded
    ``@reviewed(support=True)`` is what makes the *interpretation* (comparison set, metric choice)
    vetted — the arithmetic passing is necessary but not sufficient. Like a literature claim, a
    supported bibliometric claim backs its cite at any strength. Returns ``(verdict, detail|None)``."""
    if str(claim.get("kind")) != "bibliometric":
        return ("wrong-kind", "cited via [lit:] but is not a literature/bibliometric claim")
    if str(claim.get("outcome")) not in GROUNDED_OUTCOMES:
        return ("broken", f"the metric assertion did not pass (outcome={claim.get('outcome')}) — a "
                          "cited count moved and the asserted relation no longer holds; re-check")
    if not _metric_sources(claim):
        return ("no-metric", "kind=bibliometric but recorded no metric() read — assert via "
                             "cited_by()/metric() so the value + as_of are pinned and re-checkable")
    reviewed = claim.get("reviewed")
    if not reviewed:
        return ("needs-review", "no @reviewed yet — a human/agent must vet the comparison set and "
                                "metric choice (the assert proves the arithmetic, not the meaning)")
    if not reviewed.get("support", False):
        return ("unsupported", "agent review judged the bibliometric claim unsound (wrong "
                               "comparison set / metric / interpretation)")
    return ("backed", None)


def _asof_age_days(as_of: Any) -> int | None:
    """Whole days between ``as_of`` (``YYYY``/``YYYY-MM``/``YYYY-MM-DD``) and today, or ``None`` if
    unparseable. Advisory-only freshness signal — never affects GROUNDED/BROKEN."""
    import datetime as _dt

    s = str(as_of).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            d = _dt.datetime.strptime(s, fmt).date()
            return (_dt.date.today() - d).days
        except ValueError:
            continue
    return None


def _metric_asof_advisories(sources: list[dict[str, Any]], cite: str, line: int) -> list[dict]:
    """Non-blocking freshness nudges for a bibliometric citation: a snapshot with no ``as_of`` (can't
    tell how fresh) or one older than ~12 months (`bib enrich` + re-stamp). Never blocks GROUNDED."""
    out: list[dict] = []
    unknown = [str(s.get("citekey")) for s in sources if not s.get("as_of")]
    if unknown:
        out.append({"kind": "metric-asof-unknown", "line": line, "cite": cite,
                    "detail": f"bibliometric snapshot has no as_of for {', '.join(unknown)} — "
                              "`bib enrich` to record when the metric was fetched so freshness is checkable"})
    old = [f"{s.get('citekey')} ({s.get('as_of')})" for s in sources
           if s.get("as_of") and (_asof_age_days(s.get("as_of")) or 0) > 365]
    if old:
        out.append({"kind": "metric-asof-stale", "line": line, "cite": cite,
                    "detail": f"bibliometric data older than ~12 months ({'; '.join(old)}) — "
                              "re-`bib enrich` and re-stamp the review"})
    return out


# --------------------------------------------------------------------------- #
# [litreview:] path resolution + the PROSPERO/PRISMA protocol staleness pin
# --------------------------------------------------------------------------- #
def resolve_litreview_paths(cid: str, home: Path) -> list[Path]:
    """Resolve a ``[litreview:<id>]`` citation to review.md path(s). ``<id>`` is
    ``<exp-or-program>::<slug>`` (almost always ``program::<slug>``) or a bare ``<slug>``
    (searched tree-wide). Returns 0 (missing), 1 (resolved), or >1 (ambiguous) paths.
    Mirrors :func:`reportkit.report.resolve_report_paths` but over ``litreviews/<slug>/review.md``."""
    cid = cid.strip()
    if "::" in cid:
        scope_id, slug = cid.split("::", 1)
        if scope_id == "program":
            cand = home / "program" / "litreviews" / slug / "review.md"
            return [cand] if cand.is_file() else []
        hits = [d / "litreviews" / slug / "review.md" for d in sorted(home.glob(f"{scope_id}*"))]
        return [h for h in hits if h.is_file()]
    return sorted(home.glob(f"**/litreviews/{cid}/review.md"))


def litreview_module_prefix(review_path: Path, home: Path) -> str:
    """The claim-id prefix for a litreview's own claim module:
    ``<scope>::test_litreview_<slug>.py::`` — with the slug's hyphens mapped to underscores (a
    Python module name can't carry hyphens — slug ``it-biodist`` → ``test_litreview_it_biodist.py``).
    Every claim id starting with this belongs to the litreview; the convention is the single source
    of truth for the obligation set and the staleness pin."""
    sc = report_scope(review_path, home)
    scope_id = "program" if sc["scope"] == "program" else (sc["exp_id"] or "program")
    module = "test_litreview_" + str(sc["slug"]).replace("-", "_") + ".py"
    return f"{scope_id}::{module}::"


def litreview_module_path(review_path: Path, home: Path) -> Path:
    """The expected **on-disk** path of a litreview's claim module —
    ``<scope-dir>/claims/test_litreview_<slug>.py`` (slug hyphens → underscores), the file whose
    ``[lit:]`` claims belong to the litreview (:func:`litreview_module_prefix`).
    ``<scope-dir>`` is ``program/`` for a program litreview, else the experiment folder; it is
    derived from where the ``review.md`` lives (``…/<scope>/litreviews/<slug>/review.md``), so this
    is the single source of truth for the module-name convention :func:`scaffold` lays down."""
    rp = Path(review_path).resolve()
    sc = report_scope(rp, home)
    module = "test_litreview_" + str(sc["slug"]).replace("-", "_") + ".py"
    parts = rp.parts
    if "litreviews" in parts:                         # …/<scope>/litreviews/<slug>/review.md
        scope_dir = Path(*parts[:parts.index("litreviews")])
    elif sc["scope"] == "program":
        scope_dir = home / "program"
    else:
        scope_dir = rp.parent
    return scope_dir / "claims" / module


# PROSPERO/PRISMA protocol headings a litreview commits (protocol.md, beside review.md).
_PROTOCOL_HEADINGS = ("Question & scope", "Search queries",
                      "Inclusion criteria", "Exclusion criteria")


def litreview_protocol_path(review_path: Path) -> Path:
    """The expected on-disk path of a litreview's ``protocol.md`` — beside ``review.md``."""
    return Path(review_path).resolve().parent / "protocol.md"


def litreview_screening_path(review_path: Path) -> Path:
    """The expected on-disk path of a litreview's ``screening.jsonl`` — beside ``review.md``."""
    return Path(review_path).resolve().parent / "screening.jsonl"


def parse_protocol(protocol_path: Path) -> dict[str, Any]:
    """Parse a litreview's ``protocol.md`` into ``{present, front_matter, headings}`` —
    ``headings`` is ``{lower-cased-title: body}``. Store-free, PyYAML + stdlib. Validation
    (which fields are required + non-empty) lives in :mod:`provenance.litreview`."""
    p = Path(protocol_path)
    if not p.is_file():
        return {"present": False, "front_matter": {}, "headings": {}}
    text = p.read_text(encoding="utf-8")
    headings = {k.lower(): v for k, v in _section_bodies(text).items()}
    return {"present": True, "front_matter": _front_matter(text), "headings": headings}


def litreview_protocol_pin_sha(review_path: Path) -> str:
    """The staleness pin a citing report records for one ``[litreview:]`` edge: a sha over the
    cited litreview's pre-registered **search method** — its ``protocol.md`` *Search queries* body
    plus the front-matter ``as_of`` and ``sources``. It changes iff the registered search itself
    changes (new query, refreshed snapshot, an added/dropped source) — the only events that can
    invalidate the report's claim to rest on this survey's coverage. Edits elsewhere in the review
    (a reworded paragraph, a new non-pivotal claim) leave it untouched, so a litreview can grow
    without a BROKEN cascade. ``sci`` never re-runs the search (it stays offline); a re-discover
    re-enters via ``--ingest-discover``. See references/litreview.md → *Staleness*."""
    proto = parse_protocol(litreview_protocol_path(review_path))
    fm = proto["front_matter"]
    sources = fm.get("sources")
    payload = {
        "queries": " ".join(str(proto["headings"].get("search queries", "")).split()),
        "as_of": str(fm.get("as_of") or ""),
        "sources": sorted(str(s) for s in sources if s) if isinstance(sources, list)
                   else str(sources or ""),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _claim_drift_sig(claim: dict[str, Any]) -> str:
    """A stable signature of the drift-relevant facts of a literature claim — its ``outcome`` and
    ``strength`` plus, per source, the citekey + the quoted/paraphrased span + the retraction flag.
    Changes exactly when a cited claim *drifts* in a way a citing report must re-examine: a strength
    re-grade, a paraphrase/quote edit, or a newly-retracted source."""
    ev = claim.get("evidence") or {}
    srcs = ev.get("lit_sources") if isinstance(ev, dict) else None
    src_sigs = sorted(
        (str(s.get("citekey") or ""), str(s.get("quote") or s.get("paraphrase") or ""),
         bool((s.get("credibility") or {}).get("is_retracted")))
        for s in (srcs or []) if isinstance(s, dict))
    return json.dumps([str(claim.get("outcome")), str(claim.get("strength")), src_sigs],
                      sort_keys=True)


def litreview_pins(text: str) -> dict[str, str]:
    """The ``litreview_pins`` mapping (``{litreview-id -> recorded pin sha}``) from a report's YAML
    front matter, or ``{}`` if absent/malformed. The report records the pin it last re-examined the
    litreview against; the audit recomputes the current pin and flags ``stale-litreview`` on drift.

    **Pin contract** (the two facts the pilot had to reverse-engineer): the recorded value is a
    **12-char prefix** of the full sha — the audit matches it with ``cur_pin.startswith(recorded)``,
    NOT equality, so the surfaced 12-char ``pin`` pastes straight in. The pin is over the cited
    litreview's **protocol** (search queries + ``as_of`` + sources — see
    :func:`litreview_protocol_pin_sha`); an unrecorded pin surfaces as the ``pin_unrecorded`` nudge
    (or via ``--write-pins``) whenever the litreview resolves."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        import yaml
        data = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}
    pins = data.get("litreview_pins") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in pins.items()} if isinstance(pins, dict) else {}


def write_litreview_pins(report_path: Path, surfaced: dict[str, str]) -> dict[str, str]:
    """Merge ``surfaced`` (``{litreview-id -> 12-char pin}``) into a report's ``litreview_pins``
    front-matter block and write it back — the mechanized form of the "copy the surfaced pin into
    ``litreview_pins``" paste step (``sci report --write-pins``). Existing pins are kept (surfaced
    values win on conflict); a report with no front matter gets one. Returns the merged mapping.

    Surgical, not a full YAML round-trip: the existing ``litreview_pins:`` mapping block is replaced
    in place and other front-matter keys are left byte-for-byte untouched, so the rest of the
    report's front matter is never reformatted."""
    rp = Path(report_path)
    text = rp.read_text(encoding="utf-8")
    merged = {**litreview_pins(text), **surfaced}

    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    fm_body, rest = (m.group(1), text[m.end():]) if m else ("", text)

    # Drop any existing litreview_pins: key + its indented children from the front-matter body.
    cleaned: list[str] = []
    skipping = False
    for line in fm_body.splitlines():
        if re.match(r"^litreview_pins\s*:", line):
            skipping = True
            continue
        if skipping:
            if line.strip() and re.match(r"^\s+\S", line):   # an indented child of the block
                continue
            skipping = False
        cleaned.append(line)

    block = ["litreview_pins:"] + [f'  {k}: "{merged[k]}"' for k in sorted(merged)]
    kept = [ln for ln in cleaned if ln.strip()]
    new_fm = "\n".join(kept + block)
    rp.write_text(f"---\n{new_fm}\n---\n{rest}", encoding="utf-8")
    return merged


# --------------------------------------------------------------------------- #
# audit resolvers (the registry ``resolve`` hooks) — each takes the scheme's parsed citations +
# the shared reportkit ``_AuditContext`` and returns ``(records, findings, advisories)``.
# --------------------------------------------------------------------------- #
def _resolve_lit_citations(
        cites: list[dict[str, Any]], ctx: _AuditContext
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve ``[lit:<id>]`` citations against internal literature/bibliometric claims and the
    pre-extracted paper-claims store (grounding on a paper in the bibliographer library)."""
    claim_index = ctx.claim_index
    # Pre-extracted external claims (Phase 2): an [lit:<id>] that names no internal claim resolves
    # against the per-paper paper-claims/*.jsonl store. Loaded once per audit, in memory; no DB.
    paper_claim_index = _paperclaims.load_paper_claims(ctx.home)
    findings: list[dict[str, Any]] = []
    lit_cites: list[dict[str, Any]] = []
    metric_advisories: list[dict[str, Any]] = []   # bibliometric as_of freshness nudges (non-blocking)
    for lc in cites:
        cid, line = lc["id"], lc["line"]
        rec = {"id": cid, "line": line}
        cands = resolve_citation(cid, claim_index)
        if not cands and cid in paper_claim_index:
            # Pre-extracted external claim (Phase 2): resolves to a paper-claim in the store, not
            # an internal literature claim. ATTRIBUTED — kept visually distinct from a grounded
            # cite (rec["attributed"] flags the render path).
            pc = paper_claim_index[cid]
            rec["attributed"] = True
            rec["claim_id"] = cid
            rec["citekey"] = pc.get("citekey")
            rec["strength"] = pc.get("strength")
            rec["paraphrase"] = pc.get("paraphrase")
            rec["statement"] = pc.get("paraphrase")
            verdict, detail = paper_claim_verdict(pc)
            rec["verdict"] = verdict
            if verdict != "attributed":
                findings.append({"kind": f"{verdict}-lit", "line": line, "cite": cid,
                                 "detail": detail})
            lit_cites.append(rec)
            continue
        if not cands:
            rec["verdict"] = "missing"
            findings.append({"kind": "missing-lit", "line": line, "cite": cid,
                             "detail": "no literature claim or paper-claim has this id; write the "
                                       "[lit:] claim or extract the paper "
                                       "(`res paper-claims scaffold <citekey>`)"})
        elif len(cands) > 1:
            rec["verdict"] = "ambiguous"
            rec["candidates"] = cands
            findings.append({"kind": "ambiguous-lit", "line": line, "cite": cid,
                             "detail": f"matches {len(cands)} claims — qualify it: {cands}"})
        else:
            claim = claim_index[cands[0]]
            rec["claim_id"] = cands[0]
            rec["strength"] = claim.get("strength")
            rec["statement"] = claim.get("statement")
            rec["reviewed"] = claim.get("reviewed")
            if str(claim.get("kind")) == "bibliometric":
                # A claim ABOUT the literature (most-cited, …) grounded on a stored OpenAlex metric,
                # not a quote — its own verdict + staleness pin (over the metric values + as_of).
                rec["metric_sources"] = (claim.get("evidence") or {}).get("metric_sources", [])
                verdict, detail = bibliometric_verdict(claim)
                if verdict == "backed":
                    cur = metric_review_sha(claim)
                    stamped = (claim.get("reviewed") or {}).get("sha")
                    rec["review_sha"] = cur
                    if stamped and cur and not str(cur).startswith(str(stamped)):
                        verdict, detail = ("stale-review",
                                           "the bibliometric values/as_of moved materially since the "
                                           f"review (stamp={str(stamped)[:12]}, now={cur[:12]}) — "
                                           "re-vet and re-stamp @reviewed(sha=…)")
                    elif not stamped:
                        rec["review_unpinned"] = True   # advisory, non-blocking
                    metric_advisories.extend(
                        _metric_asof_advisories(rec["metric_sources"], cid, line))
                rec["verdict"] = verdict
                if verdict != "backed":
                    findings.append({"kind": f"{verdict}-lit", "line": line, "cite": cands[0],
                                     "detail": detail})
                lit_cites.append(rec)
                continue
            verdict, detail = lit_verdict(claim)
            rec["sources"] = (claim.get("evidence") or {}).get("lit_sources", [])
            # re-validation (LEGACY @reviewed path only): if the review was pinned
            # (@reviewed(sha=…)) and a cited paper's text has since changed, the review is stale →
            # re-read and re-stamp (blocking). Machine-judged claims pin staleness via the verdict
            # cache key (judge_status=stale → stale-judgment), so skip this for them.
            if verdict == "backed" and not _machine_lit_sources(claim):
                cur = lit_review_sha(claim)
                stamped = (claim.get("reviewed") or {}).get("sha")
                rec["review_sha"] = cur
                if stamped and cur and not str(cur).startswith(str(stamped)):
                    verdict, detail = ("stale-review",
                                       "a cited paper's library text changed since the review "
                                       f"(stamp={str(stamped)[:12]}, now={cur[:12]}) — re-read and re-stamp")
                elif not stamped:
                    rec["review_unpinned"] = True   # advisory, non-blocking
            rec["verdict"] = verdict
            if verdict != "backed":
                findings.append({"kind": f"{verdict}-lit", "line": line, "cite": cands[0],
                                 "detail": detail})
        lit_cites.append(rec)
    return lit_cites, findings, metric_advisories


def _resolve_litreview_citations(
        cites: list[dict[str, Any]], ctx: _AuditContext
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve ``[litreview:<id>]`` citations + the protocol-keyed staleness pin.

    A ``[litreview:<id>]`` grounds a topic on a neutral survey (kind=litreview). The integrity it
    carries is the survey's own (its committed PROSPERO/PRISMA protocol + screening, audited by
    ``res litreview``); the consuming report's only mechanical obligation is to stay PINNED to the
    survey's registered search method, so a re-sweep that changed the queries/snapshot/sources
    forces a re-examination here. There is NO omissions gate — coverage is the survey-side
    completeness critic's job, against the screening log (see references/litreview.md)."""
    home = ctx.home
    pins = litreview_pins(ctx.text)
    findings: list[dict[str, Any]] = []
    litreview_cites: list[dict[str, Any]] = []
    for lrc in cites:
        cid, line = lrc["id"], lrc["line"]
        rec = {"id": cid, "line": line}
        paths = resolve_litreview_paths(cid, home)
        if not paths:
            rec["verdict"] = "missing"
            findings.append({"kind": "missing-litreview", "line": line, "cite": cid,
                             "detail": "no litreview with this id; write the litreview first"})
        elif len(paths) > 1:
            rec["verdict"] = "ambiguous"
            findings.append({"kind": "ambiguous-litreview", "line": line, "cite": cid,
                             "detail": f"matches {len(paths)} litreviews — qualify with <scope>::<slug>"})
        else:
            target = paths[0].resolve()
            rec["litreview"] = _rel_or_name(target, home)
            rec["verdict"] = "backed"
            # staleness pin: did the survey's registered search method (protocol queries + as_of +
            # sources) change since this report last re-examined it?
            cur_pin = litreview_protocol_pin_sha(target)
            rec["pin"] = cur_pin[:12]
            recorded = pins.get(cid)
            if recorded:
                rec["recorded_pin"] = str(recorded)
                if not cur_pin.startswith(str(recorded)):
                    rec["verdict"] = "stale-litreview"
                    findings.append({
                        "kind": "stale-litreview", "line": line, "cite": cid,
                        "detail": "the litreview's search protocol (queries / as_of / sources) "
                                  f"changed since pinned (pin={str(recorded)[:12]}, "
                                  f"now={cur_pin[:12]}) — re-examine the survey, then re-pin "
                                  "litreview_pins in the front matter"})
            else:
                rec["pin_unrecorded"] = True         # advisory nudge (non-blocking)
        litreview_cites.append(rec)
    return litreview_cites, findings, []


# --------------------------------------------------------------------------- #
# render hooks (footnote text + works-cited entries + audit-output lines)
# --------------------------------------------------------------------------- #
def _paper_index(rctx: _RenderContext) -> dict[str, Any]:
    """The per-paper paper-claims store for this render, memoized on the render context's scratch
    so it is read once rather than per citation."""
    pi = rctx.scratch.get("paper_claim_index")
    if pi is None:
        pi = _paperclaims.load_paper_claims(rctx.home)
        rctx.scratch["paper_claim_index"] = pi
    return pi


def _lit_note_text(cid: str, rctx: _RenderContext) -> str:
    # Parallel to a data-claim endnote: the claim's statement reads as the note, followed by
    # the supporting papers (author-year) as a subdued citation. No "Literature" label (the
    # author-years make the source obvious) and no strength (low signal in prose) — the
    # quote-pinning and the evidential assessment live in the spec and the audit, not here.
    claim_index = rctx.claim_index
    cands = resolve_citation(cid, claim_index)
    if len(cands) != 1:
        # A pre-extracted external claim (Phase 2): render it ATTRIBUTED — "Author year report:
        # <paraphrase>" — visually distinct from a grounded "we measured" note, so a paper's
        # assertion is never typeset as a program fact.
        pc = _paperclaims.resolve_paper_claim(cid, _paper_index(rctx))
        if pc is not None:
            para = (pc.get("paraphrase") or "").strip().replace("\n", " ")
            ay = _author_year(pc)
            return f"{ay} report: {para} `{cid}`"
        return f"literature `{cid}` ({'unresolved' if not cands else 'ambiguous'})"
    c = claim_index[cands[0]]
    stmt = (c.get("statement") or "").strip().replace("\n", " ")
    seen, ays = set(), []
    for s in (c.get("evidence") or {}).get("lit_sources", []):
        ck = s.get("citekey")            # one author-year per paper, first-seen order
        if ck in seen:
            continue
        seen.add(ck)
        ays.append(_author_year(s))
    return f"{stmt} ({'; '.join(ays)})" if ays else stmt


def _litreview_note_text(cid: str, rctx: _RenderContext) -> str:
    # A litreview citation footnotes the survey it rests on (title + id) — the reader sees
    # which neutral evidence map the argument draws from. No conclusion (a litreview has none).
    paths = resolve_litreview_paths(cid, rctx.home)
    if len(paths) == 1:
        title = _report_title(paths[0].read_text(encoding="utf-8")) or cid
        return f"Literature review: *{title}* — `{cid}`"
    return f"litreview `{cid}` ({'unresolved' if not paths else 'ambiguous'})"


def _bib_entry(s: dict[str, Any]) -> tuple[tuple, str]:
    # (sort-key, rendered entry) for one cited paper: "Authors (Year). *Title*. Venue. <doi>".
    # All fields are read from what the source snapshotted at grounding time, so the
    # bibliography needs no live library. Authors/year fall back to the citekey-derived
    # surname+year (`<lastname><year>…`) for a source that predates the authors/venue snapshot
    # (re-running the claims regenerates it with the full fields). Sorted author, year, title, ck.
    ck = str(s.get("citekey") or "")
    year = (s.get("year") or "").strip()
    authors = _short_authors((s.get("authors_text") or "").strip())
    if not authors or not year:
        m = re.match(r"^([a-z]+)(\d{4})", ck)
        if m:
            authors = authors or m.group(1).capitalize()
            year = year or m.group(2)
    authors = authors or ck or "Anonymous"
    title = (s.get("title") or "").strip()
    venue = (s.get("venue") or "").strip()
    doi = (s.get("doi") or "").strip()
    if not doi:                          # a paper-claim carries its id as `paper: "doi:…"`
        paper_id = str(s.get("paper") or "").strip()
        if paper_id.lower().startswith("doi:"):
            doi = paper_id[4:].strip()
    bits = [f"{authors} ({year})." if year else f"{authors}."]
    if title:
        bits.append(f"*{title}*.")
    if venue:
        bits.append(f"{venue}.")
    if doi:
        bits.append(f"<{doi if doi.startswith('http') else 'https://doi.org/' + doi}>")
    return ((authors.lower(), year, title.lower(), ck), " ".join(bits))


def _lit_bib_entries(cids: list[str], rctx: _RenderContext) -> list[tuple[tuple, str]]:
    """Works-cited entries for the ``[lit:]``-cited papers — one per distinct paper (by citekey)
    across every cited literature claim / paper-claim. The engine handles the defer-to-author's-own
    -References-heading rule and the final sort; this just supplies the deduped entries."""
    claim_index = rctx.claim_index
    paper_claim_index = _paper_index(rctx)
    seen: set[str] = set()
    entries: list[tuple[tuple, str]] = []
    for cid in cids:
        cands = resolve_citation(cid, claim_index)
        if len(cands) != 1:
            # A pre-extracted external claim contributes its own paper to the works-cited list
            # (one entry per citekey; fields fall back to the citekey-derived surname+year).
            pc = _paperclaims.resolve_paper_claim(cid, paper_claim_index)
            if pc is not None:
                ck = str(pc.get("citekey") or "")
                if ck and ck not in seen:
                    seen.add(ck)
                    entries.append(_bib_entry(pc))
            continue
        for s in (claim_index[cands[0]].get("evidence") or {}).get("lit_sources", []):
            ck = str(s.get("citekey") or "")
            if not ck or ck in seen:
                continue
            seen.add(ck)
            entries.append(_bib_entry(s))
    return entries


# The audit-output mark per [lit:] verdict (the literature analogue of reportkit's _CITE_MARK).
_LIT_MARK = {"backed": "✅ backed", "attributed": "📄 attributed",
             "needs-review": "❌ needs-review",
             "needs-judgment": "❌ needs-judgment (run `res judge`)",
             "stale-judgment": "❌ stale-judgment (re-run `res judge`)",
             "over-strength": "❌ over-strength (exceeds locator ceiling)",
             "unsupported": "❌ unsupported", "broken": "❌ broken (quote absent)",
             "wrong-kind": "❌ wrong-kind", "missing": "❌ missing", "ambiguous": "❌ ambiguous",
             "stale-review": "❌ stale-review (source changed since review)",
             "no-metric": "❌ no-metric (no cited_by()/metric() read recorded)"}


def _lit_render_lines(result: dict[str, Any]) -> list[str]:
    """The ``render_audit`` lines for the ``[lit:]`` citations (+ the literature tally line)."""
    lits = result.get("lit_cites", [])
    lines: list[str] = []
    for lc in lits:
        mark = _LIT_MARK.get(lc["verdict"], lc["verdict"])
        tail = ""
        if lc["verdict"] == "attributed":
            # A pre-extracted external claim — "the paper reports", never "we measured".
            tail = f"  (attributed → {lc.get('citekey')}, {lc.get('strength')})"
        elif lc["verdict"] == "backed":
            srcs = lc.get("sources") or lc.get("metric_sources") or []
            label = "metric" if lc.get("metric_sources") else "source"
            nsrc = len(srcs)
            tail = f"  ({lc.get('strength')}, {nsrc} {label}{'s' if nsrc != 1 else ''})"
            if lc.get("review_unpinned"):
                tail += f"  [review unpinned — stamp @reviewed(sha=\"{(lc.get('review_sha') or '')[:12]}\")]"
        lines.append(f"  [lit L{lc['line']}] {lc['id']}: {mark}{tail}")
    if lits:
        # A separate literature line so the green badge never launders attribution as
        # data-grounding: show the tier spread across all [lit:] citations.
        tally = Counter(
            (lc.get("strength") if lc["verdict"] in ("backed", "attributed") else lc["verdict"])
            for lc in lits)
        spread = ", ".join(f"{n} {k}" for k, n in tally.items())
        lines.append(f"  literature: {len(lits)} cited — {spread}")
    return lines


def _litreview_render_lines(result: dict[str, Any]) -> list[str]:
    """The ``render_audit`` lines for the ``[litreview:]`` citations."""
    lines: list[str] = []
    for lr in result.get("litreview_cites", []):
        if lr["verdict"] == "backed":
            mark = "✅ backed"
            if lr.get("pin_unrecorded"):
                mark += f"  [unpinned — record litreview_pins: {{{lr['id']}: \"{lr.get('pin','')}\"}}]"
        elif lr["verdict"] == "stale-litreview":
            mark = "❌ stale-litreview (search protocol drifted — re-examine the survey + re-pin)"
        else:
            mark = f"❌ {lr['verdict']}"
        tail = f"  → {lr.get('litreview')}" if lr.get("litreview") else ""
        lines.append(f"  [litreview L{lr['line']}] {lr['id']}: {mark}{tail}")
    return lines


# --------------------------------------------------------------------------- #
# registration — activates the schemes whenever this module is imported. Registration order is
# the audit dispatch order, the render footnote-family order, and the audit-output order (lit
# before litreview), matching the pre-split behavior. Idempotent by scheme name.
# --------------------------------------------------------------------------- #
register_citation("lit", regex=_LIT_RE, parse_key="lit_cites",
                  resolve=_resolve_lit_citations, note_text=_lit_note_text,
                  bib_entries=_lit_bib_entries, render_lines=_lit_render_lines,
                  quantity_cites=True)
register_citation("litreview", regex=_LITREVIEW_RE, parse_key="litreview_cites",
                  resolve=_resolve_litreview_citations, note_text=_litreview_note_text,
                  render_lines=_litreview_render_lines)
