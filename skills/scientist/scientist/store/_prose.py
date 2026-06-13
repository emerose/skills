"""Prose ⟷ claims enforcement (reports only — never mutates).

The deterministic half of "no asserted result without a grounded backing": an
evidentiary conclusion asserted in prose (a ``README.md`` today, a ``reports/`` doc,
and tomorrow a ``sci report`` Markdown blob) must map to a grounded ``kind=claim`` —
else it is flagged, so the prose can't drift ahead of the evidence.

**Detection is inverted to the caller.** Judging *whether a sentence asserts an
evidentiary conclusion* is a language task, not a regex one — so this module does NOT
detect assertions. The caller supplies them: the parallel-agent semantic pass reads
the prose, decides which sentences are conclusions worth grounding, and feeds that
list here (via ``sci enforce-prose``). What stays deterministic — and testable — is
the part worth pinning down: parse the exact ``[claim:<id>]`` citation, resolve it
against the claim set, and check the backing is grounded and strong.

**Quantitative vs qualitative is a severity tier, not a scope filter.** A qualitative
conclusion ("well tolerated", "sustained knockdown") is grounded and audited exactly
like a numeric one — the claim layer never cared whether a statement had a number. But
qualitative conclusions are fuzzier to enumerate and noisier in bulk, so a *missing
citation* on one is **advisory** while a missing citation on a numeric result
**blocks**; an explicit-but-bad citation blocks either way. The caller tags each
assertion's ``kind``; the default (``quantitative``) keeps the original blocking gate.

:func:`enforce_prose` is the single entry point. It is pure (no I/O, no store, no
network) and free of any README- or store-specific plumbing, so the planned report
phase (``sci report``) reuses it verbatim against report Markdown.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# Backing-quality vocabulary (shared with the grounding model)
# --------------------------------------------------------------------------- #
# An outcome that actually supports the statement (pytest pass / unexpected pass).
# failed = DRIFT, xfail = contradicted, skipped = unverifiable — none clear prose.
GROUNDED_OUTCOMES = {"passed", "xpass"}
# Strengths strong enough to back a stated result. weak/unspecified/None do not.
CLEARING_STRENGTHS = {"strong", "moderate"}

# Explicit prose→claim citation: [claim:<id>] or [[claim:<id>]]. The id may be the
# full stable claim_id (``<exp>::<test-file>::<node>``) or just its trailing node.
# This syntax is exact, so parsing it IS deterministic (unlike detecting assertions).
_CITE_RE = re.compile(r"\[\[?\s*claim:\s*([^\]]+?)\s*\]\]?", re.IGNORECASE)

# Tokens worth comparing for the advisory auto-match suggestion: numbers and words
# of length >= 4 (drops "the", "and", "with" noise).
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-]{3,}|\d+(?:\.\d+)?%?")


# --------------------------------------------------------------------------- #
# Claim helpers
# --------------------------------------------------------------------------- #
def _norm_claim(c: dict[str, Any]) -> dict[str, Any]:
    """Reduce a claim record (libkit index card OR grounding-report claim) to the
    one shape this module reasons over."""
    return {
        "claim_id": c.get("claim_id") or c.get("id") or "",
        "statement": c.get("statement") or "",
        "outcome": c.get("outcome"),
        "strength": c.get("strength"),
        "claim_kind": c.get("claim_kind") or c.get("kind"),
    }


def _is_grounding(c: dict[str, Any]) -> bool:
    """True iff this claim is positive evidence strong enough to clear an assertion."""
    return (c.get("outcome") in GROUNDED_OUTCOMES
            and (c.get("strength") or "") in CLEARING_STRENGTHS)


def _backref(c: dict[str, Any]) -> dict[str, Any]:
    return {"claim_id": c.get("claim_id"), "outcome": c.get("outcome"),
            "strength": c.get("strength"), "claim_kind": c.get("claim_kind")}


def _resolve_citation(token: str, by_id: dict[str, dict[str, Any]],
                      claims: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve a cited token to a single claim, or None (absent OR ambiguous).

    Exact ``claim_id`` wins; otherwise a trailing-node suffix match
    (``…::<token>`` or node-name ``==`` token), but only when it's unambiguous."""
    if token in by_id:
        return by_id[token]
    matches = [c for c in claims
               if c["claim_id"].endswith("::" + token)
               or c["claim_id"].split("::")[-1] == token]
    return matches[0] if len(matches) == 1 else None


def _suggest(text: str, claims: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Advisory best-overlap claim for an un-cited assertion — a hint for the author
    to add a citation, NEVER a backing (it must not clear the assertion)."""
    want = {t.lower() for t in _TOKEN_RE.findall(text)}
    if not want:
        return None
    best, best_score = None, 0.0
    for c in claims:
        have = {t.lower() for t in _TOKEN_RE.findall(c["statement"])}
        if not have:
            continue
        score = len(want & have) / len(want | have)
        if score > best_score:
            best, best_score = c, score
    if best is None or best_score < 0.18:   # conservative floor; below = no useful hint
        return None
    return {**_backref(best), "statement": best["statement"], "score": round(best_score, 3)}


# An assertion's nature, set by the extracting agent. Governs flag severity, not
# whether it's checked: a numeric *result* is high-precision to spot and self-
# disciplining, so an unbacked one BLOCKS; a *qualitative* conclusion ("well
# tolerated", "sustained knockdown") is fuzzier to enumerate and noisier in bulk, so
# an unbacked one is ADVISORY. (An explicit-but-bad citation — weak/contradicted or
# unresolved — blocks either way: it's a checkable author error, not extraction noise.)
QUANTITATIVE = "quantitative"
QUALITATIVE = "qualitative"


def _tier(kind: Any) -> str:
    """Normalize an assertion's declared kind to its severity tier. Only an explicit,
    correctly-spelled ``qualitative`` downgrades to the advisory tier; absent, unknown,
    or typo'd kinds stay ``quantitative`` (blocking) — fail-safe, so a typo can't
    silently mute the gate, and backward-compatible with bare-string assertions."""
    k = (kind or "").strip().lower()
    return QUALITATIVE if k in ("qualitative", "qual") else QUANTITATIVE


def _normalize_assertion(a: Any) -> dict[str, Any]:
    """Accept a bare string or ``{"text", "line"?, "kind"?}``; normalize to a dict
    carrying its severity ``tier``."""
    if isinstance(a, str):
        return {"text": a, "line": None, "tier": QUANTITATIVE}
    return {"text": a.get("text") or "", "line": a.get("line"),
            "tier": _tier(a.get("kind"))}


def _severity(status: str, tier: str) -> str:
    """Blocking vs advisory. Only a *missing* citation on a *qualitative* conclusion is
    advisory; everything else (any quantitative flag; any explicit citation error)
    blocks."""
    if status == "unbacked" and tier == QUALITATIVE:
        return "advisory"
    return "blocking"


# --------------------------------------------------------------------------- #
# Enforcement — the deterministic core
# --------------------------------------------------------------------------- #
def enforce_prose(assertions: list[Any], claims: list[dict[str, Any]] | None,
                  *, source: str | None = None) -> dict[str, Any]:
    """Check that every supplied evidentiary assertion maps to a grounded claim.

    ``assertions`` — the evidentiary conclusions the caller (the semantic-pass agent)
    extracted from the prose. Each is a string, or ``{"text": str, "line": int,
    "kind": "quantitative"|"qualitative"}``. Detection is the caller's judgment; this
    function does NOT scan for assertions. ``kind`` sets the severity tier (default
    ``quantitative``); it does not change *whether* the assertion is checked — a
    qualitative conclusion ("well tolerated", "sustained knockdown") is grounded and
    audited exactly like a numeric one.

    ``claims`` — claim records, each ``{claim_id, statement, outcome, strength,
    claim_kind}`` (the shape the libkit ``kind=claim`` index *and* a grounding report
    both reduce to).

    Returns::

        {"source", "assertions": <int>, "backed": <int>,
         "blocking": <int>, "advisory": <int>, "flags": [ ... ]}

    where each flag is one un-cleared assertion carrying ``status``, ``tier``
    (quantitative|qualitative), and ``severity`` (blocking|advisory):
      * ``unbacked``      — no ``[claim:…]`` citation (carries an advisory
        ``suggestion`` when a plausible claim exists). Blocking for a quantitative
        assertion; **advisory** for a qualitative one — the fuzzy, high-volume case,
        softened so the gate stays trustworthy.
      * ``weak-backing``  — cited only to claim(s) that are contradicted (``xfail``),
        drifted (``failed``), unverifiable (``skipped``), or weak/unspecified
        strength — surfaced with each backing's ``outcome``+``strength``. Always
        blocking (a checkable author error, regardless of tier).
      * ``unknown-claim`` — a ``[claim:…]`` citation that resolves to no claim. Always
        blocking.

    An assertion is **cleared** only by a citation resolving to a grounded
    (``passed``/``xpass``), strong/moderate claim. A coincidental statement overlap
    never clears (it only ever appears as an advisory ``suggestion``) — a false
    "backed" must not mask missing evidence.
    """
    norm_claims = [_norm_claim(c) for c in (claims or []) if (c.get("claim_id") or c.get("id"))]
    by_id = {c["claim_id"]: c for c in norm_claims}
    items = [_normalize_assertion(a) for a in (assertions or [])]
    flags: list[dict[str, Any]] = []
    backed = 0
    for a in items:
        text, tier = a["text"], a["tier"]
        base = {"text": text, "line": a.get("line"), "tier": tier}
        cites = [t.strip() for t in _CITE_RE.findall(text)]
        flag: dict[str, Any] | None = None
        if cites:
            resolved = [c for t in cites if (c := _resolve_citation(t, by_id, norm_claims))]
            unknown = [t for t in cites if _resolve_citation(t, by_id, norm_claims) is None]
            if any(_is_grounding(c) for c in resolved):
                backed += 1
                continue
            if resolved:
                flag = {**base, "status": "weak-backing",
                        "backing": [_backref(c) for c in resolved],
                        "reason": "cited claim(s) are contradicted/drifted/unverifiable "
                                  "or weak — not grounded support"}
            else:
                flag = {**base, "status": "unknown-claim", "cited": unknown,
                        "reason": "citation does not resolve to a known claim"}
        else:
            flag = {**base, "status": "unbacked",
                    "reason": "evidentiary assertion with no [claim:…] citation to a grounded claim"}
            sugg = _suggest(text, norm_claims)
            if sugg:
                flag["suggestion"] = sugg
        flag["severity"] = _severity(flag["status"], tier)
        flags.append(flag)
    blocking = sum(1 for f in flags if f["severity"] == "blocking")
    return {"source": source, "assertions": len(items), "backed": backed,
            "blocking": blocking, "advisory": len(flags) - blocking, "flags": flags}
