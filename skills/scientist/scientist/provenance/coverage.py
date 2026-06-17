"""Claims coverage — is the grounding keeping up with the library?

The completeness counterpart to the report audit. The audit checks that the citations a
report *wrote* resolve; this checks the opposite gap: papers banked into the bibliographer
library that **no grounded claim cites**. A literature sweep that grows the library by
dozens of papers while the claim set stays put is the silent failure mode — the library
looks like diligence, the audit stays green, and the grounding quietly stagnates (the
"0 new claims after banking 58 papers" case).

Pure and store-free: the set-difference and recency filter live here and are unit-tested
on plain data; the CLI glue (reading the library via ``bib list --json`` and the cited
citekeys from grounding reports) lives in ``sci.py``. The mechanical diff is a *worklist
generator*, not a verdict — judging which uncited papers are load-bearing enough to
warrant a claim stays an agent's job (typically a fresh-context completeness critic).
"""
from __future__ import annotations

from typing import Any


def cited_citekeys(claim_index: dict[str, dict[str, Any]]) -> set[str]:
    """Every bibliographer citekey cited by some literature claim, read from each claim's
    ``evidence.lit_sources[].citekey`` (the same structure ``sci report`` resolves)."""
    out: set[str] = set()
    for claim in claim_index.values():
        ev = claim.get("evidence") or {}
        if not isinstance(ev, dict):
            continue
        for src in ev.get("lit_sources") or []:
            ck = src.get("citekey")
            if ck:
                out.add(str(ck))
    return out


def coverage(library: list[dict[str, Any]], cited: set[str],
             since: str | None = None, recent_n: int = 15) -> dict[str, Any]:
    """Diff the library against the cited citekeys.

    ``library`` is a list of records carrying at least ``citekey`` (and ``added_at`` for
    recency), e.g. ``bib list --json``. ``since`` is an ISO date/timestamp; ``added_at``
    sorts lexically as ISO-8601, so a string compare is the recency test. Returns counts,
    a coverage percentage, and the uncited papers newest-first — the worklist a critic
    judges for which deserve a claim.
    """
    lib_keys = {str(r["citekey"]) for r in library if r.get("citekey")}
    cited_in_lib = lib_keys & cited
    uncited = sorted(
        ({"citekey": str(r["citekey"]), "added_at": r.get("added_at")}
         for r in library if r.get("citekey") and str(r["citekey"]) not in cited),
        key=lambda r: (r.get("added_at") or ""), reverse=True)
    if since:
        flagged = [r for r in uncited if (r.get("added_at") or "") >= since]
    else:
        flagged = uncited[:recent_n]
    return {
        "library_total": len(lib_keys),
        "cited_count": len(cited_in_lib),
        "uncited_count": len(uncited),
        "coverage_pct": round(100.0 * len(cited_in_lib) / len(lib_keys), 1) if lib_keys else 0.0,
        "since": since,
        "flagged": flagged,         # uncited since `since`, or the newest `recent_n`
        "uncited": uncited,         # the full uncited set, newest-first
        # citekeys a claim cites that are NOT in this library (typo / wrong library / stale)
        "cited_not_in_library": sorted(cited - lib_keys),
    }


def render_coverage(result: dict[str, Any]) -> str:
    """Human summary: coverage headline, then the flagged uncited worklist newest-first."""
    lines = [
        f"claims coverage: {result['cited_count']}/{result['library_total']} library papers "
        f"cited by a claim ({result['coverage_pct']}%); {result['uncited_count']} uncited."
    ]
    flagged = result.get("flagged", [])
    if result.get("since"):
        lines.append(f"  uncited, added since {result['since']} ({len(flagged)}) — "
                     f"candidates for a claim (judge which are load-bearing):")
    elif flagged:
        lines.append(f"  most recently banked uncited ({len(flagged)} of "
                     f"{result['uncited_count']}):")
    for r in flagged:
        lines.append(f"    - {r['citekey']}  ({(r.get('added_at') or '')[:10]})")
    missing = result.get("cited_not_in_library", [])
    if missing:
        lines.append(f"  ! {len(missing)} citekey(s) cited by a claim but absent from the "
                     f"library (typo / wrong library?): {', '.join(missing[:8])}")
    return "\n".join(lines)
