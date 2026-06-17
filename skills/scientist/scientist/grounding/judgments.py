"""scientist.grounding.judgments — the literature support-verdict cache (pure, offline).

A *literature* claim's deterministic tripwire is the verbatim quote (``source(quote=…)``):
present in the cited paper's text or not, every audit, no model. The new, separable part is
the **support judgment** — "is paraphrase *P* a fair reading of quote *Q*?" — which used to be a
hand-stamped human boolean (``@reviewed(support=…)``) the audit never re-checked. This module
holds the machine-judged answer as a **cache** so the claims suite can stay what it is: a
re-runnable, deterministic, *offline* pytest suite.

The discipline is strict and load-bearing:

  * The model is invoked **only** in the refresh step (``scientist.grounding.refresh`` /
    ``sci judge``), which WRITES this cache.
  * The pytest path (``source()``) and the audit (``provenance.report.lit_verdict``) only ever
    READ this cache — a plain JSON file, a pure function of bytes. No network, no key, no model.

This module is therefore pure stdlib and safe to import on the pytest path; the actual model
client (:mod:`scientist.grounding.judge`) is *not* — see its module docstring.

## Cache shape

Each verdict answers one entailment question, keyed by the triple
``(evidence_sha, paraphrase, model_id)`` (decision: a model upgrade or a quote/paraphrase edit
must invalidate the verdict, never silently carry it forward):

  * ``evidence_sha`` — sha256 of the exact text span the judge read (the verbatim quote for a
    tier-1 source, a chunk's text for tier-2, the whole-document text for tier-3).
  * ``paraphrase``  — the claim's paraphrase of that span (the human-authored anchor).
  * ``model_id``    — the judge model the verdict was produced by.

The stored entry is machine-written/-rewritten and inspectable, so a green claim is never an
opaque "the LLM said yes":

    {evidence_sha, paraphrase, model_id, citekey, tier, supported, rationale, timestamp}

## Lookup → fresh / stale / miss

:meth:`JudgmentCache.lookup` resolves a source against the cache into one of three states,
mirroring the existing ``stale-review`` design (which fires when a cited paper's text drifts):

  * ``fresh`` — an entry exists for this exact ``(evidence_sha, paraphrase, model_id)``; use it.
  * ``stale`` — an entry exists for this ``(citekey, paraphrase)`` but under a different
    ``evidence_sha`` or ``model_id`` (the quote changed, or the model was upgraded) → re-judge.
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

# The sidecar the refresh step writes and the pytest/audit paths read. Lives next to the
# grounding report it serves (e.g. ``program/analysis/lit_judgments.json``) — a machine-owned
# artifact, like ``grounding_report.json``, NOT a hand-edited decorator value.
JUDGMENT_CACHE_NAME = "lit_judgments.json"

# A small, fast model is the right default for the narrow entailment task — it is two short
# strings, not "read the whole paper". Override per-run via ``$SCIENTIST_JUDGE_MODEL`` (or the
# ``sci judge --model`` flag). Pinned into every cache key so an upgrade forces an explicit,
# auditable mass re-judge rather than a silent shift in verdicts.
DEFAULT_JUDGE_MODEL = "claude-haiku-4-5"


def judge_model_id(env: dict[str, str] | None = None) -> str:
    """The configured judge model id: ``$SCIENTIST_JUDGE_MODEL`` or :data:`DEFAULT_JUDGE_MODEL`.

    Both the pytest path (key matching in ``source()``) and the refresh step read it through
    here so the keys they compute always agree."""
    import os

    e = env if env is not None else os.environ
    return e.get("SCIENTIST_JUDGE_MODEL") or DEFAULT_JUDGE_MODEL


def evidence_sha(span: str) -> str:
    """sha256 of the exact text span the judge reads — the stable identity of the *evidence*
    side of one entailment question (quote / chunk / whole-doc text)."""
    return hashlib.sha256(span.encode("utf-8")).hexdigest()


def _key(evidence_sha_: str, paraphrase: str, model_id: str) -> str:
    h = hashlib.sha256()
    h.update(evidence_sha_.encode("utf-8"))
    h.update(b"\x00")
    h.update(paraphrase.encode("utf-8"))
    h.update(b"\x00")
    h.update(model_id.encode("utf-8"))
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
    def lookup(self, citekey: str, evidence_sha_: str, paraphrase: str,
               model_id: str) -> tuple[str, dict[str, Any] | None]:
        """Resolve one source to ``("fresh"|"stale"|"miss", entry|None)`` — see module docs."""
        exact = self.entries.get(_key(evidence_sha_, paraphrase, model_id))
        if exact is not None:
            return ("fresh", exact)
        for e in self.entries.values():
            if e.get("citekey") == citekey and e.get("paraphrase") == paraphrase:
                return ("stale", e)        # quote/model drifted since judged
        return ("miss", None)

    def put(self, *, citekey: str, evidence_sha_: str, paraphrase: str, model_id: str,
            supported: bool, rationale: str, timestamp: str, tier: int) -> dict[str, Any]:
        """Store a verdict (refresh step only). Returns the stored entry.

        A re-judge for the same ``(citekey, paraphrase)`` under a *new* ``evidence_sha`` /
        ``model_id`` writes a new key; the orphaned old entry is pruned so the cache reflects
        only live questions."""
        for k in [k for k, e in self.entries.items()
                  if e.get("citekey") == citekey and e.get("paraphrase") == paraphrase]:
            del self.entries[k]
        entry = {
            "citekey": citekey, "evidence_sha": evidence_sha_, "paraphrase": paraphrase,
            "model_id": model_id, "supported": bool(supported), "rationale": rationale,
            "timestamp": timestamp, "tier": int(tier),
        }
        self.entries[_key(evidence_sha_, paraphrase, model_id)] = entry
        return entry
