"""scientist.grounding.judgments — the literature support-verdict cache (pure, offline).

A *literature* claim's deterministic tripwire is the verbatim quote (``source(quote=…)``):
present in the cited paper's text or not, every audit, no model. The separable part is the
**support judgment** — "is paraphrase *P* a fair reading of quote *Q*?" — which used to be a
hand-stamped human boolean (``@reviewed(support=…)``) the audit never re-checked. This module
holds the answer as a **cache** so the claims suite can stay what it is: a re-runnable,
deterministic, *offline* pytest suite.

The discipline is strict and load-bearing — and note WHO judges: **no model lives in this tool.**
The orchestrating agent (an LLM that already read the paper, ideally via a fresh-context judge
subagent for independence) produces the verdict; ``sci judge`` only lists the work and records the
verdict it is handed.

  * The verdict is WRITTEN by the record step (``scientist.grounding.refresh.record_verdicts`` /
    ``sci judge --record``), which ingests caller-supplied verdicts and pins each one with an
    ``evidence_sha`` the tool recomputes itself.
  * The pytest path (``source()``) and the audit (``provenance.report.lit_verdict``) only ever
    READ this cache — a plain JSON file, a pure function of bytes. No network, no key, no model.

This module is pure stdlib and safe to import on the pytest path.

## Cache shape

Each verdict answers one entailment question, keyed by the pair ``(evidence_sha, paraphrase)``
(decision: a quote/paraphrase edit must invalidate the verdict, never silently carry it forward;
*who* judged is metadata, not part of the key — a verdict by a different judge is still valid):

  * ``evidence_sha`` — sha256 of the *folded* text span the judge read (the verbatim quote for a
    tier-1 source, a chunk's text for tier-2, the whole-document text for tier-3). The span is
    folded with the SAME normalization quote-matching uses (NFKC, Unicode-dash fold, strip Markdown
    ``*``/``_``, collapse whitespace) BEFORE hashing, so two quotes the matcher treats as the same
    evidence (e.g. ``*Ube3a*…`` vs ``Ube3a…``) share one cache identity → one verdict (see
    :func:`evidence_sha` and ``grounding.normalize``).
  * ``paraphrase``  — the claim's paraphrase of that span (the human-authored anchor).

The stored entry is machine-pinned and inspectable, so a green claim is never an opaque "the LLM
said yes" — it records who judged it and when:

    {evidence_sha, paraphrase, judge_id, citekey, tier, supported, rationale, timestamp}

## Lookup → fresh / stale / miss

:meth:`JudgmentCache.lookup` resolves a source against the cache into one of three states,
mirroring the existing ``stale-review`` design (which fires when a cited paper's text drifts):

  * ``fresh`` — an entry exists for this exact ``(evidence_sha, paraphrase)``; use it.
  * ``stale`` — an entry exists for this ``(citekey, paraphrase)`` but under a different
    ``evidence_sha`` (the quote/span changed since judged) → re-judge.
  * ``miss``  — no entry for this ``(citekey, paraphrase)`` at all (never judged, or the
    paraphrase was edited into a new question) → judge.

Both ``stale`` and ``miss`` are resolved the same way — run ``sci judge`` — so the distinction is
purely diagnostic (``stale-judgment`` vs ``needs-judgment`` in the audit).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .normalize import fold_match as _fold_match

# The sidecar the record step writes and the pytest/audit paths read. Lives next to the
# grounding report it serves (e.g. ``program/analysis/lit_judgments.json``) — a machine-owned
# artifact, like ``grounding_report.json``, NOT a hand-edited decorator value.
JUDGMENT_CACHE_NAME = "lit_judgments.json"

# Stamped on a recorded verdict when the caller does not name itself. The judge is the
# orchestrating agent (ideally a fresh-context subagent); ``judge_id`` is purely descriptive
# metadata — it is NOT part of the staleness key.
DEFAULT_JUDGE_ID = "agent"


def evidence_sha(span: str) -> str:
    """sha256 of the *folded* text span — the stable identity of the *evidence* side of one
    entailment question (quote / chunk / whole-doc text).

    The hash is taken over ``fold_match(span)``, the SAME normalization quote-matching uses
    (NFKC, fold Unicode dashes, strip Markdown ``*``/``_``, collapse whitespace), NOT the raw
    bytes. This is deliberate and load-bearing: two quotes the matcher treats as the same
    evidence (e.g. ``*Ube3a* gene dosage…`` vs ``Ube3a gene dosage…``) must map to ONE cache
    identity → ONE shared verdict. If we hashed the raw span instead, the cache's
    one-canonical-verdict pruning (``JudgmentCache.put``) would see the markdown/whitespace
    variant as a drift and stale a good verdict cited from another module. A judge still READS
    the raw span (``span_text`` in the worklist); only the cache *identity* is folded."""
    return hashlib.sha256(_fold_match(span).encode("utf-8")).hexdigest()


def _key(evidence_sha_: str, paraphrase: str) -> str:
    h = hashlib.sha256()
    h.update(evidence_sha_.encode("utf-8"))
    h.update(b"\x00")
    h.update(paraphrase.encode("utf-8"))
    return h.hexdigest()


@dataclass
class JudgmentCache:
    """An in-memory view of the verdict sidecar. Load it with :meth:`load`, query with
    :meth:`lookup`, populate with :meth:`put` (refresh step only), persist with :meth:`save`."""

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None

    # ---- io ---------------------------------------------------------------- #
    @classmethod
    def load(cls, path: Path | str) -> "JudgmentCache":
        """Load the cache from ``path``; an absent or unreadable file yields an empty cache
        bound to that path (so the pytest path degrades to *miss*, never a crash)."""
        p = Path(path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            ents = data.get("verdicts", {}) if isinstance(data, dict) else {}
            if not isinstance(ents, dict):
                ents = {}
        except (OSError, ValueError):
            ents = {}
        return cls(entries=dict(ents), path=p)

    def save(self, path: Path | str | None = None) -> Path:
        """Write the cache deterministically (sorted keys) to ``path`` (or the bound path)."""
        target = path if path is not None else self.path
        if target is None:
            raise ValueError("JudgmentCache.save needs a path (none bound)")
        p = Path(target)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"verdicts": self.entries}, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8")
        return p

    # ---- query / mutate ---------------------------------------------------- #
    def lookup(self, citekey: str, evidence_sha_: str,
               paraphrase: str) -> tuple[str, dict[str, Any] | None]:
        """Resolve one source to ``("fresh"|"stale"|"miss", entry|None)`` — see module docs."""
        exact = self.entries.get(_key(evidence_sha_, paraphrase))
        if exact is not None:
            return ("fresh", exact)
        for e in self.entries.values():
            if e.get("citekey") == citekey and e.get("paraphrase") == paraphrase:
                return ("stale", e)        # quote/span drifted since judged
        return ("miss", None)

    def put(self, *, citekey: str, evidence_sha_: str, paraphrase: str, judge_id: str,
            supported: bool, rationale: str, timestamp: str, tier: int) -> dict[str, Any]:
        """Store a caller-supplied verdict (record step only). Returns the stored entry.

        A re-judge for the same ``(citekey, paraphrase)`` under a *new* ``evidence_sha`` writes a
        new key; the orphaned old entry is pruned so the cache reflects only live questions.
        ``judge_id`` is stamped as metadata (who judged) and never enters the key."""
        for k in [k for k, e in self.entries.items()
                  if e.get("citekey") == citekey and e.get("paraphrase") == paraphrase]:
            del self.entries[k]
        entry = {
            "citekey": citekey, "evidence_sha": evidence_sha_, "paraphrase": paraphrase,
            "judge_id": judge_id, "supported": bool(supported), "rationale": rationale,
            "timestamp": timestamp, "tier": int(tier),
        }
        self.entries[_key(evidence_sha_, paraphrase)] = entry
        return entry
