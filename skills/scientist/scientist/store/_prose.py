"""Prose ⟷ claims enforcement (reports only — never mutates).

The semantic-audit *deterministic gate*: a quantitative result asserted in prose
(a ``README.md`` today, a ``reports/`` doc, and tomorrow a ``sci report`` Markdown
blob) must map to a **grounded** ``kind=claim`` — else it is flagged so the prose
can't drift ahead of the evidence.

Two reusable pieces, both stdlib-only (no libkit, no I/O):

* :func:`find_quantitative_assertions` — a conservative heuristic detector. Given
  any Markdown text it returns the sentences that assert a *quantitative result*
  (a percentage, fold-change, p-value, n=, concentration/dose, IC50…). Bare
  numbers, dates, figure/section refs, and plain time/temperature method details
  do **not** trigger — false-positive avoidance is deliberate (see ``_QUANT_RE``).

* :func:`enforce_prose` — given Markdown + a claim list (each ``{claim_id,
  statement, outcome, strength, claim_kind}``, the shape the libkit index *and* a
  grounding report both reduce to), maps each assertion to its backing claim and
  returns the flagged ones. An assertion is **cleared** only by an explicit
  ``[claim:<id>]`` citation that resolves to a grounded, non-weak claim; an
  assertion cited only to a contradicted/weak claim is surfaced *with* its
  outcome+strength (not silently passed); an un-cited assertion is flagged
  ``unbacked`` (with an advisory best-match suggestion, never an auto-clear — a
  coincidental token overlap must not mask missing evidence).

:func:`enforce_prose` is the entry point the planned report phase (`sci report`)
reuses verbatim against report Markdown — it is intentionally free of any
README-specific or store-specific plumbing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

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
_CITE_RE = re.compile(r"\[\[?\s*claim:\s*([^\]]+?)\s*\]\]?", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Quantitative-assertion detector
# --------------------------------------------------------------------------- #
# Each alternative is "result-like": a number carrying meaning a claim would
# pin down. Deliberately conservative — NO bare integers, dates, figure/section
# numbers, version strings, or plain method time/temperature (e.g. "30 min",
# "37 °C", "for 3 days"), which are common in prose and rarely the asserted result.
_QUANT_PATTERNS = [
    r"\d+(?:\.\d+)?\s*%",                                  # 80%, 80 %
    r"\b\d+(?:\.\d+)?\s*[-‑]?[Ff]old\b",                   # 3-fold, 3 fold
    r"\b\d+(?:\.\d+)?\s*[×x]\b",                           # 3×, 3x
    r"\bp\s*[<>=]\s*0?\.\d+",                              # p < 0.05
    r"\bp[-\s]?values?\b",                                 # p-value(s)
    r"\b[nN]\s*=\s*\d+",                                   # n = 6
    r"±\s*\d+(?:\.\d+)?",                                  # ± 1.2
    r"\b(?:IC50|IC90|EC50|EC90|GI50|TC50|Ki|Kd)\b",       # potency metrics
    r"\b95\s*%?\s*CI\b",                                   # 95% CI
    # number + a result-relevant unit (concentration, mass dose, molecular size):
    r"\b\d+(?:\.\d+)?\s*(?:nM|µM|uM|mM|pM|fM|M|nmol|µmol|umol|mmol|mol)\b",
    r"\b\d+(?:\.\d+)?\s*(?:mg|µg|ug|ng|pg|kg|g)\s*/\s*(?:kg|mL|ml|L|day|d)\b",
    r"\b\d+(?:\.\d+)?\s*(?:kDa|Da|bp|kb|nt)\b",
]
_QUANT_RE = re.compile("|".join(_QUANT_PATTERNS))

# Split a line into sentences on terminal punctuation followed by a capital / open
# bracket (so a trailing "[claim:…]" stays attached to its sentence).
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")

# Tokens worth comparing for the advisory auto-match suggestion: numbers and words
# of length >= 4 (drops "the", "and", "with" noise).
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-]{3,}|\d+(?:\.\d+)?%?")


def _strip_noise(markdown: str) -> str:
    """Blank out fenced code blocks, inline code spans, and HTML comments (the
    ``scientist:deps`` dependency block) while preserving line count + offsets, so
    a ``n=3`` inside a code span or a sha inside the deps comment never trips the
    detector and reported line numbers stay accurate."""
    lines: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
            in_fence = not in_fence
            lines.append("")
            continue
        lines.append("" if in_fence else line)
    text = "\n".join(lines)
    # HTML comments (may span lines) -> spaces, keeping newlines.
    text = re.sub(r"<!--.*?-->",
                  lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.DOTALL)
    # inline code -> spaces (same length, no newlines inside by the `[^`\n]` class).
    text = re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), text)
    return text


def find_quantitative_assertions(markdown: str) -> list[dict[str, Any]]:
    """Sentences in ``markdown`` that assert a quantitative result.

    Returns ``[{"text", "line", "matches"}]`` — ``line`` is 1-based into the
    original text, ``matches`` the quantitative tokens that triggered detection.
    Reusable on any Markdown blob (README, report doc); no I/O, no store.
    """
    cleaned = _strip_noise(markdown or "")
    out: list[dict[str, Any]] = []
    for line_no, raw in enumerate(cleaned.split("\n"), start=1):
        line = raw.strip()
        if not line:
            continue
        for sent in _SENT_SPLIT.split(line) or [line]:
            hits = [m.group(0).strip() for m in _QUANT_RE.finditer(sent)]
            if hits:
                out.append({"text": sent.strip(), "line": line_no, "matches": hits})
    return out


# --------------------------------------------------------------------------- #
# Enforcement
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


def enforce_prose(markdown: str, claims: list[dict[str, Any]] | None,
                  *, source: str | None = None) -> dict[str, Any]:
    """Check that every quantitative assertion in ``markdown`` maps to a grounded claim.

    ``claims`` is a list of claim records — the libkit ``kind=claim`` index (live,
    pruned, authoritative) or, store-free, the per-experiment ``grounding_report``
    claims reduced to the same shape. Returns::

        {"source", "assertions": <int>, "backed": <int>, "flags": [ ... ]}

    where each flag is one un-cleared assertion:
      * ``unbacked``      — quantitative assertion with no ``[claim:…]`` citation
        (carries an advisory ``suggestion`` when a plausible claim exists);
      * ``weak-backing``  — cited only to claim(s) that are contradicted (``xfail``),
        drifted (``failed``), unverifiable (``skipped``), or weak/unspecified
        strength — surfaced with each backing's ``outcome``+``strength``;
      * ``unknown-claim`` — a ``[claim:…]`` citation that resolves to no indexed claim.

    This is the reusable enforcement entry point the planned report phase's ``sci report`` calls
    directly on report Markdown.
    """
    norm = [_norm_claim(c) for c in (claims or []) if (c.get("claim_id") or c.get("id"))]
    by_id = {c["claim_id"]: c for c in norm}
    assertions = find_quantitative_assertions(markdown)
    flags: list[dict[str, Any]] = []
    backed = 0
    for a in assertions:
        base = {"text": a["text"], "line": a["line"], "matches": a["matches"]}
        cites = [t.strip() for t in _CITE_RE.findall(a["text"])]
        if cites:
            resolved = [c for t in cites if (c := _resolve_citation(t, by_id, norm))]
            unknown = [t for t in cites if _resolve_citation(t, by_id, norm) is None]
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
                              "reason": "citation does not resolve to an indexed claim"})
        else:
            flag = {**base, "status": "unbacked",
                    "reason": "quantitative assertion with no [claim:…] citation to a grounded claim"}
            sugg = _suggest(a["text"], norm)
            if sugg:
                flag["suggestion"] = sugg
            flags.append(flag)
    return {"source": source, "assertions": len(assertions), "backed": backed, "flags": flags}


# --------------------------------------------------------------------------- #
# Prose-document discovery (the README + reports/ Markdown to enforce over)
# --------------------------------------------------------------------------- #
def iter_prose_docs(exp_dir: Path, home: Path | None = None) -> Iterator[tuple[str, str]]:
    """Yield ``(label, text)`` for each prose doc whose quantitative claims this
    experiment must back: the root ``README.md`` plus any ``reports/**/*.md``.

    ``label`` is home-relative when ``home`` is given (else relative to ``exp_dir``).
    The report phase will instead feed its own Markdown straight to
    :func:`enforce_prose`; this walker is only for the audit's per-experiment pass.
    """
    candidates: list[Path] = []
    for name in ("README.md", "README.markdown"):
        p = exp_dir / name
        if p.is_file():
            candidates.append(p)
    reports = exp_dir / "reports"
    if reports.is_dir():
        candidates.extend(sorted(p for p in reports.rglob("*")
                                 if p.is_file() and p.suffix.lower() in (".md", ".markdown")))
    base = home or exp_dir
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            label = str(p.resolve().relative_to(base.resolve()))
        except ValueError:
            label = p.name
        yield label, text
