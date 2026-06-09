"""Prose ⟷ claims enforcement (reports only — never mutates).

The deterministic half of "no quantitative result without a grounded backing": a
result asserted in prose (a ``README.md`` today, a ``reports/`` doc, and tomorrow a
``sci report`` Markdown blob) must map to a grounded ``kind=claim`` — else it is
flagged, so the prose can't drift ahead of the evidence.

**Detection is inverted to the caller.** Judging *whether a sentence asserts a
quantitative result* is a language task, not a regex one — so this module does NOT
detect assertions. The caller supplies them: the parallel-agent semantic pass reads
the prose, decides which sentences are quantitative claims, and feeds that list here
(via ``sci enforce-prose``). What stays deterministic — and testable — is the part
worth pinning down: parse the exact ``[claim:<id>]`` citation, resolve it against the
claim set, and check the backing is grounded and strong.

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


def _normalize_assertion(a: Any) -> dict[str, Any]:
    """Accept either a bare string or ``{"text", "line"?}``; normalize to a dict."""
    if isinstance(a, str):
        return {"text": a, "line": None}
    return {"text": a.get("text") or "", "line": a.get("line")}


# --------------------------------------------------------------------------- #
# Enforcement — the deterministic core
# --------------------------------------------------------------------------- #
def enforce_prose(assertions: list[Any], claims: list[dict[str, Any]] | None,
                  *, source: str | None = None) -> dict[str, Any]:
    """Check that every supplied quantitative assertion maps to a grounded claim.

    ``assertions`` — the quantitative sentences the caller (the semantic-pass agent)
    extracted from the prose. Each is a string, or ``{"text": str, "line": int}``.
    Detection is the caller's judgment; this function does NOT scan for assertions.

    ``claims`` — claim records, each ``{claim_id, statement, outcome, strength,
    claim_kind}`` (the shape the libkit ``kind=claim`` index *and* a grounding report
    both reduce to).

    Returns::

        {"source", "assertions": <int>, "backed": <int>, "flags": [ ... ]}

    where each flag is one un-cleared assertion:
      * ``unbacked``      — quantitative assertion with no ``[claim:…]`` citation
        (carries an advisory ``suggestion`` when a plausible claim exists);
      * ``weak-backing``  — cited only to claim(s) that are contradicted (``xfail``),
        drifted (``failed``), unverifiable (``skipped``), or weak/unspecified
        strength — surfaced with each backing's ``outcome``+``strength``;
      * ``unknown-claim`` — a ``[claim:…]`` citation that resolves to no claim.

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
        text = a["text"]
        base = {"text": text, "line": a.get("line")}
        cites = [t.strip() for t in _CITE_RE.findall(text)]
        if cites:
            resolved = [c for t in cites if (c := _resolve_citation(t, by_id, norm_claims))]
            unknown = [t for t in cites if _resolve_citation(t, by_id, norm_claims) is None]
            if any(_is_grounding(c) for c in resolved):
                backed += 1
                continue
            if resolved:
                flags.append({**base, "status": "weak-backing",
                              "backing": [_backref(c) for c in resolved],
                              "reason": "cited claim(s) are contradicted/drifted/unverifiable "
                                        "or weak — not grounded support"})
            else:
                flags.append({**base, "status": "unknown-claim", "cited": unknown,
                              "reason": "citation does not resolve to a known claim"})
        else:
            flag = {**base, "status": "unbacked",
                    "reason": "quantitative assertion with no [claim:…] citation to a grounded claim"}
            sugg = _suggest(text, norm_claims)
            if sugg:
                flag["suggestion"] = sugg
            flags.append(flag)
    return {"source": source, "assertions": len(items), "backed": backed, "flags": flags}
