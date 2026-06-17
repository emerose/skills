"""scientist.grounding.refresh — the literature-verdict refresh step (``sci judge``).

This is the **one and only** place the support judge (the LLM) is invoked. It reads an existing
``grounding_report.json``, finds the machine-judged literature sources (those a claim authored
with ``source(paraphrase=…)``), and for each one whose verdict is missing or stale it calls the
judge and writes the result to the verdict cache (:mod:`scientist.grounding.judgments`). A normal
grounding run / ``sci report`` then reads the populated cache and stays free + deterministic.

Determinism discipline: the model client (:mod:`scientist.grounding.judge`) is imported lazily
here, never at module import, and never by the pytest path. The default judge is injectable
(``judge=`` / ``library_resolver=``) so the refresh logic is unit-testable with a stub — no real
API, no real bibliographer library.

Graceful degradation: if the judge is unavailable (no API key / no SDK), each source is left
unset and counted under ``skipped`` — the claim stays ``needs-judgment`` (non-blocking), never a
crash.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .judgments import JudgmentCache, JUDGMENT_CACHE_NAME, evidence_sha, judge_model_id


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
    """The text span the judge must read for one source. Tier 1 (quote) and tier 2 (chunk) store
    the span in the report, so judging needs only the model. Tier 3 (whole-doc) does not — it is
    re-resolved from the library via ``library_resolver`` (costly), or skipped if none is given."""
    span = src.get("span")
    if span:
        return span
    if int(src.get("tier", 3)) >= 3 and library_resolver is not None:
        return library_resolver(src)
    return None


def refresh(report_path: Path | str, cache_path: Path | str | None = None, *,
            model_id: str | None = None,
            judge: Callable[..., dict] | None = None,
            library_resolver: Callable[[dict], str] | None = None,
            force: bool = False) -> dict[str, Any]:
    """Refresh the support verdicts for one grounding report.

    For each machine-judged literature source whose cache entry is missing or stale (or when
    ``force``), call ``judge(span, paraphrase, model_id=…)`` and store ``{supported, rationale}``
    in the verdict cache. Returns a summary
    ``{report, cache, model_id, judged, fresh, skipped, errors, details}``.

    ``judge`` defaults to :func:`scientist.grounding.judge.judge_entailment` (the real model
    client, imported lazily). Pass a stub in tests."""
    rp = Path(report_path)
    cp = Path(cache_path) if cache_path is not None else rp.parent / JUDGMENT_CACHE_NAME
    mid = model_id or judge_model_id()

    judge_fn = judge
    if judge_fn is None:                     # lazily import the model client — never at module load
        from .judge import judge_entailment as judge_fn

    try:
        data = json.loads(rp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"report": str(rp), "error": f"unreadable grounding report: {exc}",
                "judged": 0, "fresh": 0, "skipped": 0, "errors": 0, "details": []}
    claims = data.get("claims") if isinstance(data, dict) else data
    claims = claims if isinstance(claims, list) else []

    cache = JudgmentCache.load(cp)
    judged = fresh = skipped = errors = 0
    details: list[dict[str, Any]] = []

    for claim in claims:
        if not isinstance(claim, dict):
            continue
        for src in _machine_sources(claim):
            citekey = str(src.get("citekey") or "")
            paraphrase = str(src.get("paraphrase") or "")
            span = _span_for(src, library_resolver=library_resolver)
            esha = src.get("evidence_sha") or (evidence_sha(span) if span else None)
            if not esha:
                continue
            status, _ = cache.lookup(citekey, esha, paraphrase, mid)
            if status == "fresh" and not force:
                fresh += 1
                continue
            if span is None:                # tier-3 with no resolver: cannot judge here
                skipped += 1
                details.append({"citekey": citekey, "status": "skipped",
                                "reason": "tier-3 span unavailable (no library resolver)"})
                continue
            try:
                verdict = judge(span, paraphrase, model_id=mid)
            except Exception as exc:        # JudgeUnavailable or a transport error → degrade
                skipped += 1
                details.append({"citekey": citekey, "status": "skipped", "reason": str(exc)})
                continue
            cache.put(citekey=citekey, evidence_sha_=esha, paraphrase=paraphrase, model_id=mid,
                      supported=bool(verdict.get("supported")),
                      rationale=str(verdict.get("rationale") or ""), timestamp=_now_iso(),
                      tier=int(src.get("tier", 3)))
            judged += 1
            details.append({"citekey": citekey, "status": "judged",
                            "supported": bool(verdict.get("supported"))})

    if judged:
        cache.save(cp)
    return {"report": str(rp), "cache": str(cp), "model_id": mid, "judged": judged,
            "fresh": fresh, "skipped": skipped, "errors": errors, "details": details}


def render_summary(result: dict[str, Any]) -> str:
    """Human-readable one-block summary of a :func:`refresh` run."""
    if result.get("error"):
        return f"{result['report']}: {result['error']}"
    lines = [f"{result['report']} (model: {result['model_id']})",
             f"  judged {result['judged']}  ·  already-fresh {result['fresh']}  ·  "
             f"skipped {result['skipped']}  →  cache: {result['cache']}"]
    for d in result.get("details", []):
        if d["status"] == "judged":
            mark = "supported" if d.get("supported") else "UNSUPPORTED"
            lines.append(f"    judged {d['citekey']}: {mark}")
        elif d["status"] == "skipped":
            lines.append(f"    skipped {d['citekey']}: {d.get('reason', '')}")
    if result.get("skipped"):
        lines.append("  (skipped sources stay needs-judgment — non-blocking; set "
                     "ANTHROPIC_API_KEY and re-run `sci judge`)")
    return "\n".join(lines)
