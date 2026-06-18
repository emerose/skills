"""The report phase — ``sci report`` (build / audit / render).

A *report* is the terminal phase of the pipeline ``raw → data → analysis → claims →
report``. Where a **claim** is one machine-checkable assertion, a **report** is a
human-facing narrative built *from* claims: it collects grounded claims (often fanning
in across experiments), arranges them into an argument, and embeds figures/tables to
make a point. It is for humans — readable, concise, compelling — but holds the same
grounding discipline as the rest of the pipeline:

    no quantitative prose without a backing,

where the backing is an *existing* grounded ``kind=claim`` (or a sha-pinned analysis
artifact). To assert something new the author writes the claim first; reports never
re-litigate grounding.

## What this module mechanizes (and what it doesn't)

Reports are git-diffable Markdown carrying inline ``[claim:<id>]`` citations — the SAME
syntax §3 (the prose↔claims check) defined for ``README.md`` / ``reports/*.md``. ``<id>``
is the stable ``claim_id`` (``<exp>::<test-file>::<node>``) or its trailing node name.
Figures/tables are embedded with Markdown image syntax ``![caption](path)``.

This module does the **mechanical** half of ``sci report``:

* **parse** the report for ``[claim:<id>]`` citations and ``![..](..)`` embeds;
* **validate citations** — each must resolve to a *live, grounded* claim in some
  experiment's ``grounding_report.json`` (the same source §3 / ``sci trace`` use). The
  grounded rule (``outcome ∈ {passed, xpass}`` AND ``strength ∈ {strong, moderate}``)
  decides ``backed`` vs ``weak-backing`` (surfacing the claim's outcome+strength); an
  unresolvable id is ``missing`` and an ambiguous short id is ``ambiguous`` — both fail
  the audit exactly as ``sci trace`` flags a broken chain;
* **validate embeds** — each embedded figure/table must be a *current* sha-pinned
  ``analysis/`` artifact recorded in some experiment's (or the program's) ledger: a
  drifted, missing, or untracked (ad-hoc) graphic fails;
* **render** — assemble a self-contained Markdown (citations → footnoted references,
  ``*.csv`` table embeds inlined as Markdown tables, figure paths absolutised) that a
  toolchain (pandoc) turns into the primary deliverable, a PDF.

The **semantic** judgment — "is every quantitative sentence actually cited / on-topic /
not over-reaching" — stays the §3 semantic-pass discipline of the authoring agent, NOT a
regex assertion-detector. ``sci report`` mechanizes citation + artifact resolution and
render; it does not reintroduce prose assertion-detection.

Stdlib + PyYAML (pandas only for ``*.csv`` table inlining); pure, store-free — like
:mod:`provenance.trace` / :mod:`provenance.reproduce`.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import _load_raw, edges, sha256_file

GROUNDING_REPORT_NAME = "grounding_report.json"

# A claim is *grounded* (a valid backing) only when its outcome is a clean pass AND its
# strength is at least moderate — the identical rule §3 / index-claims / sci trace apply.
GROUNDED_OUTCOMES = {"passed", "xpass"}
GROUNDED_STRENGTHS = {"strong", "moderate"}

# Inline citation: [claim:<id>]. <id> may be a full claim_id (a::b::c), a <file>::<node>
# pair, or a bare node name — optionally parametrized, e.g. test_x[100]. Allow one level
# of square brackets inside so a parametrized nodeid survives the match.
_CITE_RE = re.compile(r"\[claim:\s*([^\[\]]+(?:\[[^\]]*\])?)\s*\]")
# Markdown image embed: ![alt](target "optional title"). Captures the target path.
_EMBED_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
# Inline report citation: [report:<id>] — grounds a report on another (a "lemma" sub-report).
# <id> is <exp-or-program>::<slug> (e.g. program::target-dosage-window) or a bare <slug>.
_REPORT_RE = re.compile(r"\[report:\s*([^\[\]]+?)\s*\]")
# Inline literature citation: [lit:<id>] — grounds a third-party statement on a *literature*
# claim (kind=literature) that verifies a verbatim quote against a paper in the bibliographer
# library and carries an agent support-review. Epistemically second-class to [claim:].
_LIT_RE = re.compile(r"\[lit:\s*([^\[\]]+?)\s*\]")
# An experiment folder id prefix (K1-YYMMXX …), to derive an exp_id from a folder name.
_EXP_ID_RE = re.compile(r"^\s*(K1-[A-Za-z0-9]+)")

# --------------------------------------------------------------------------- #
# claim_id formatting (kept in sync with store._meta.claim_id_for — replicated
# here so the provenance layer stays store-free, like trace/reproduce)
# --------------------------------------------------------------------------- #
def claim_id_for(exp_id: str, nodeid: str) -> str:
    """A STABLE logical key for a claim: ``<exp_id>::<test-file basename>::<node>``.

    Mirrors :func:`scientist.store._meta.claim_id_for` exactly (a test asserts they agree)
    — replicated here so :mod:`provenance` need not import the store package."""
    head, sep, rest = nodeid.partition("::")
    basename = head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or head
    node = rest if sep else ""
    parts = [p for p in (exp_id, basename, node) if p]
    return "::".join(parts)


def _exp_id_for_dir(folder: Path) -> str:
    """The exp_id for an experiment folder (its ``K1-…`` prefix), or the bare folder name
    (so ``program`` claims key as ``program::…``)."""
    m = _EXP_ID_RE.match(folder.name)
    return m.group(1) if m else folder.name


def _short_claim_id(claim_id: str) -> str:
    """A compact display form of a ``<exp>::<test-file>::<node>`` claim id for an endnote
    citation: drop the test-file component and the ``test_`` node prefix, leaving
    ``<exp>::<node>`` (e.g. ``program::lead_is_deepest_protein_knockdown``). Display only —
    the report source still cites the full, unambiguous id."""
    parts = claim_id.split("::")
    exp, node = parts[0], parts[-1]
    if node.startswith("test_"):
        node = node[len("test_"):]
    return f"{exp}::{node}" if node else exp


def _author_year(src: dict[str, Any]) -> str:
    """An author-year label for a literature source, parsed from its bibliographer citekey
    (``<lastname><year><word>`` → ``Lastname year``); falls back to citekey + recorded year."""
    ck = str(src.get("citekey") or "")
    m = re.match(r"^([a-z]+)(\d{4})", ck)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    yr = str(src.get("year") or "")
    return f"{ck} {yr}".strip() or "source"


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def parse_report(text: str) -> dict[str, list[dict[str, Any]]]:
    """Pull ``[claim:<id>]`` citations and ``![..](target)`` embeds out of report
    Markdown, each with its 1-based line number. Citations/embeds inside fenced code
    blocks (```` ``` ````) are skipped so an example in a code block isn't audited.

    Returns ``{"citations": [{id, line}], "embeds": [{target, line}]}``.
    """
    citations: list[dict[str, Any]] = []
    embeds: list[dict[str, Any]] = []
    report_cites: list[dict[str, Any]] = []
    lit_cites: list[dict[str, Any]] = []
    in_fence = False
    fence_marker = ""
    for n, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif stripped.startswith(fence_marker):
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        for m in _CITE_RE.finditer(line):
            citations.append({"id": m.group(1).strip(), "line": n})
        for m in _REPORT_RE.finditer(line):
            report_cites.append({"id": m.group(1).strip(), "line": n})
        for m in _LIT_RE.finditer(line):
            lit_cites.append({"id": m.group(1).strip(), "line": n})
        for m in _EMBED_RE.finditer(line):
            embeds.append({"target": m.group(1).strip(), "line": n})
    return {"citations": citations, "embeds": embeds, "report_cites": report_cites,
            "lit_cites": lit_cites}


def parse_sections(text: str) -> dict[str, Any]:
    """Mechanically split a report into ``{title, abstract, sections}`` for indexing.

    * ``title`` — the first ``# H1`` (or the leading non-blank line);
    * ``abstract`` — the first prose paragraph after the title, OR the body of a section
      titled *Abstract* / *Summary* if one exists;
    * ``sections`` — ``[{heading, summary}]`` for each ``##``/``###`` heading, ``summary``
      being that section's first non-blank, non-heading line (citations/embeds stripped).
    """
    lines = text.splitlines()
    title = ""
    sections: list[dict[str, str]] = []
    cur: dict[str, Any] | None = None
    abstract = ""
    para: list[str] = []
    saw_title = False

    def _clean(s: str) -> str:
        s = _CITE_RE.sub("", s)
        s = _REPORT_RE.sub("", s)
        s = _LIT_RE.sub("", s)
        s = _EMBED_RE.sub("", s)
        return s.strip()

    for raw in lines:
        line = raw.rstrip()
        h1 = re.match(r"^#\s+(.*)$", line)
        h2 = re.match(r"^#{2,3}\s+(.*)$", line)
        if h1 and not title:
            title = h1.group(1).strip()
            saw_title = True
            continue
        if h2:
            if cur is not None and not cur["summary"]:
                cur["summary"] = _clean(" ".join(para))
            cur = {"heading": h2.group(1).strip(), "summary": ""}
            sections.append(cur)
            para = []
            continue
        body = _clean(line)
        if not body:
            # paragraph boundary: capture the first real paragraph as the abstract
            if saw_title and not abstract and cur is None and para:
                abstract = " ".join(para).strip()
            if cur is not None and not cur["summary"] and para:
                cur["summary"] = " ".join(para).strip()
                para = []
            elif cur is None:
                para = []
            continue
        para.append(body)
    if cur is not None and not cur["summary"] and para:
        cur["summary"] = " ".join(para).strip()
    if saw_title and not abstract:
        # no blank-line-terminated lead paragraph; fall back to the running buffer
        if cur is None and para:
            abstract = " ".join(para).strip()
    # An explicit Abstract/Summary section wins.
    for s in sections:
        if s["heading"].lower() in ("abstract", "summary") and s["summary"]:
            abstract = s["summary"]
            break
    return {"title": title, "abstract": abstract, "sections": sections}


# --------------------------------------------------------------------------- #
# claim + artifact indexes (across every experiment under the data root)
# --------------------------------------------------------------------------- #
def _grounding_reports(home: Path) -> list[tuple[str, Path]]:
    """``(exp_id, grounding_report.json path)`` for every experiment under ``home`` that
    has one (``<child>/analysis/grounding_report.json`` then ``<child>/…``)."""
    out: list[tuple[str, Path]] = []
    if not home.is_dir():
        return out
    for child in sorted(home.iterdir()):
        if not child.is_dir():
            continue
        for cand in (child / "analysis" / GROUNDING_REPORT_NAME, child / GROUNDING_REPORT_NAME):
            if cand.is_file():
                out.append((_exp_id_for_dir(child), cand))
                break
    return out


def index_claims(home: Path) -> dict[str, dict[str, Any]]:
    """Build ``{full_claim_id -> claim}`` across every experiment's grounding report under
    ``home``. ``full_claim_id`` is ``claim_id_for(exp_id, raw_nodeid)`` so it matches
    ``index-claims`` / ``sci query --kind claim``. Each claim carries its ``exp_id`` and
    the experiment folder ``exp_dir`` (for the downstream report-rooted trace)."""
    index: dict[str, dict[str, Any]] = {}
    for exp_id, report_path in _grounding_reports(home):
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        claims = data.get("claims") if isinstance(data, dict) else data
        if not isinstance(claims, list):
            continue
        exp_dir = report_path.parent.parent if report_path.parent.name == "analysis" else report_path.parent
        for c in claims:
            if not isinstance(c, dict):
                continue
            nodeid = c.get("id") or ""
            full = claim_id_for(exp_id, nodeid)
            index[full] = {**c, "exp_id": exp_id, "exp_dir": str(exp_dir), "claim_id": full}
    return index


def resolve_citation(cid: str, index: dict[str, dict[str, Any]]) -> list[str]:
    """Resolve a cited ``<id>`` to matching full claim_ids: exact full-id match wins;
    else a ``<file>::<node>`` suffix or a bare trailing node-name match (which may be
    ambiguous across experiments → caller treats >1 as ``ambiguous``)."""
    if cid in index:
        return [cid]
    tail = cid.split("::")[-1]
    cands = [fid for fid in index
             if fid.endswith("::" + cid) or fid.split("::")[-1] == tail]
    return sorted(set(cands))


def is_grounded(claim: dict[str, Any]) -> bool:
    """The grounded rule: a clean pass at moderate-or-strong evidence."""
    return (str(claim.get("outcome")) in GROUNDED_OUTCOMES
            and str(claim.get("strength")) in GROUNDED_STRENGTHS)


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
      verdict the orchestrating agent records via ``sci judge --record``. ``needs-judgment`` (not
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
                                      "was cached — re-run `sci judge` to re-judge and re-record")
        if any(s.get("judge_status") != "fresh" or "supported" not in s for s in machine):
            return ("needs-judgment", "no cached support verdict yet — run `sci judge --list`, "
                                      "judge whether the span supports the paraphrase, and "
                                      "`sci judge --record`")
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


def index_analysis_artifacts(home: Path) -> dict[str, str | None]:
    """``{repo-relative analysis artifact path -> recorded artifact_sha256}`` across every
    experiment's ledger under ``home`` (including ``program/``). The key is how a report's
    embed is matched to the producing edge; the sha lets the audit flag drift."""
    out: dict[str, str | None] = {}
    if not home.is_dir():
        return out
    for child in sorted(home.iterdir()):
        if not child.is_dir():
            continue
        sidecar = _load_raw(child)
        for e in edges(sidecar, "analysis/"):
            art = str(e.get("artifact", ""))
            if not art:
                continue
            rel = f"{child.name}/{art}"
            out[rel] = e.get("artifact_sha256")
    return out


# --------------------------------------------------------------------------- #
# scope
# --------------------------------------------------------------------------- #
def report_scope(report_path: Path, home: Path) -> dict[str, Any]:
    """Classify a report by where it lives: a cross-experiment report under
    ``program/reports/<slug>/`` (``scope='program'``) or a per-experiment summary under
    ``<exp>/reports/<slug>/`` (``scope='experiment'``, with ``exp_id``). ``slug`` is the
    report-folder name; falls back to the file stem."""
    rp = report_path.resolve()
    try:
        rel_parts = rp.relative_to(home.resolve()).parts
    except ValueError:
        rel_parts = rp.parts
    scope, exp_id, slug = "experiment", None, rp.parent.name or rp.stem
    if rel_parts:
        top = rel_parts[0]
        if top == "program":
            scope = "program"
        else:
            m = _EXP_ID_RE.match(top)
            exp_id = m.group(1) if m else top
    if "reports" in rel_parts:
        i = rel_parts.index("reports")
        if i + 1 < len(rel_parts):
            slug = rel_parts[i + 1]
            if slug.endswith(".md"):
                slug = Path(slug).stem
    return {"scope": scope, "exp_id": exp_id, "slug": slug}


# --------------------------------------------------------------------------- #
# prose quantity advisories (a non-blocking recall aid for the §3 subagent)
# --------------------------------------------------------------------------- #
# NOT an assertion-detector and NOT a gate. A deliberately narrow, advisory pass over
# %/×/fold quantities: it surfaces a number asserted on the same line as a
# [claim:]/[lit:] citation whose cited claim(s) do not themselves contain that value —
# the `derived`/mis-transcribed case the per-citation audit structurally cannot see (see
# review-audit.md §3). Advisories never change GROUNDED/BROKEN; they are the mechanical
# floor the required fresh-context §3 review subagent consumes. Scope is intentionally
# limited to percent/fold magnitudes (the load-bearing quantities in these reports), so
# years / n= / p-values / locus names don't generate noise; widen later if needed.
_PCT_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%")
_FOLD_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:×|x(?![A-Za-z])|-?fold)")
_RANGE_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s*(×|%|x(?![A-Za-z])|-?fold)")


def _to_pct(val: float, unit: str) -> float:
    return val if unit.strip().startswith("%") else val * 100.0


def _quantities(text: str) -> set[float]:
    """The set of %/×/fold magnitudes in ``text``, normalized to percent (2× -> 200)."""
    if not text:
        return set()
    out: set[float] = set()
    for m in _RANGE_RE.finditer(text):
        out.add(_to_pct(float(m.group(1)), m.group(3)))
        out.add(_to_pct(float(m.group(2)), m.group(3)))
    for m in _PCT_RE.finditer(text):
        out.add(float(m.group(1)))
    for m in _FOLD_RE.finditer(text):
        out.add(float(m.group(1)) * 100.0)
    return out


def _numeric_leaves(obj: Any) -> set[float]:
    """Every numeric leaf in a (possibly nested) evidence value, skipping bools."""
    if isinstance(obj, bool):
        return set()
    if isinstance(obj, (int, float)):
        return {float(obj)}
    if isinstance(obj, dict):
        out: set[float] = set()
        for v in obj.values():
            out |= _numeric_leaves(v)
        return out
    if isinstance(obj, (list, tuple)):
        out = set()
        for v in obj:
            out |= _numeric_leaves(v)
        return out
    return set()


def _claim_quantities(claim: dict[str, Any]) -> set[float]:
    """Numbers a claim actually asserts. A *data* claim: its structured ``evidence``
    leaves (already in percent/fold-ish magnitudes) plus %/×/fold numbers in its
    statement. A *literature* claim: its statement + each source quote (its evidence
    holds sources, not values), so the figures it attributes are matched verbatim."""
    nums = _quantities(claim.get("statement") or "")
    ev = claim.get("evidence") or {}
    if isinstance(ev, dict) and "lit_sources" in ev:
        for s in ev.get("lit_sources") or []:
            nums |= _quantities(s.get("quote") or "")
    else:
        nums |= _numeric_leaves(ev)
    return nums


def _qty_close(q: float, pool: set[float]) -> bool:
    """True if some claim number is within rounding distance of ``q`` (15% relative, or
    5 percentage points — loose on purpose: an advisory should under-flag, not flood, so
    a defensible rounding like 2× for a measured 224.8% is *not* surfaced)."""
    return any(abs(q - b) <= max(5.0, 0.15 * abs(b)) for b in pool)


def _cite_ids_in(s: str) -> list[str]:
    """The ``[claim:]`` + ``[lit:]`` ids in a string (not ``[report:]``)."""
    return ([m.group(1).strip() for m in _CITE_RE.finditer(s)]
            + [m.group(1).strip() for m in _LIT_RE.finditer(s)])


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """Split into blank-line-separated paragraphs (``(start_line, text)``), skipping
    fenced code blocks. Hard-wrapped lines are joined so a sentence's number and its
    citation share a paragraph even when the line wrap splits them — the unit at which
    the value↔claim association is reliable."""
    paras: list[tuple[int, str]] = []
    cur: list[str] = []
    start: int | None = None
    in_fence = False
    fence_marker = ""

    def flush() -> None:
        nonlocal cur, start
        if cur:
            paras.append((start or 1, "\n".join(cur)))
        cur, start = [], None

    for n, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
                flush()
            elif stripped.startswith(fence_marker):
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        if not stripped:
            flush()
            continue
        if start is None:
            start = n
        cur.append(line)
    flush()
    return paras


def prose_quantity_advisories(
        text: str, claim_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Surface a %/×/fold quantity in a *cited* paragraph that **no cited claim asserts**
    — the `derived`/mis-transcribed case the per-citation audit can't see. Scoping
    decisions, each tuned against false positives observed on real reports:

    - **paragraph, not line** — a number and its citation routinely land on different
      wrapped lines; line scope flags those spuriously.
    - **report-wide restatement filter** — a number asserted by *some* cited claim
      elsewhere (an abstract/conclusion restating a backed result) is not flagged; only a
      value no cited claim anywhere asserts is surfaced.
    - **skip ``[report:]`` paragraphs** — the value may be supplied by the sub-report,
      which carries no inline number to match.
    - **cited paragraphs only** — an uncited number (an abstract gloss, a table cell, an
      inline-derived figure) is out of scope here; that is the (noisier) uncited-quantity
      advisory's job, deliberately not bundled in.

    Non-blocking: returns ``{kind, line, value, cites, sentence}`` for the §3 subagent."""
    paras = _paragraphs(text)
    global_pool: set[float] = set()
    for _, ptext in paras:
        for cid in _cite_ids_in(ptext):
            cands = resolve_citation(cid, claim_index)
            if len(cands) == 1:
                global_pool |= _claim_quantities(claim_index[cands[0]])

    advisories: list[dict[str, Any]] = []
    for start, ptext in paras:
        if _REPORT_RE.search(ptext):
            continue
        local_pool: set[float] = set()
        resolved: list[str] = []
        for cid in _cite_ids_in(ptext):
            cands = resolve_citation(cid, claim_index)
            if len(cands) == 1:
                local_pool |= _claim_quantities(claim_index[cands[0]])
                resolved.append(_short_claim_id(cands[0]))
        if not resolved:
            continue
        for q in sorted(_quantities(ptext)):
            if _qty_close(q, local_pool) or _qty_close(q, global_pool):
                continue
            advisories.append({
                "kind": "unsupported-quantity",
                "line": start,
                "value": q,
                "cites": resolved,
                "sentence": " ".join(ptext.split())[:240],
            })
    return advisories


# --------------------------------------------------------------------------- #
# incommensurate-evidence advisory (a non-blocking recall aid for the §3 subagent)
# --------------------------------------------------------------------------- #
# Grounding checks *attribution faithfulness* — every number maps to a real claim, every
# quote backs its paraphrase. It does NOT check *evidentiary weight relative to how much a
# conclusion leans on the claim*: a report can audit fully GROUNDED while a central,
# load-bearing bound rests on evidence that is not commensurate with its importance, and the
# prose never says so. That is a judgment call (the tool can't know which claim is
# load-bearing, nor whether the evidence's measured scope transfers to the use) — so this is
# a RECALL AID, not a gate. It raises *candidates*: a quantity/bound in a cited paragraph
# backed ONLY by claim(s) that fall short of robust, with the specific weakness named, for the
# required fresh-context §3 pass to weigh. It never changes GROUNDED/BROKEN.
#
# "Robust" is deliberately broader than "strong strength / multiple groups" — the maintaining
# principle is candor proportional to centrality × (lack of robustness), and non-robust
# includes contested/indirect/secondary/abstract-only/out-of-scope evidence, a tidy bound on
# one study, an analogy doing load-bearing work. The signals below are the ones the grounding
# report actually carries per claim/source; the §3 reviewer judges the rest.
#
# Precision model (mirrors unsupported-quantity): paragraph-scoped, skips [report:] and
# uncited paragraphs, and — the load-bearing proxy — fires ONLY on a paragraph that asserts a
# %/×/fold quantity (a bound), and ONLY when EVERY cited claim backing it is non-robust. A
# quantity also backed by one strong, independent, in-scope claim is not surfaced — the weak
# corroborating cite alongside a strong one is fine. This under-flags on purpose.

# A lit source whose locator is weaker than tier 1 (a paragraph/section chunk, not a sentence).
_WEAK_LOCATOR_TIER = 2


def _independent_groups(claim: dict[str, Any]) -> int | None:
    """How many *independent* groups back a literature claim, or ``None`` for a non-literature
    claim / when it can't be told. Machine-judged claims carry a per-source ``group`` (defaults
    to the citekey) — count the distinct non-empty ones; legacy ``@reviewed`` claims stamp
    ``independent_groups`` directly. ``<=1`` is the "all one lab" signal."""
    if str(claim.get("kind")) != "literature":
        return None
    ev = claim.get("evidence") or {}
    srcs = ev.get("lit_sources") if isinstance(ev, dict) else None
    if isinstance(srcs, list) and srcs:
        groups = {str(s.get("group") or s.get("citekey") or "").strip()
                  for s in srcs if isinstance(s, dict)}
        groups.discard("")
        if groups:
            return len(groups)
    rev = claim.get("reviewed") or {}
    ig = rev.get("independent_groups")
    return int(ig) if isinstance(ig, (int, float)) else None


def claim_robustness_weaknesses(claim: dict[str, Any]) -> list[str]:
    """The robustness deficits a load-bearing use of ``claim`` would need the prose to own —
    the named weaknesses the §3 reviewer weighs against how central the claim is. Empty list ⇒
    nothing the tool can see makes it non-robust (strong, multi-group, direct, primary,
    full-text, in-tier — a use of it needs no special hedge). Each entry is a short tag the
    advisory surfaces; the list is the recall signal, the *judgment* stays human/§3.

    Signals are exactly the fields a ``grounding_report.json`` carries:

    * ``strength<strong`` — moderate/weak evidence (the coarsest, most common signal);
    * ``single-group`` — a literature claim resting on one lab (``independent_groups<=1``) —
      the "all one lab" case, the motivating failure;
    * ``suggestive-source`` — an *indirect* literature source (``test=suggestive``);
    * ``secondary-source`` — a non-primary / relayed source (``primary=False`` — the telephone
      problem);
    * ``abstract-only`` — a source read from the abstract/title, not full text;
    * ``weak-locator`` — a source pinned by a tier-≥2 chunk locator (a paragraph, not a quoted
      sentence);
    * ``interpretive`` / ``external`` — an *interpretation* or a CRO's own conclusion doing
      load-bearing work, rather than a direct measurement.
    """
    weaknesses: list[str] = []
    strength = str(claim.get("strength") or "")
    if strength and strength not in ("strong",):
        weaknesses.append(f"strength={strength}")

    kind = str(claim.get("kind") or "")
    if kind in ("interpretive", "external"):
        weaknesses.append(kind)

    if kind == "literature":
        ig = _independent_groups(claim)
        if ig is not None and ig <= 1:
            weaknesses.append("single-group")
        ev = claim.get("evidence") or {}
        srcs = ev.get("lit_sources") if isinstance(ev, dict) else None
        if isinstance(srcs, list):
            if any(isinstance(s, dict) and str(s.get("test")) == "suggestive" for s in srcs):
                weaknesses.append("suggestive-source")
            if any(isinstance(s, dict) and s.get("primary") is False for s in srcs):
                weaknesses.append("secondary-source")
            if any(isinstance(s, dict) and str(s.get("mode")) in ("abstract", "title")
                   for s in srcs):
                weaknesses.append("abstract-only")
            if any(isinstance(s, dict) and isinstance(s.get("tier"), (int, float))
                   and int(s.get("tier")) >= _WEAK_LOCATOR_TIER for s in srcs):
                weaknesses.append("weak-locator")
    return weaknesses


def incommensurate_evidence_advisories(
        text: str, claim_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Surface a load-bearing *bound* (a %/×/fold quantity in a cited paragraph) backed
    **only** by non-robust claim(s) — a candidate for the candor-proportional-to-centrality
    discipline (see review-audit.md §3 / report.md). The quantity is the load-bearing proxy
    the tool can see; the paragraph is the association unit (a number and its citation share a
    paragraph even across wrapped lines). Fires only when EVERY resolved cited claim in the
    paragraph is non-robust — one strong, in-scope backing clears it. Skips ``[report:]`` and
    uncited paragraphs, like the unsupported-quantity advisory.

    Non-blocking: returns ``{kind, line, value, cites, weaknesses, sentence}`` per candidate,
    where ``weaknesses`` maps each cited claim to its named deficits. The tool cannot judge
    *which* claims are load-bearing or whether a source's measured scope transfers to the use —
    those stay §3/human judgments; this just raises the candidate."""
    advisories: list[dict[str, Any]] = []
    for start, ptext in _paragraphs(text):
        if _REPORT_RE.search(ptext):
            continue
        quantities = _quantities(ptext)
        if not quantities:                      # load-bearing proxy: only bounds/magnitudes
            continue
        resolved: list[dict[str, Any]] = []
        all_non_robust = True
        for cid in _cite_ids_in(ptext):
            cands = resolve_citation(cid, claim_index)
            if len(cands) != 1:                 # missing/ambiguous → the citation audit owns it
                continue
            claim = claim_index[cands[0]]
            weaknesses = claim_robustness_weaknesses(claim)
            resolved.append({"cite": _short_claim_id(cands[0]), "weaknesses": weaknesses})
            if not weaknesses:                  # a robust backing clears the whole paragraph
                all_non_robust = False
        if not resolved or not all_non_robust:
            continue
        advisories.append({
            "kind": "weak-load-bearing",
            "line": start,
            "value": sorted(quantities),
            "cites": [r["cite"] for r in resolved],
            "weaknesses": {r["cite"]: r["weaknesses"] for r in resolved},
            "sentence": " ".join(ptext.split())[:240],
        })
    return advisories


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #
def resolve_report_paths(cid: str, home: Path) -> list[Path]:
    """Resolve a ``[report:<id>]`` citation to report.md path(s). ``<id>`` is
    ``<exp-or-program>::<slug>`` or a bare ``<slug>`` (searched tree-wide). Returns 0
    (missing), 1 (resolved), or >1 (ambiguous) paths."""
    cid = cid.strip()
    if "::" in cid:
        scope_id, slug = cid.split("::", 1)
        if scope_id == "program":
            cand = home / "program" / "reports" / slug / "report.md"
            return [cand] if cand.is_file() else []
        hits = [d / "reports" / slug / "report.md" for d in sorted(home.glob(f"{scope_id}*"))]
        return [h for h in hits if h.is_file()]
    return sorted(home.glob(f"**/reports/{cid}/report.md"))


def audit(report_path: Path, home: Path | None = None,
          _seen: frozenset[str] | None = None) -> dict[str, Any]:
    """Mechanically validate a report's citations, embeds, and report-citations.

    Returns ``{report, scope, exp_id, citations, embeds, report_cites, findings, status}``
    where ``status`` is ``GROUNDED`` (no blocking finding) or ``BROKEN``. Claim-citation
    verdict is ``backed`` / ``weak-backing`` / ``missing`` / ``ambiguous``; embed verdict is
    ``current`` / ``drifted`` / ``missing`` / ``untracked`` / ``dangling``; a ``[report:<id>]``
    grounds on another report (a "lemma") and is ``backed`` only if that report resolves AND
    is itself ``GROUNDED`` (checked recursively, cycle-guarded). Everything but
    ``backed`` / ``current`` is a blocking finding (the *semantic* off-topic check stays
    with the authoring agent — see the module docstring).
    """
    rp = Path(report_path).resolve()
    home = Path(home).resolve() if home is not None else _infer_home(rp)
    seen = (_seen or frozenset()) | {str(rp)}
    text = rp.read_text(encoding="utf-8")
    parsed = parse_report(text)
    sc = report_scope(rp, home)

    claim_index = index_claims(home)
    artifact_index = index_analysis_artifacts(home)

    findings: list[dict[str, Any]] = []

    # ---- citations -------------------------------------------------------- #
    citations: list[dict[str, Any]] = []
    for cit in parsed["citations"]:
        cid, line = cit["id"], cit["line"]
        cands = resolve_citation(cid, claim_index)
        rec: dict[str, Any] = {"id": cid, "line": line}
        if not cands:
            rec["verdict"] = "missing"
            findings.append({"kind": "missing-claim", "line": line, "cite": cid,
                             "detail": "no grounded claim has this id; write the claim first"})
        elif len(cands) > 1:
            rec["verdict"] = "ambiguous"
            rec["candidates"] = cands
            findings.append({"kind": "ambiguous-claim", "line": line, "cite": cid,
                             "detail": f"matches {len(cands)} claims — qualify it: {cands}"})
        else:
            claim = claim_index[cands[0]]
            rec["claim_id"] = cands[0]
            rec["outcome"] = claim.get("outcome")
            rec["strength"] = claim.get("strength")
            rec["statement"] = claim.get("statement")
            if is_grounded(claim):
                rec["verdict"] = "backed"
            else:
                rec["verdict"] = "weak-backing"
                findings.append({"kind": "weak-backing", "line": line, "cite": cands[0],
                                 "outcome": claim.get("outcome"), "strength": claim.get("strength"),
                                 "detail": f"cited claim is {claim.get('outcome')}/"
                                           f"{claim.get('strength')}, not grounded"})
        citations.append(rec)

    # ---- embeds ----------------------------------------------------------- #
    embeds: list[dict[str, Any]] = []
    for emb in parsed["embeds"]:
        target, line = emb["target"], emb["line"]
        rec = {"target": target, "line": line}
        if re.match(r"^[a-z]+://", target):     # remote URL — ungroundable
            rec["verdict"] = "untracked"
            findings.append({"kind": "untracked-embed", "line": line, "embed": target,
                             "detail": "remote/external image; embed a sha-pinned analysis artifact"})
            embeds.append(rec)
            continue
        rel = _repo_rel(rp.parent, target, home)
        rec["rel"] = rel
        recorded_sha = artifact_index.get(rel)
        abs_path = (home / rel)
        if recorded_sha is not None:
            if not abs_path.is_file():
                rec["verdict"] = "missing"
                findings.append({"kind": "missing-embed", "line": line, "embed": rel,
                                 "detail": "recorded analysis artifact absent on disk"})
            elif sha256_file(abs_path) != recorded_sha:
                rec["verdict"] = "drifted"
                findings.append({"kind": "drifted-embed", "line": line, "embed": rel,
                                 "detail": "artifact bytes differ from the recorded sha "
                                           "(re-run the derivation, or re-record)"})
            else:
                rec["verdict"] = "current"
        else:
            # not produced by any analysis edge
            if abs_path.is_file():
                rec["verdict"] = "untracked"
                findings.append({"kind": "untracked-embed", "line": line, "embed": rel,
                                 "detail": "on disk but no analysis edge records it — produce it "
                                           "via a derivation so it is sha-pinned"})
            else:
                rec["verdict"] = "dangling"
                findings.append({"kind": "dangling-embed", "line": line, "embed": rel,
                                 "detail": "not a recorded analysis artifact and not on disk"})
        embeds.append(rec)

    # ---- report citations (grounding on another report / "lemma") ---------- #
    report_cites: list[dict[str, Any]] = []
    for rc in parsed.get("report_cites", []):
        cid, line = rc["id"], rc["line"]
        rec = {"id": cid, "line": line}
        paths = resolve_report_paths(cid, home)
        if not paths:
            rec["verdict"] = "missing"
            findings.append({"kind": "missing-report", "line": line, "cite": cid,
                             "detail": "no report with this id; write the lemma report first"})
        elif len(paths) > 1:
            rec["verdict"] = "ambiguous"
            findings.append({"kind": "ambiguous-report", "line": line, "cite": cid,
                             "detail": f"matches {len(paths)} reports — qualify with <scope>::<slug>"})
        else:
            target = paths[0].resolve()
            rec["report"] = _rel_or_name(target, home)
            if str(target) in seen:                  # cycle: treat as backed (already on the stack)
                rec["verdict"] = "backed"
            else:
                sub = audit(target, home, _seen=seen)
                rec["sub_status"] = sub["status"]
                if sub["status"] == "GROUNDED":
                    rec["verdict"] = "backed"
                else:
                    rec["verdict"] = "weak-backing"
                    findings.append({"kind": "broken-report-cite", "line": line, "cite": cid,
                                     "detail": "cited report is itself BROKEN — fix it first"})
        report_cites.append(rec)

    # ---- literature citations (grounding on a paper in the bibliographer library) -------- #
    lit_cites: list[dict[str, Any]] = []
    for lc in parsed.get("lit_cites", []):
        cid, line = lc["id"], lc["line"]
        rec = {"id": cid, "line": line}
        cands = resolve_citation(cid, claim_index)
        if not cands:
            rec["verdict"] = "missing"
            findings.append({"kind": "missing-lit", "line": line, "cite": cid,
                             "detail": "no literature claim has this id; write the [lit:] claim first"})
        elif len(cands) > 1:
            rec["verdict"] = "ambiguous"
            rec["candidates"] = cands
            findings.append({"kind": "ambiguous-lit", "line": line, "cite": cid,
                             "detail": f"matches {len(cands)} claims — qualify it: {cands}"})
        else:
            claim = claim_index[cands[0]]
            verdict, detail = lit_verdict(claim)
            rec["claim_id"] = cands[0]
            rec["strength"] = claim.get("strength")
            rec["statement"] = claim.get("statement")
            rec["sources"] = (claim.get("evidence") or {}).get("lit_sources", [])
            rec["reviewed"] = claim.get("reviewed")
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

    # Non-blocking: recall aids for the §3 review subagent, NOT part of the GROUNDED gate.
    # unsupported-quantity catches a number no cited claim asserts; weak-load-bearing catches a
    # bound backed only by non-robust claim(s) — incommensurate evidence the prose may not hedge.
    advisories = (prose_quantity_advisories(text, claim_index)
                  + incommensurate_evidence_advisories(text, claim_index))

    status = "GROUNDED" if not findings else "BROKEN"
    return {
        "report": _rel_or_name(rp, home),
        "scope": sc["scope"],
        "exp_id": sc["exp_id"],
        "slug": sc["slug"],
        "citations": citations,
        "embeds": embeds,
        "report_cites": report_cites,
        "lit_cites": lit_cites,
        "findings": findings,
        "advisories": advisories,
        "status": status,
    }


def _infer_home(report_path: Path) -> Path:
    """Best-effort data-root for a report path: the parent of the top ``program`` or
    ``K1-…`` folder above it; else the report's grandparent."""
    parts = report_path.parts
    for i, p in enumerate(parts):
        if p == "program" or _EXP_ID_RE.match(p):
            return Path(*parts[:i]) if i else report_path.parent
    return report_path.parent.parent if len(report_path.parents) >= 2 else report_path.parent


def _repo_rel(report_dir: Path, target: str, home: Path) -> str:
    """Resolve an embed ``target`` (relative to the report's directory, or absolute) to a
    home-relative POSIX path."""
    p = Path(target)
    ap = p if p.is_absolute() else (report_dir / p)
    try:
        return ap.resolve().relative_to(home.resolve()).as_posix()
    except (ValueError, OSError):
        return ap.as_posix()


def _rel_or_name(path: Path, home: Path) -> str:
    try:
        return path.resolve().relative_to(home.resolve()).as_posix()
    except ValueError:
        return path.name


# --------------------------------------------------------------------------- #
# render — assemble a self-contained Markdown, then (optionally) call pandoc
# --------------------------------------------------------------------------- #
def render_markdown(report_path: Path, home: Path | None = None) -> str:
    """Assemble a self-contained Markdown from the report (pure; no external tools):

    * ``[claim:<id>]`` → a native pandoc **footnote** carrying the cited claim's statement +
      ``[outcome · strength]`` + its ``claim_id``;
    * ``![cap](*.csv)`` → the CSV inlined as a Markdown table (the derived table, embedded);
    * ``![cap](fig)`` → the same image with its path absolutised so pandoc resolves it.

    Citations are native footnotes (hyperlinked, auto-numbered): :func:`render` lets the
    writer typeset them as true bottom-of-page footnotes (native LaTeX ``\\footnote`` for
    PDF, native footnotes for HTML / docx) — locality over a relocated endnotes section.
    The result is what the render is produced from."""
    rp = Path(report_path).resolve()
    home = Path(home).resolve() if home is not None else _infer_home(rp)
    text = rp.read_text(encoding="utf-8")
    claim_index = index_claims(home)

    order: list[str] = []
    num: dict[str, int] = {}           # cid -> 1-based index, first-seen order
    rorder: list[str] = []
    rnum: dict[str, int] = {}          # report-cite id -> 1-based index
    lorder: list[str] = []
    lnum: dict[str, int] = {}          # lit-cite id -> 1-based index

    def _cite_sub(m: re.Match) -> str:
        cid = m.group(1).strip()
        if cid not in num:
            order.append(cid)
            num[cid] = len(order)
        return f"[^claim-{num[cid]}]"

    def _report_sub(m: re.Match) -> str:
        cid = m.group(1).strip()
        if cid not in rnum:
            rorder.append(cid)
            rnum[cid] = len(rorder)
        return f"[^report-{rnum[cid]}]"

    def _lit_sub(m: re.Match) -> str:
        cid = m.group(1).strip()
        if cid not in lnum:
            lorder.append(cid)
            lnum[cid] = len(lorder)
        return f"[^lit-{lnum[cid]}]"

    def _embed_sub(m: re.Match) -> str:
        path = m.group(1).strip()
        if re.match(r"^[a-z]+://", path):
            return m.group(0)
        ap = Path(path)
        ap = ap if ap.is_absolute() else (rp.parent / ap)
        if ap.suffix.lower() == ".csv" and ap.is_file():
            return _csv_to_md_table(ap)
        # a figure: rewrite to an absolute path so the renderer finds it
        alt_m = re.match(r"!\[([^\]]*)\]", m.group(0))
        alt = alt_m.group(1) if alt_m else ""
        return f"![{alt}]({ap.resolve().as_posix()})"

    # Bind each note marker to the preceding word: drop any whitespace (incl. a soft line
    # wrap) immediately before a citation, so the superscript attaches like a footnote mark
    # rather than drifting onto the next line.
    body = re.sub(r"[^\S\n]*\n?[^\S\n]*" + _CITE_RE.pattern, _cite_sub, text)
    body = re.sub(r"[^\S\n]*\n?[^\S\n]*" + _REPORT_RE.pattern, _report_sub, body)
    body = re.sub(r"[^\S\n]*\n?[^\S\n]*" + _LIT_RE.pattern, _lit_sub, body)
    # embeds can span only a line each; substitute per match on the citation-substituted text
    body = _EMBED_RE.sub(_embed_sub, body)

    def _report_note_text(cid: str) -> str:
        paths = resolve_report_paths(cid, home)
        if len(paths) == 1:
            title = _report_title(paths[0].read_text(encoding="utf-8")) or cid
            return f"Lemma report: *{title}* — `{cid}`"
        return f"report `{cid}` ({'unresolved' if not paths else 'ambiguous'})"

    def _note_text(cid: str) -> str:
        # A true endnote: the claim's statement reads as the note, followed by a compact
        # claim-id citation. No outcome (a cited claim passed by construction) and no
        # strength (low signal in prose); the id is shortened (drop the test-file and the
        # `test_` node prefix) and set in monospace so it reads as a subdued reference.
        cands = resolve_citation(cid, claim_index)
        if len(cands) == 1:
            c = claim_index[cands[0]]
            stmt = (c.get("statement") or "").strip().replace("\n", " ")
            return f"{stmt} `{_short_claim_id(cands[0])}`"
        return f"claim `{cid}` ({'unresolved' if not cands else 'ambiguous'})"

    def _lit_note_text(cid: str) -> str:
        # Parallel to a data-claim endnote: the claim's statement reads as the note, followed by
        # the supporting papers (author-year) as a subdued citation. No "Literature" label (the
        # author-years make the source obvious) and no strength (low signal in prose) — the
        # quote-pinning and the evidential assessment live in the spec and the audit, not here.
        cands = resolve_citation(cid, claim_index)
        if len(cands) != 1:
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

    defs = [f"[^claim-{num[cid]}]: {_note_text(cid)}" for cid in order]
    defs += [f"[^report-{rnum[cid]}]: {_report_note_text(cid)}" for cid in rorder]
    defs += [f"[^lit-{lnum[cid]}]: {_lit_note_text(cid)}" for cid in lorder]
    if defs:
        # pandoc footnote definitions; the writer typesets them as per-page footnotes
        body = body.rstrip() + "\n\n" + "\n".join(defs) + "\n"
    return body


def _csv_to_md_table(path: Path) -> str:
    """Render a derived ``.csv`` as a GitHub-flavoured Markdown table (pipe-escaped)."""
    import csv as _csv

    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(_csv.reader(fh))
    if not rows:
        return f"_(empty table: {path.name})_"

    def esc(v: str) -> str:
        return str(v).replace("|", "\\|").replace("\n", " ").strip()

    header = rows[0]
    out = ["| " + " | ".join(esc(c) for c in header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"]
    for r in rows[1:]:
        cells = (r + [""] * len(header))[:len(header)]
        out.append("| " + " | ".join(esc(c) for c in cells) + " |")
    return "\n".join(out)


# A restrained, modern house style for the PDF target (xelatex). Headings come out
# sans-serif for free from the KOMA `scrartcl` class; the body is a serif via fontspec
# `mainfont`. This header only adds a thin running header (section · page) + tightened
# rules — it touches no colors, so it is order-independent w.r.t. pandoc's hyperref setup.
# A centered single column with conventional margins (Tufte margins were dropped — an
# empty wide margin is wasted space unless it holds sidenotes, which a citation-dense
# report can't use well). Title block, headings, and the running header/footer all come
# out sans-serif; the body is serif. Margins set via -V geometry:margin in render().
#
# The title block carries the TITLE only: the author byline and the date are blanked
# (`\author{}\date{}`) — the author/date front-matter keys are stripped before pandoc, so
# `\maketitle` would otherwise fall back to `\today`. The date instead rides in the footer
# next to the revision sha (see render()).
_PDF_HEADER_TEX = r"""
% --- modern report style (injected by `sci report`) ---
\usepackage{graphicx}   % layout.lua emits raw \includegraphics, so load it unconditionally
                        % (pandoc only auto-loads graphicx when it still sees an Image)
\usepackage{fancyhdr}
\usepackage{caption}
\captionsetup{font=small,labelfont=bf,justification=raggedright,singlelinecheck=false}
\author{}\date{}        % no byline / no title-block date (date moves to the footer)
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.4pt}
\renewcommand{\footrulewidth}{0pt}
\fancyhead[L]{\footnotesize\sffamily @@RUNNING_TITLE@@}
\fancyhead[R]{\footnotesize\sffamily @@HEAD_RIGHT@@}
\fancyfoot[L]{\scriptsize\sffamily @@FOOT_LEFT@@}
\fancyfoot[R]{\footnotesize\sffamily \thepage}
\setlength{\headheight}{14pt}
"""


def _front_field(text: str, key: str) -> str:
    """A scalar YAML front-matter field (empty if absent). Front matter is a leading
    ``---``-fenced block."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        mt = re.match(rf"\s*{re.escape(key)}\s*:\s*(.+?)\s*$", line)
        if mt:
            return mt.group(1).strip().strip("\"'")
    return ""


def _report_title(text: str) -> str:
    """The report's YAML front-matter ``title`` — used for the running header."""
    return _front_field(text, "title")


def _strip_front_matter_keys(md: str, keys: tuple[str, ...]) -> str:
    """Drop ``keys`` (scalar, single-line) from a leading ``---``-fenced YAML block so
    pandoc never puts them in the title block — used to suppress the ``author`` byline and
    the title-block ``date`` (the source report keeps the keys on disk; only the rendered
    Markdown sees them removed). A no-op when there is no front matter."""
    m = re.match(r"^---\n(.*?)\n---\n", md, re.DOTALL)
    if not m:
        return md
    kept = [ln for ln in m.group(1).splitlines()
            if not any(re.match(rf"\s*{re.escape(k)}\s*:", ln) for k in keys)]
    return "---\n" + "\n".join(kept) + "\n---\n" + md[m.end():]


def _git_revision(folder: Path, ignore: Path | None = None) -> tuple[str, bool]:
    """``(<short-sha>, dirty)`` for the repo containing ``folder`` — stamps the rendered PDF
    with the source revision. ``dirty`` reflects uncommitted changes to *source*, excluding
    ``ignore`` (the output file we are about to write — otherwise rendering would always mark
    its own PDF dirty). ``("", False)`` when not a git repo / git is unavailable."""
    import shutil
    import subprocess
    git = shutil.which("git")
    if not git:
        return "", False
    try:
        sha = subprocess.run([git, "-C", str(folder), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if sha.returncode != 0 or not sha.stdout.strip():
            return "", False
        top = subprocess.run([git, "-C", str(folder), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        root = Path(top.stdout.strip()) if top.returncode == 0 else None
        ignore_rel = None
        if ignore is not None and root is not None:
            try:
                ignore_rel = ignore.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                ignore_rel = None
        st = subprocess.run([git, "-C", str(folder), "status", "--porcelain"],
                            capture_output=True, text=True, timeout=10)
        dirty = False
        if st.returncode == 0:
            for line in st.stdout.splitlines():
                path = line[3:].strip().strip('"')        # "XY <path>"; rename keeps new name after "->"
                if "->" in path:
                    path = path.split("->")[-1].strip()
                if path and path != ignore_rel:
                    dirty = True
                    break
        return sha.stdout.strip(), dirty
    except (OSError, subprocess.SubprocessError):
        return "", False


def _short_running_title(title: str, limit: int = 60) -> str:
    """A compact running-header form of the title: the part before a colon, truncated."""
    head = title.split(":", 1)[0].strip() or title.strip()
    if len(head) > limit:
        head = head[:limit].rstrip() + "…"
    return head


def _latex_escape(s: str) -> str:
    repl = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
            "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
            "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(repl.get(ch, ch) for ch in s)

# Serif body / sans headings: prefer the named fonts the user asked for (Times /
# Helvetica), then portable equivalents. Probed against the system via `fc-list`, so a
# missing font is skipped rather than failing the xelatex run.
_SERIF_CANDIDATES = ["Times New Roman", "TeX Gyre Termes", "Times", "Liberation Serif",
                     "Georgia", "Nimbus Roman"]
_SANS_CANDIDATES = ["Helvetica Neue", "Helvetica", "TeX Gyre Heros", "Arial",
                    "Liberation Sans", "Nimbus Sans"]
# A modern monospace for inline code / claim ids — deliberately NOT a LaTeX-world font
# (no Latin Modern / Computer Modern Typewriter). Prefer clean coding faces without
# distracting programming ligatures (claim ids are full of `::` / `_`); ligature-heavy
# faces like Fira Code come last.
_MONO_CANDIDATES = ["JetBrains Mono", "Cascadia Mono", "SF Mono", "Source Code Pro",
                    "IBM Plex Mono", "DejaVu Sans Mono", "Menlo", "Roboto Mono",
                    "Monaco", "Fira Mono", "Fira Code"]


def _available_font_families() -> set[str]:
    """The set of font family names xelatex can resolve, via ``fc-list`` (empty if the
    tool is absent — callers then fall back to the LaTeX default font)."""
    import shutil
    import subprocess
    fc = shutil.which("fc-list")
    if not fc:
        return set()
    try:
        out = subprocess.run([fc, ":", "family"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    fams: set[str] = set()
    for line in out.splitlines():
        for fam in line.split(","):
            fams.add(fam.strip())
    return fams


def _pick_font(candidates: list[str], available: set[str]) -> str | None:
    return next((c for c in candidates if c in available), None)


# Bundled pandoc filters (all formats): widen exhibits, and unnumber the References list.
# (endnotes.lua — which relocated footnotes into an endnotes section — is kept in-tree but
# no longer wired in: citations now render as true per-page footnotes.)
_LAYOUT_LUA = Path(__file__).with_name("layout.lua")
_REFERENCES_LUA = Path(__file__).with_name("references.lua")


class RenderError(RuntimeError):
    """A render toolchain (pandoc) is unavailable or failed."""


def render(report_path: Path, out_path: Path, home: Path | None = None,
           *, to: str = "pdf") -> dict[str, Any]:
    """Render the report to ``out_path`` via **pandoc** (the documented toolchain), in
    ``to`` ∈ ``pdf`` / ``html`` / ``docx``. Assembles the self-contained Markdown with
    :func:`render_markdown` first (embeds inlined/absolutised, citations footnoted).

    Returns ``{output, format}``; raises :class:`RenderError` if pandoc is absent or the
    conversion fails (with the install hint)."""
    import shutil
    import subprocess
    import tempfile

    rp = Path(report_path).resolve()
    home = Path(home).resolve() if home is not None else _infer_home(rp)
    out = Path(out_path)

    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise RenderError(
            "pandoc not found — it is the report render toolchain. Install it "
            "(macOS: `brew install pandoc`; a PDF target also needs a LaTeX engine, "
            "e.g. `brew install --cask basictex`), or render to a format you have "
            "(`--to html`).")

    md = render_markdown(rp, home)        # citations as native footnotes
    # Drop the author byline + the title-block date: pandoc would otherwise render both in
    # the title block. The keys stay on disk (the source report is untouched); only the
    # Markdown handed to pandoc has them removed. The date re-appears in the PDF footer.
    md = _strip_front_matter_keys(md, ("author", "date"))

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_md = tmp_header = None
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
        tf.write(md)
        tmp_md = Path(tf.name)
    try:
        # layout.lua widens tables/figures; references.lua unnumbers the References list.
        # Both are structural AST transforms (every target, no LaTeX package). Citations
        # are left as native footnotes for the writer to typeset per-page.
        cmd = [pandoc, str(tmp_md), "-o", str(out), "--standalone",
               f"--lua-filter={_LAYOUT_LUA}", f"--lua-filter={_REFERENCES_LUA}",
               f"--resource-path={rp.parent}", f"--resource-path={home}"]
        if to == "pdf":
            # modern house style: KOMA `scrartcl` (sans headings), serif body + modern
            # monospace via fontspec, half-space block paragraphs, running header, links.
            src = rp.read_text(encoding="utf-8")
            running = _latex_escape(_short_running_title(_report_title(src)))
            # header-right: a classification stamp (front-matter `classification:`,
            # e.g. CONFIDENTIAL / INTERNAL / DRAFT), in a muted red so it reads as a warning
            classification = _front_field(src, "classification")
            head_right = (rf"\textcolor{{red!60!black}}{{\textbf{{{_latex_escape(classification)}}}}}"
                          if classification else "")
            # footer-left: the date (front-matter `date:`, else the render date) next to
            # the source revision, so the rendered PDF is traceable to a commit. A trailing
            # asterisk (rather than "-dirty") marks an uncommitted tree — unobtrusive and
            # legible to a non-technical reader.
            import datetime
            sha, dirty = _git_revision(rp.parent, ignore=out)
            date = _front_field(src, "date") or datetime.date.today().isoformat()
            foot_bits = [_latex_escape(date)] if date else []
            if sha:
                foot_bits.append(rf"rev~\texttt{{{_latex_escape(sha)}}}{'*' if dirty else ''}")
            foot_left = r"~~\textperiodcentered~~".join(foot_bits)
            header_tex = (_PDF_HEADER_TEX
                          .replace("@@RUNNING_TITLE@@", running)
                          .replace("@@HEAD_RIGHT@@", head_right)
                          .replace("@@FOOT_LEFT@@", foot_left))
            with tempfile.NamedTemporaryFile("w", suffix=".tex", delete=False,
                                             encoding="utf-8") as hf:
                hf.write(header_tex)
                tmp_header = Path(hf.name)
            fams = _available_font_families()
            serif = _pick_font(_SERIF_CANDIDATES, fams)
            sans = _pick_font(_SANS_CANDIDATES, fams)
            mono = _pick_font(_MONO_CANDIDATES, fams)
            cmd += [
                "--pdf-engine=xelatex",
                "-V", "documentclass=scrartcl",
                "-V", "classoption=parskip=half",
                "-V", "geometry:margin=1in",
                "-V", "fontsize=11pt",
                "-V", "linestretch=1.12",
                "-V", "colorlinks=true", "-V", "linkcolor=teal",
                "-V", "urlcolor=teal", "-V", "toccolor=teal",
                "--include-in-header", str(tmp_header),
            ]
            if serif:
                cmd += ["-V", f"mainfont={serif}"]
            if sans:
                cmd += ["-V", f"sansfont={sans}"]
            if mono:
                # smaller so a long claim_id fits the measure; modern coding face
                cmd += ["-V", f"monofont={mono}", "-V", "monofontoptions=Scale=0.85"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RenderError(f"pandoc failed ({proc.returncode}):\n{proc.stderr.strip()}")
    finally:
        tmp_md.unlink(missing_ok=True)
        if tmp_header is not None:
            tmp_header.unlink(missing_ok=True)
    return {"output": str(out), "format": to}


# --------------------------------------------------------------------------- #
# rendering (human-readable audit)
# --------------------------------------------------------------------------- #
_CITE_MARK = {"backed": "✅ backed", "weak-backing": "⚠️ weak-backing",
              "missing": "❌ missing", "ambiguous": "❌ ambiguous"}
_LIT_MARK = {"backed": "✅ backed", "needs-review": "❌ needs-review",
             "needs-judgment": "❌ needs-judgment (run `sci judge`)",
             "stale-judgment": "❌ stale-judgment (re-run `sci judge`)",
             "over-strength": "❌ over-strength (exceeds locator ceiling)",
             "unsupported": "❌ unsupported", "broken": "❌ broken (quote absent)",
             "wrong-kind": "❌ wrong-kind", "missing": "❌ missing", "ambiguous": "❌ ambiguous",
             "stale-review": "❌ stale-review (paper text changed)"}
_EMBED_MARK = {"current": "✅ current", "drifted": "❌ drifted", "missing": "❌ missing",
               "untracked": "❌ untracked", "dangling": "❌ dangling"}


def render_audit(result: dict[str, Any]) -> str:
    """Human-readable audit output, matching the ``sci trace`` / ``sci reproduce`` style."""
    lines = [f"{result['report']}: {result['status']}  "
             f"(scope: {result['scope']}"
             + (f", {result['exp_id']}" if result.get("exp_id") else "") + ")"]
    for c in result["citations"]:
        mark = _CITE_MARK.get(c["verdict"], c["verdict"])
        tail = ""
        if c["verdict"] == "weak-backing":
            tail = f"  ({c.get('outcome')} · {c.get('strength')})"
        elif c["verdict"] == "backed":
            tail = f"  → {c.get('claim_id')}"
        lines.append(f"  [cite L{c['line']}] {c['id']}: {mark}{tail}")
    for e in result["embeds"]:
        mark = _EMBED_MARK.get(e["verdict"], e["verdict"])
        lines.append(f"  [embed L{e['line']}] {e.get('rel') or e['target']}: {mark}")
    for r in result.get("report_cites", []):
        mark = _CITE_MARK.get(r["verdict"], r["verdict"])
        tail = f"  → {r.get('report')}" if r["verdict"] == "backed" and r.get("report") else ""
        lines.append(f"  [report L{r['line']}] {r['id']}: {mark}{tail}")
    lits = result.get("lit_cites", [])
    for lc in lits:
        mark = _LIT_MARK.get(lc["verdict"], lc["verdict"])
        tail = ""
        if lc["verdict"] == "backed":
            nsrc = len(lc.get("sources") or [])
            tail = f"  ({lc.get('strength')}, {nsrc} source{'s' if nsrc != 1 else ''})"
            if lc.get("review_unpinned"):
                tail += f"  [review unpinned — stamp @reviewed(sha=\"{(lc.get('review_sha') or '')[:12]}\")]"
        lines.append(f"  [lit L{lc['line']}] {lc['id']}: {mark}{tail}")
    if lits:
        # A separate literature line so the green badge never launders attribution as
        # data-grounding: show the tier spread across all [lit:] citations.
        from collections import Counter
        tally = Counter(
            (lc.get("strength") if lc["verdict"] == "backed" else lc["verdict"]) for lc in lits)
        spread = ", ".join(f"{n} {k}" for k, n in tally.items())
        lines.append(f"  literature: {len(lits)} cited — {spread}")
    for f in result["findings"]:
        if f.get("detail"):
            loc = f.get("cite") or f.get("embed") or ""
            lines.append(f"  ! {f['kind']} (L{f['line']}) {loc}: {f['detail']}")
    adv = result.get("advisories", [])
    for a in adv:
        cites = ", ".join(a.get("cites", []))
        lines.append(f"  ~ {a['kind']} (L{a['line']}) {cites}: {_fmt_qty(a['value'])} not "
                     f"asserted by the cited claim(s) — verify it isn't a derived/mis-transcribed number")
    if adv:
        lines.append(f"  advisories: {len(adv)} unsupported-quantity (non-blocking; for the "
                     f"§3 review subagent — not part of GROUNDED)")
    return "\n".join(lines)


def _fmt_qty(v: float) -> str:
    """Display a normalized percent magnitude compactly (200.0 -> '200%/2×')."""
    s = f"{v:g}%"
    if v >= 100 and v % 50 == 0:
        s += f"/{v / 100:g}×"
    return s
