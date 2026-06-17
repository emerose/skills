"""scientist.grounding.judge — the LLM entailment client (refresh step ONLY).

This is the one module that talks to a model. It is invoked **only** by the refresh step
(:mod:`scientist.grounding.refresh` / ``sci judge``) to compute a literature support verdict on a
cache miss or a stale key; the result is written to the verdict cache
(:mod:`scientist.grounding.judgments`). The pytest path and the report audit then read that cache
and never import this module.

CRITICAL DISCIPLINE — do not import this from the pytest path. The claims suite's entire value is
that it is deterministic, offline, and re-runnable. A live model call inside a claim assert would
poison that. Keep the model strictly here, behind the refresh step. ``grounding.__init__`` /
``grounding.plugin`` / ``source()`` must never import :mod:`judge`.

## The task is deliberately narrow

The judge answers ONE local question: **does quote/span Q entail (fairly support) paraphrase P?**
— an entailment check over two short strings, not the open-ended, hallucination-prone "read the
whole paper and decide if it supports X". A narrow task needs only a small/fast model
(:data:`scientist.grounding.judgments.DEFAULT_JUDGE_MODEL`).

## Pluggable client

The provider is chosen from the ``model_id``: ``claude-*`` → Anthropic (implemented here). The
dispatch table makes adding another provider (e.g. a ``gpt-*`` client) a localized change. Each
client reads its own API key from the environment and returns ``{supported: bool, rationale}``;
absence of a key raises :class:`JudgeUnavailable`, which the refresh step catches to degrade
gracefully (skip judging → the claim stays ``needs-judgment``), never a hard crash.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable


class JudgeUnavailable(RuntimeError):
    """No usable model client (missing API key / SDK / unknown provider). The refresh step
    catches this and leaves the verdict absent so the claim degrades to ``needs-judgment``."""


_SYSTEM = (
    "You are a strict entailment checker for scientific literature citations. You are given a "
    "verbatim QUOTE taken from a paper and a one-sentence PARAPHRASE that a citation attributes "
    "to that paper. Decide ONLY whether the QUOTE, read on its own, fairly supports the "
    "PARAPHRASE — i.e. the paraphrase is a faithful, non-overreaching reading of the quote. "
    "Be conservative: if the paraphrase adds a claim the quote does not state, generalizes "
    "beyond it, flips a hedge into a certainty, or quote-mines, answer not supported. You are "
    "NOT judging whether the paper is correct or whether the finding is true — only whether the "
    "quote backs the paraphrase. Reply with a single JSON object: "
    '{"supported": true|false, "rationale": "<one sentence>"}.'
)


def _build_prompt(span: str, paraphrase: str) -> str:
    return (f"QUOTE:\n\"\"\"\n{span.strip()}\n\"\"\"\n\n"
            f"PARAPHRASE:\n\"\"\"\n{paraphrase.strip()}\n\"\"\"\n\n"
            "Does the QUOTE fairly support the PARAPHRASE? Reply with the JSON object only.")


def _parse_verdict(text: str) -> dict:
    """Pull the ``{supported, rationale}`` object out of a model reply (tolerant of code fences
    / surrounding prose)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise JudgeUnavailable(f"judge returned no JSON verdict: {text[:200]!r}")
    try:
        obj = json.loads(m.group(0))
    except ValueError as exc:
        raise JudgeUnavailable(f"judge verdict was not valid JSON: {text[:200]!r}") from exc
    return {"supported": bool(obj.get("supported")),
            "rationale": str(obj.get("rationale") or "").strip()}


# --------------------------------------------------------------------------- #
# providers — each: (span, paraphrase, model_id) -> {supported, rationale}
# --------------------------------------------------------------------------- #
def _anthropic_judge(span: str, paraphrase: str, model_id: str) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise JudgeUnavailable(
            "ANTHROPIC_API_KEY is not set — the literature support judge needs it. Set it (the "
            "judge runs only in `sci judge`, never in the claims suite), or skip judging: "
            "un-judged claims stay `needs-judgment` (non-blocking).")
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise JudgeUnavailable(
            "the `anthropic` SDK is not installed — `pip install anthropic` (or "
            "`uv run --with anthropic …`) to run `sci judge`.") from exc
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model_id, max_tokens=300, system=_SYSTEM,
        messages=[{"role": "user", "content": _build_prompt(span, paraphrase)}])
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return _parse_verdict("\n".join(parts))


# model_id prefix -> client. Add a provider by registering its prefix + client here.
_PROVIDERS: list[tuple[str, Callable[[str, str, str], dict]]] = [
    ("claude", _anthropic_judge),
]


def judge_entailment(span: str, paraphrase: str, *, model_id: str) -> dict:
    """Judge whether ``span`` fairly supports ``paraphrase`` using ``model_id``.

    Returns ``{supported: bool, rationale: str}``. Raises :class:`JudgeUnavailable` when no
    client can serve ``model_id`` (no key / no SDK / unknown provider) — the refresh step
    catches it and leaves the verdict unset."""
    for prefix, client in _PROVIDERS:
        if model_id.startswith(prefix):
            return client(span, paraphrase, model_id)
    raise JudgeUnavailable(
        f"no judge client for model_id {model_id!r} — register a provider in "
        f"scientist.grounding.judge._PROVIDERS (known prefixes: "
        f"{', '.join(p for p, _ in _PROVIDERS)}).")
