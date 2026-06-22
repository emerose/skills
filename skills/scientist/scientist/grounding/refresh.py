"""scientist.grounding.refresh — the literature-verdict worklist + record step (``sci judge``).

**No model lives here — or anywhere in ``sci``.** The orchestrating agent is already an LLM that
read the paper; the entailment verdict is produced by that agent (ideally a *fresh-context judge
subagent* it spawns, so the authoring context never grades its own paraphrase). This module does
only the two deterministic halves of the loop:

  * :func:`worklist` — surface the literature sources whose support verdict is **missing or stale**,
    each with the ``span_text`` the judge must read and the ``paraphrase`` it must weigh.
    (``sci judge --list``.) This is what the judge subagent reads.
  * :func:`record_verdicts` — ingest the caller-supplied verdicts ``{citekey, paraphrase,
    supported, rationale}`` and write them into the verdict cache
    (:mod:`scientist.grounding.judgments`), pinning each with an ``evidence_sha`` the tool
    **recomputes itself** from the report's stored span — so a caller cannot record a verdict
    against a stale or wrong span. (``sci judge --record``.)

Determinism discipline (unchanged): the verdict cache is pure stdlib; the pytest path
(``source()``) and the audit (``provenance.report.lit_verdict``) only READ it. This module only
WRITES it. There is no model client to import.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..provenance import claims_of, load_report
from .judgments import (JudgmentCache, JUDGMENT_CACHE_NAME, DEFAULT_JUDGE_ID, evidence_sha)
from grounding import fold_match


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _machine_sources(claim: dict[str, Any]) -> list[dict[str, Any]]:
    """The machine-judged sources of one claim: lit sources carrying a ``paraphrase``."""
    if str(claim.get("kind")) != "literature":
        return []
    ev = claim.get("evidence") or {}
    return [s for s in (ev.get("lit_sources") or [])
            if isinstance(s, dict) and s.get("paraphrase")]


def _span_for(src: dict[str, Any], *, library_resolver: Callable[[dict], str] | None) -> str | None:
    """The text span the judge must read for one source. Tier 1 (quote) and tier 2 (chunk) carry
    the span in the report, so the worklist surfaces it directly. Tier 3 (whole-doc) does not —
    it is re-resolved from the library via ``library_resolver`` (costly), or left unavailable."""
    span = src.get("span")
    if span:
        return span
    if int(src.get("tier", 3)) >= 3 and library_resolver is not None:
        return library_resolver(src)
    return None


def _iter_machine_sources(report_path: Path | str):
    """Yield ``(claim, src)`` for every machine-judged literature source in a grounding report.
    Returns ``(error_dict, None)`` as a single yield if the report is unreadable."""
    rp = Path(report_path)
    try:
        data = load_report(rp)
    except (OSError, ValueError) as exc:
        yield ({"_error": f"unreadable grounding report: {exc}"}, None)
        return
    for claim in (claims_of(data) or []):
        if not isinstance(claim, dict):
            continue
        for src in _machine_sources(claim):
            yield (claim, src)


def _cache_path_for(report_path: Path | str, cache_path: Path | str | None) -> Path:
    rp = Path(report_path)
    return Path(cache_path) if cache_path is not None else rp.parent / JUDGMENT_CACHE_NAME


def worklist(report_path: Path | str, cache_path: Path | str | None = None, *,
             library_resolver: Callable[[dict], str] | None = None,
             force: bool = False) -> dict[str, Any]:
    """Surface the literature sources whose support verdict is missing or stale (``sci judge
    --list``).

    For each machine-judged source whose cache entry is *miss* or *stale* (or every one, when
    ``force``), emit ``{claim_id, citekey, tier, span_text, paraphrase, evidence_sha, status}``.
    ``span_text`` is the verbatim quote (tier 1) or the resolved chunk text (tier 2); tier-3
    whole-doc spans are not carried in the report and come back empty unless a ``library_resolver``
    is supplied. Returns ``{report, cache, items, missing, stale, fresh}`` (``items`` is the
    worklist a judge subagent reads to decide *does span_text fairly support paraphrase?*)."""
    cp = _cache_path_for(report_path, cache_path)
    cache = JudgmentCache.load(cp)
    items: list[dict[str, Any]] = []
    missing = stale = fresh = 0

    for claim, src in _iter_machine_sources(report_path):
        if src is None:                      # unreadable report
            return {"report": str(Path(report_path)), "error": claim["_error"], "items": []}
        citekey = str(src.get("citekey") or "")
        paraphrase = str(src.get("paraphrase") or "")
        span = _span_for(src, library_resolver=library_resolver)
        esha = (evidence_sha(span) if span else src.get("evidence_sha"))
        if not esha:
            continue
        status, _ = cache.lookup(citekey, esha, paraphrase)
        if status == "fresh" and not force:
            fresh += 1
            continue
        if status == "stale":
            stale += 1
        else:
            missing += 1
        items.append({
            "claim_id": str(claim.get("id") or ""),
            "citekey": citekey, "tier": int(src.get("tier", 3)),
            "span_text": span or "", "paraphrase": paraphrase,
            "evidence_sha": esha, "status": status,
            **({"note": "tier-3 whole-doc span not carried in the report — resolve it from the "
                        "library before judging"} if not span else {}),
        })

    return {"report": str(Path(report_path)), "cache": str(cp), "items": items,
            "missing": missing, "stale": stale, "fresh": fresh}


def record_verdicts(report_path: Path | str, records: list[dict[str, Any]],
                    cache_path: Path | str | None = None, *,
                    judge_id: str | None = None,
                    library_resolver: Callable[[dict], str] | None = None) -> dict[str, Any]:
    """Ingest caller-supplied verdicts and write them into the verdict cache (``sci judge
    --record``).

    Each record is ``{citekey, paraphrase, supported, rationale}`` (plus an optional
    ``evidence_sha`` echoed back from the worklist). It is matched to a machine source in the
    report by ``(citekey, paraphrase)``; the pin (``evidence_sha``) is **recomputed by the tool**
    from that source's current stored span — never taken from the caller — so a verdict can only
    ever attach to the exact span the report carries now. A record is rejected when its
    ``(citekey, paraphrase)`` no longer resolves to a source, or when it echoes an ``evidence_sha``
    that no longer matches the current span (the worklist span the caller judged went stale).
    ``judge_id`` (default :data:`scientist.grounding.judgments.DEFAULT_JUDGE_ID`) is stamped as
    metadata.

    Returns ``{report, cache, recorded, rejected, details}``."""
    cp = _cache_path_for(report_path, cache_path)
    jid = judge_id or DEFAULT_JUDGE_ID

    # Index the report's machine sources by (citekey, paraphrase) → recomputed pin + tier.
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for claim, src in _iter_machine_sources(report_path):
        if src is None:
            return {"report": str(Path(report_path)), "error": claim["_error"],
                    "recorded": 0, "rejected": len(records), "details": []}
        citekey = str(src.get("citekey") or "")
        paraphrase = str(src.get("paraphrase") or "")
        span = _span_for(src, library_resolver=library_resolver)
        esha = (evidence_sha(span) if span else src.get("evidence_sha"))
        if not esha:
            continue
        index[(citekey, paraphrase)] = {"evidence_sha": esha, "tier": int(src.get("tier", 3))}

    cache = JudgmentCache.load(cp)
    recorded = rejected = 0
    details: list[dict[str, Any]] = []
    ts = _now_iso()

    for rec in records:
        if not isinstance(rec, dict):
            rejected += 1
            details.append({"status": "rejected", "reason": f"not an object: {rec!r}"})
            continue
        citekey = str(rec.get("citekey") or "")
        paraphrase = str(rec.get("paraphrase") or "")
        if "supported" not in rec:
            rejected += 1
            details.append({"citekey": citekey, "status": "rejected",
                            "reason": "record has no `supported` boolean"})
            continue
        match = index.get((citekey, paraphrase))
        if match is None:
            rejected += 1
            details.append({"citekey": citekey, "status": "rejected",
                            "reason": "no machine source in the report for this "
                                      "(citekey, paraphrase) — span/paraphrase changed or "
                                      "the claim is gone; re-list and re-judge"})
            continue
        echoed = rec.get("evidence_sha")
        if echoed and str(echoed) != match["evidence_sha"]:
            rejected += 1
            details.append({"citekey": citekey, "status": "rejected",
                            "reason": "evidence_sha echoed from the worklist no longer matches the "
                                      "report's current span — the span went stale; re-list and "
                                      "re-judge"})
            continue
        cache.put(citekey=citekey, evidence_sha_=match["evidence_sha"], paraphrase=paraphrase,
                  judge_id=jid, supported=bool(rec.get("supported")),
                  rationale=str(rec.get("rationale") or ""), timestamp=ts,
                  tier=match["tier"])
        recorded += 1
        details.append({"citekey": citekey, "status": "recorded",
                        "supported": bool(rec.get("supported"))})

    if recorded:
        cache.save(cp)
    return {"report": str(Path(report_path)), "cache": str(cp), "judge_id": jid,
            "recorded": recorded, "rejected": rejected, "details": details}


# --------------------------------------------------------------------------- #
# Divergence lint — cross-module hygiene for the residual the fold-key fix leaves.
# --------------------------------------------------------------------------- #
# Folding the cache key (sha of fold_match(span)) collapses markdown/whitespace/dash
# variants of the SAME sentence to one shared verdict. The residual it can't collapse:
# two GENUINELY different sentences from the same paper, each backing the same paraphrase
# in different modules — they fold differently, so they stay distinct cache identities
# (correct, by design: a different span is a different question). That's usually an
# authoring slip — the same fact should cite one canonical quote everywhere — so this lint
# surfaces it. It is advisory: a warning, never a failure (the verdicts are still each
# valid for their own span).
def divergence_lint(report_paths) -> list[dict[str, Any]]:
    """Scan grounding reports for a ``(citekey, paraphrase)`` cited with quotes that fold to
    DIFFERENT spans across modules, and return one warning per such pair.

    ``report_paths`` is an iterable of grounding_report.json paths (the program's literature
    modules). For every machine-judged source, group its (folded) span by ``(citekey,
    paraphrase)``; a pair backed by more than one distinct folded span is a divergence —
    the same claimed reading of the same paper is grounded on different sentences, which the
    folded cache key keeps as separate verdicts. Reconcile to ONE canonical quote.

    Each warning: ``{citekey, paraphrase, spans: [span_text…], where: [claim_id…]}`` (the
    spans + the claims that carry them, so the author can find and unify them)."""
    # (citekey, paraphrase) -> {folded_span -> {"span_text": raw, "where": {claim_id…}}}
    seen: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for rp in report_paths:
        for claim, src in _iter_machine_sources(rp):
            if src is None:
                continue                       # unreadable report → skip (check reports it elsewhere)
            span = src.get("span")
            if not span:
                continue                       # tier-3 whole-doc spans aren't carried; nothing to compare
            citekey = str(src.get("citekey") or "")
            paraphrase = str(src.get("paraphrase") or "")
            folded = fold_match(span)
            bucket = seen.setdefault((citekey, paraphrase), {})
            slot = bucket.setdefault(folded, {"span_text": span, "where": set()})
            slot["where"].add(str(claim.get("id") or ""))
    out: list[dict[str, Any]] = []
    for (citekey, paraphrase), by_span in sorted(seen.items()):
        if len(by_span) < 2:
            continue                           # one canonical span → no divergence
        spans = sorted(by_span.values(), key=lambda s: s["span_text"])
        where: set[str] = set()
        for s in spans:
            where |= s["where"]
        out.append({
            "citekey": citekey, "paraphrase": paraphrase,
            "spans": [s["span_text"] for s in spans],
            "where": sorted(where),
        })
    return out


def render_divergence(warnings: list[dict[str, Any]]) -> str:
    """Human-readable summary of a :func:`divergence_lint` run (empty → a clean line)."""
    if not warnings:
        return "✓ no divergent literature quotes (every (citekey, paraphrase) cites one span)"
    lines = [f"⚠ {len(warnings)} divergent literature quote(s) — same (citekey, paraphrase) "
             "backed by different spans across modules; reconcile to one canonical quote:"]
    for w in warnings:
        lines.append(f"  {w['citekey']}: {w['paraphrase']!r}")
        for sp in w["spans"]:
            lines.append(f"      span: {sp!r}")
        lines.append(f"      cited by: {', '.join(w['where'])}")
    return "\n".join(lines)


def render_worklist(result: dict[str, Any]) -> str:
    """Human-readable one-block summary of a :func:`worklist` run."""
    if result.get("error"):
        return f"{result['report']}: {result['error']}"
    items = result.get("items", [])
    lines = [f"{result['report']}",
             f"  to judge: {len(items)}  ·  missing {result.get('missing', 0)}  ·  "
             f"stale {result.get('stale', 0)}  ·  already-fresh {result.get('fresh', 0)}"]
    for it in items:
        lines.append(f"    [{it['status']}] {it['citekey']} (tier {it['tier']}): "
                     f"{it['paraphrase']!r}")
    if items:
        lines.append("  → a fresh-context judge subagent decides supported/unsupported for each, "
                     "then `sci judge --record`")
    return "\n".join(lines)


def render_record(result: dict[str, Any]) -> str:
    """Human-readable one-block summary of a :func:`record_verdicts` run."""
    if result.get("error"):
        return f"{result['report']}: {result['error']}"
    lines = [f"{result['report']} (judge: {result['judge_id']})",
             f"  recorded {result['recorded']}  ·  rejected {result['rejected']}  →  "
             f"cache: {result['cache']}"]
    for d in result.get("details", []):
        if d["status"] == "recorded":
            mark = "supported" if d.get("supported") else "UNSUPPORTED"
            lines.append(f"    recorded {d['citekey']}: {mark}")
        else:
            lines.append(f"    rejected {d.get('citekey', '?')}: {d.get('reason', '')}")
    return "\n".join(lines)
