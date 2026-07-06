"""The generic grounded-report engine — build / audit / render (the ``sci report`` core).

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

The engine is domain-generic: it natively resolves ``[claim:]`` / ``[report:]`` / embeds, and
every other citation scheme (a host skill's ``[lit:]`` / ``[litreview:]`` literature layer)
plugs in through the citation-resolver registry (:func:`register_citation`) — so this module
imports no literature/store/library code. Stdlib + PyYAML (``*.csv`` table inlining uses the
stdlib ``csv``); pure, store-free.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from ._ledger import _load_raw, edges, sha256_file
from ._grounding_io import (  # canonical locate+load; GROUNDING_REPORT_NAME re-exported
    GROUNDING_REPORT_NAME,
    claims_of,
    iter_reports,
    load_report,
)

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
# A References / Bibliography / Works-cited section heading (any ATX level), used to detect a
# hand-authored references list so the auto-generated bibliography defers to it (see
# render_markdown). Matched per-line outside code fences, like the citation parse.
_REFS_HEADING_RE = re.compile(r"(?i)^\s{0,3}#{1,6}\s+(references|bibliography|works cited)\s*$")
# An experiment folder id prefix (K1-YYMMXX …), to derive an exp_id from a folder name.
_EXP_ID_RE = re.compile(r"^\s*(K1-[A-Za-z0-9]+)")


# --------------------------------------------------------------------------- #
# citation-resolver registry
# --------------------------------------------------------------------------- #
# The generic report engine natively knows three citation kinds — ``[claim:]`` (a grounded
# internal claim), ``[report:]`` (a lemma sub-report), and ``![..](..)`` embeds — plus all the
# render/quality machinery around them. Every OTHER citation scheme (today: the literature layer's
# ``[lit:]`` and ``[litreview:]``) plugs in through this registry rather than as a hardcoded branch:
# a scheme registers its regex (so :func:`parse_report` discovers its citations) and a resolver (so
# :func:`audit` dispatches them). This is the seam that lets the literature resolvers move out to a
# separate ``research`` skill later — for now they still LIVE in this module (see the literature
# section below) and are merely *registered* at module load instead of inlined into ``audit``.
class _CitationScheme:
    """One pluggable citation scheme. ``scheme`` is its name (e.g. ``lit``/``litreview``);
    ``regex`` finds its inline citations (capture group 1 = the cited id); ``parse_key`` is the key
    under which :func:`parse_report` collects them and :func:`audit` returns their records.

    The hooks (all keyed off the same parsed ids) let a scheme plug into every engine phase:

    * ``resolve(cites, ctx) -> (records, findings, advisories)`` — the audit pass, with the SAME
      record / finding / advisory shapes ``audit`` produced inline before the registry;
    * ``note_text(cid, rctx) -> str`` — the footnote text for one cited id, so :func:`render_markdown`
      can give the scheme its own numbered footnote family (``None`` ⇒ no footnotes for this scheme);
    * ``bib_entries(cids, rctx) -> [(sort_key, entry)]`` — works-cited entries for the scheme's
      distinct cited ids, merged into the auto-generated ``# References`` list (``None`` ⇒ none);
    * ``render_lines(result) -> [str]`` — the human-readable :func:`render_audit` lines for this
      scheme's records (``None`` ⇒ the scheme contributes no audit-output section);
    * ``quantity_cites`` — whether this scheme's citations feed the prose-quantity advisory pool
      (a value attributed via this scheme counts as "asserted by a cited claim")."""
    __slots__ = ("scheme", "regex", "parse_key", "resolve",
                 "note_text", "bib_entries", "render_lines", "quantity_cites")

    def __init__(self, scheme: str, regex: "re.Pattern[str]", parse_key: str, resolve: Any,
                 note_text: Any = None, bib_entries: Any = None, render_lines: Any = None,
                 quantity_cites: bool = False):
        self.scheme = scheme
        self.regex = regex
        self.parse_key = parse_key
        self.resolve = resolve
        self.note_text = note_text
        self.bib_entries = bib_entries
        self.render_lines = render_lines
        self.quantity_cites = quantity_cites


_CITATION_RESOLVERS: dict[str, _CitationScheme] = {}


def register_citation(scheme: str, *, regex: "re.Pattern[str]", parse_key: str,
                      resolve: Any, note_text: Any = None, bib_entries: Any = None,
                      render_lines: Any = None, quantity_cites: bool = False) -> None:
    """Register a pluggable citation scheme (see :class:`_CitationScheme`). Idempotent by
    ``scheme`` name. The built-in ``[claim:]`` / ``[report:]`` / embed kinds are NOT registered
    here — the engine resolves and renders those natively; this is for a downstream citation
    layer (e.g. the literature ``[lit:]`` / ``[litreview:]`` schemes a ``research`` skill adds).
    ``resolve`` is the audit hook; the rest plug the scheme into render / advisories (see the
    class doc)."""
    _CITATION_RESOLVERS[scheme] = _CitationScheme(
        scheme, regex, parse_key, resolve, note_text=note_text, bib_entries=bib_entries,
        render_lines=render_lines, quantity_cites=quantity_cites)


class _AuditContext:
    """The read-only context an :func:`audit` citation resolver needs: the resolved data ``home``,
    the report ``text`` (for front-matter pins), and the prebuilt ``claim_index`` (built once per
    audit and shared across resolvers). A scheme that needs its own per-audit index (e.g. the
    literature layer's paper-claims store) loads it from ``home`` inside its resolver."""
    __slots__ = ("home", "text", "claim_index")

    def __init__(self, home: Path, text: str, claim_index: dict[str, dict[str, Any]]):
        self.home = home
        self.text = text
        self.claim_index = claim_index


class _RenderContext:
    """The read-only context a scheme's render hooks (``note_text`` / ``bib_entries``) get: the
    resolved data ``home``, the report ``text``, and the prebuilt ``claim_index``. ``scratch`` is a
    per-render scratch dict a hook may use to memoize its own index (e.g. a paper-claims store load),
    so it is read once per render rather than per citation."""
    __slots__ = ("home", "text", "claim_index", "scratch")

    def __init__(self, home: Path, text: str, claim_index: dict[str, dict[str, Any]]):
        self.home = home
        self.text = text
        self.claim_index = claim_index
        self.scratch: dict[str, Any] = {}


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


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def _iter_lines_outside_fences(text: str):
    """Yield ``(lineno, line)`` for every 1-based line *outside* a fenced code block
    (```` ``` ```` or ``~~~``), so an example inside a code block isn't parsed/audited.
    The fence delimiter lines themselves are not yielded. Shared by :func:`parse_report`
    and :func:`_paragraphs` so the skip-inside-fences rule lives in one place."""
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
        yield n, line


def parse_report(text: str) -> dict[str, list[dict[str, Any]]]:
    """Pull ``[claim:<id>]`` citations and ``![..](target)`` embeds out of report
    Markdown, each with its 1-based line number. Citations/embeds inside fenced code
    blocks (```` ``` ````) are skipped so an example in a code block isn't audited.

    The engine parses ``[claim:]`` / ``[report:]`` citations and embeds natively; every other
    citation scheme (``[lit:]`` / ``[litreview:]`` and anything else registered via
    :func:`register_citation`) is discovered from :data:`_CITATION_RESOLVERS`, each collected under
    its scheme's ``parse_key``.

    Returns ``{"citations": [{id, line}], "embeds": [{target, line}],
    "report_cites": [...], <scheme parse_key>: [...], ...}``.
    """
    citations: list[dict[str, Any]] = []
    embeds: list[dict[str, Any]] = []
    report_cites: list[dict[str, Any]] = []
    scheme_cites: dict[str, list[dict[str, Any]]] = {
        sch.parse_key: [] for sch in _CITATION_RESOLVERS.values()}
    for n, line in _iter_lines_outside_fences(text):
        for m in _CITE_RE.finditer(line):
            citations.append({"id": m.group(1).strip(), "line": n})
        for m in _REPORT_RE.finditer(line):
            report_cites.append({"id": m.group(1).strip(), "line": n})
        for sch in _CITATION_RESOLVERS.values():
            for m in sch.regex.finditer(line):
                scheme_cites[sch.parse_key].append({"id": m.group(1).strip(), "line": n})
        for m in _EMBED_RE.finditer(line):
            embeds.append({"target": m.group(1).strip(), "line": n})
    return {"citations": citations, "embeds": embeds, "report_cites": report_cites,
            **scheme_cites}


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
        for sch in _CITATION_RESOLVERS.values():   # registered schemes ([lit:]/[litreview:]/…)
            s = sch.regex.sub("", s)
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
    return [(_exp_id_for_dir(exp_dir), report_path)
            for exp_dir, report_path in iter_reports(home)]


def stale_grounding_warnings(home: Path) -> list[dict[str, Any]]:
    """Each ``grounding_report.json`` that is **older** than a claim module it should reflect — a
    cheap ``mtime`` check that the recorded grounding may not match the current claim source.

    ``sci report`` / ``sci litreview`` read the *recorded* grounding report (they never re-run the
    claims suite), so an edited ``claims/test_*.py`` that was never re-run leaves the audit looking
    at stale verdicts/strengths. This compares each experiment's grounding
    report against the newest ``claims/test_*.py`` beside it; a newer module yields a non-blocking
    warning ``{report, modules, detail}`` telling the caller to re-run ``pytest --grounding-out``.
    Warn, never block — the mtime heuristic can false-positive (a no-op edit), so it nudges."""
    out: list[dict[str, Any]] = []
    for exp_dir, report_path in iter_reports(home):
        try:
            gmtime = report_path.stat().st_mtime
        except OSError:
            continue
        claims_dir = exp_dir / "claims"
        if not claims_dir.is_dir():
            continue
        newer: list[Path] = []
        for py in sorted(claims_dir.glob("test_*.py")):
            try:
                if py.stat().st_mtime > gmtime:
                    newer.append(py)
            except OSError:
                continue
        if newer:
            out.append({
                "kind": "stale-grounding",
                "report": _rel_or_name(report_path, home),
                "modules": [_rel_or_name(p, home) for p in newer],
                "detail": "grounding may be stale — re-run pytest --grounding-out "
                          "(a claim module is newer than the recorded grounding report)"})
    return out


# A citation-like ``[<scheme>: …]`` token: a lowercase scheme name, a colon, then a
# bracket-free id. The shape the built-in [claim:]/[report:] citations and any registered
# scheme share — used to spot a cited scheme that has *no* registered resolver.
_SCHEME_TOKEN_RE = re.compile(r"\[([a-z][a-z0-9_-]*)\s*:\s*[^\[\]]+\]")
# URL schemes a bare ``[http://…]`` / ``[mailto:…]`` would otherwise trip on — never a citation.
_URL_SCHEMES = frozenset({"http", "https", "ftp", "ftps", "mailto", "file", "data", "tel"})


def unregistered_scheme_warnings(text: str) -> list[dict[str, Any]]:
    """Each citation-like ``[<scheme>:…]`` token whose ``<scheme>`` is neither a built-in
    (``claim`` / ``report``) nor a registered resolver — surfaced as a NON-blocking warning so
    the citation is not silently dropped from the audit.

    The engine natively resolves ``[claim:]`` / ``[report:]`` and dispatches every other scheme
    through :data:`_CITATION_RESOLVERS`; a token like ``[lit:…]`` with no resolver registered (the
    literature layer lives in the separate ``research`` skill, not installed) would otherwise just
    not match any parser and vanish from the audit with no signal. This is that forward-flag: one
    warning per distinct unknown scheme (at its first line), telling the caller the citation went
    unaudited — and, for the literature schemes, that installing ``research`` registers them.

    Warn, never block (the GROUNDED gate is unchanged): a scheme the host hasn't installed is a
    setup gap, not a broken citation. URL-ish schemes (``http``/``mailto``/…) are excluded so a
    bare bracketed link isn't mistaken for a citation."""
    known = {"claim", "report"} | set(_CITATION_RESOLVERS)
    first_line: dict[str, int] = {}
    for n, line in _iter_lines_outside_fences(text):
        for m in _SCHEME_TOKEN_RE.finditer(line):
            scheme = m.group(1)
            if scheme in known or scheme in _URL_SCHEMES or scheme in first_line:
                continue
            first_line[scheme] = n
    out: list[dict[str, Any]] = []
    for scheme, line in sorted(first_line.items(), key=lambda kv: (kv[1], kv[0])):
        hint = (" — install the research skill to register it"
                if scheme in ("lit", "litreview") else "")
        out.append({
            "kind": "unregistered-scheme", "scheme": scheme, "line": line,
            "detail": f"[{scheme}:…] cited but no resolver is registered for scheme "
                      f"'{scheme}'{hint}; the citation is not audited"})
    return out


def index_claims(home: Path) -> dict[str, dict[str, Any]]:
    """Build ``{full_claim_id -> claim}`` across every experiment's grounding report under
    ``home``. ``full_claim_id`` is ``claim_id_for(exp_id, raw_nodeid)`` so it matches
    ``index-claims`` / ``sci query --kind claim``. Each claim carries its ``exp_id`` and
    the experiment folder ``exp_dir`` (for the downstream report-rooted trace)."""
    index: dict[str, dict[str, Any]] = {}
    for exp_id, report_path in _grounding_reports(home):
        try:
            data = load_report(report_path)
        except (OSError, ValueError):
            continue
        claims = claims_of(data)
        if claims is None:
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
    """The ``[claim:]`` ids in a string, plus the ids of every registered ``quantity_cites``
    scheme (the literature ``[lit:]`` layer) — the citations a quantity can be attributed to.
    Excludes ``[report:]`` and any scheme that does not opt into the quantity pool."""
    ids = [m.group(1).strip() for m in _CITE_RE.finditer(s)]
    for sch in _CITATION_RESOLVERS.values():
        if sch.quantity_cites:
            ids += [m.group(1).strip() for m in sch.regex.finditer(s)]
    return ids


def _resolved_single_cites(ptext: str, claim_index: dict[str, dict[str, Any]]):
    """Yield ``(claim_id, claim)`` for each ``[claim:]`` / ``[lit:]`` citation in ``ptext``
    that resolves to *exactly one* claim. Multi-/zero-candidate cites (missing/ambiguous)
    are skipped — the per-citation audit owns those. Shared by the paragraph-scoped
    advisories, which both walk only the unambiguously-resolved cites of a paragraph."""
    for cid in _cite_ids_in(ptext):
        cands = resolve_citation(cid, claim_index)
        if len(cands) == 1:
            yield cands[0], claim_index[cands[0]]


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """Split into blank-line-separated paragraphs (``(start_line, text)``), skipping
    fenced code blocks. Hard-wrapped lines are joined so a sentence's number and its
    citation share a paragraph even when the line wrap splits them — the unit at which
    the value↔claim association is reliable."""
    paras: list[tuple[int, str]] = []
    cur: list[str] = []
    start: int | None = None
    prev_lineno: int | None = None

    def flush() -> None:
        nonlocal cur, start
        if cur:
            paras.append((start or 1, "\n".join(cur)))
        cur, start = [], None

    for n, line in _iter_lines_outside_fences(text):
        # A gap in line numbers means a fenced code block was skipped between this line
        # and the last — a fence opening ends the current paragraph just as a blank line does.
        if prev_lineno is not None and n > prev_lineno + 1:
            flush()
        prev_lineno = n
        stripped = line.lstrip()
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
        for _cid, claim in _resolved_single_cites(ptext, claim_index):
            global_pool |= _claim_quantities(claim)

    advisories: list[dict[str, Any]] = []
    for start, ptext in paras:
        if _REPORT_RE.search(ptext):
            continue
        local_pool: set[float] = set()
        resolved: list[str] = []
        for cands0, claim in _resolved_single_cites(ptext, claim_index):
            local_pool |= _claim_quantities(claim)
            resolved.append(_short_claim_id(cands0))
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
#
# The all-non-robust gate is the key precision lever and is deliberate: `strength<strong` alone
# is intentionally NOT sufficient to flag a paragraph. A lone `moderate` cite is common (most
# literature claims in a real report are `moderate`) and usually fine; flagging every one of them
# would flood the §3 reviewer and train waive-throughs. So the advisory fires only when EVERY
# backing claim of a bound is non-robust — under-flagging for precision, by design.

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


def _review_note(claim: dict[str, Any]) -> str | None:
    """The human review-note / caveat text for ``claim``, when the grounding report carries one —
    the single most useful thing to put in front of the §3 reviewer, because it is where the
    author's own "all one lab" / scope caveat already lives. In real reports this rides on
    ``reviewed.note`` (the ``@reviewed(note=…)`` / independent-review path); a top-level
    ``note``/``caveats`` is also honored if present. Machine-judged claims carry no note
    (``reviewed`` is null) — they have only the per-source signals, so this returns ``None`` and
    the advisory surfaces strength + the structural deficits instead."""
    rev = claim.get("reviewed") or {}
    for src in (rev.get("note"), claim.get("note"), claim.get("caveats")):
        if isinstance(src, str) and src.strip():
            return " ".join(src.split())
    return None


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
            if any(isinstance(s, dict) and isinstance((tier := s.get("tier")), (int, float))
                   and int(tier) >= _WEAK_LOCATOR_TIER for s in srcs):
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

    Non-blocking: returns ``{kind, line, value, cites, weaknesses, claims, sentence}`` per
    candidate, where ``weaknesses`` maps each cited claim to its named deficits and ``claims``
    carries, per cited claim, the evidence the author had — its ``strength``, its named
    ``weaknesses``, and its review ``note`` text when the grounding report records one
    (``@reviewed(note=…)`` / caveats). Surfacing the note is the point: the motivating failure's
    signal *existed* — the claim's own review note said "all one lab" — but nothing put it in
    front of the reviewer. The tool still cannot judge *which* claims are load-bearing or whether
    a source's measured scope transfers to the use — those stay §3/human judgments; this just
    raises the candidate, now with the strength + note so the reviewer can weigh scope/robustness
    with what the author saw."""
    advisories: list[dict[str, Any]] = []
    for start, ptext in _paragraphs(text):
        if _REPORT_RE.search(ptext):
            continue
        quantities = _quantities(ptext)
        if not quantities:                      # load-bearing proxy: only bounds/magnitudes
            continue
        resolved: list[dict[str, Any]] = []
        all_non_robust = True
        for cands0, claim in _resolved_single_cites(ptext, claim_index):
            weaknesses = claim_robustness_weaknesses(claim)
            short = _short_claim_id(cands0)
            # Surface the evidence the author had: strength + the review note ("all one lab" etc.)
            # when present, so the §3 reviewer can weigh centrality vs. robustness, not just see a
            # bare tag. note is None for machine-judged claims (they carry no review note).
            rec = {"cite": short, "strength": str(claim.get("strength") or "") or None,
                   "weaknesses": weaknesses, "note": _review_note(claim)}
            resolved.append(rec)
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
            "claims": resolved,
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


_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")


def _front_matter(text: str) -> dict[str, Any]:
    """The YAML front-matter mapping of a Markdown file, or ``{}`` if absent/malformed."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        import yaml
        data = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _section_bodies(text: str) -> dict[str, str]:
    """Map each ATX heading's title → its body text (lines until the next heading), stripped.
    Heading titles are kept verbatim; callers match case-insensitively."""
    out: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = _ATX_HEADING_RE.match(line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(2).strip(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


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
    home = _resolve_home(home, rp)
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

    # ---- registered citation schemes (the literature layer: [lit:], [litreview:]) -------- #
    # Everything beyond the engine's native [claim:]/[report:]/embed kinds is dispatched through the
    # citation-resolver registry. Each scheme's resolver returns its records (keyed by the scheme's
    # parse_key — lit_cites / litreview_cites), its blocking findings, and any non-blocking
    # advisories (the bibliometric as_of freshness nudges). Dispatch order is registration order
    # (lit before litreview), so findings/result-keys stay in the pre-registry order.
    ctx = _AuditContext(home, text, claim_index)
    scheme_records: dict[str, list[dict[str, Any]]] = {}
    scheme_advisories: list[dict[str, Any]] = []
    for sch in _CITATION_RESOLVERS.values():
        recs, scheme_findings, scheme_adv = sch.resolve(parsed.get(sch.parse_key, []), ctx)
        scheme_records[sch.parse_key] = recs
        findings.extend(scheme_findings)
        scheme_advisories.extend(scheme_adv)

    # Non-blocking: recall aids for the §3 review subagent, NOT part of the GROUNDED gate.
    # unsupported-quantity catches a number no cited claim asserts; weak-load-bearing catches a
    # bound backed only by non-robust claim(s) — incommensurate evidence the prose may not hedge.
    # scheme_advisories carries the registered resolvers' nudges (bibliometric as_of), kept last to
    # match the pre-registry order (prose + incommensurate + metric).
    advisories = (prose_quantity_advisories(text, claim_index)
                  + incommensurate_evidence_advisories(text, claim_index)
                  + scheme_advisories)

    status = "GROUNDED" if not findings else "BROKEN"
    return {
        "report": _rel_or_name(rp, home),
        "scope": sc["scope"],
        "exp_id": sc["exp_id"],
        "slug": sc["slug"],
        "citations": citations,
        "embeds": embeds,
        "report_cites": report_cites,
        **scheme_records,
        "findings": findings,
        "advisories": advisories,
        # Non-blocking warnings: stale grounding (a claim module newer than its report) +
        # any cited [<scheme>:…] whose resolver isn't registered (e.g. [lit:] with the research
        # skill not installed) — the latter so the citation isn't silently dropped.
        "warnings": stale_grounding_warnings(home) + unregistered_scheme_warnings(text),
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


def _resolve_home(home: Path | None, report_path: Path) -> Path:
    """The data-root for a report: the caller's ``home`` (resolved) when given, else
    inferred from the report path via :func:`_infer_home`. Local to this module — distinct
    from the argparse-based ``cli_utils.resolve_home``, which infers from the CWD/flags."""
    return Path(home).resolve() if home is not None else _infer_home(report_path)


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
    Footnotes are numbered by their rendered *text*, not the cited id, so a note cited more
    than once — or two ids that resolve to identical text (the same fact asserted as separate
    claims) — share ONE numbered footnote cited N times, never a run of duplicate identical
    notes (the content-keyed dedup the old ``endnotes.lua`` did before native footnotes).

    Built-in ``[claim:]`` and ``[report:]`` get their own numbered footnote families; every
    registered citation scheme with a ``note_text`` hook (the literature ``[lit:]`` /
    ``[litreview:]`` layer) gets one too, in registration order. A scheme with a ``bib_entries``
    hook also contributes to an auto-generated ``# References`` works-cited section appended at the
    end (one entry per distinct source, sorted) — skipped when the report already carries its own
    References / Bibliography heading (the author then owns the list). The result is what the render
    is produced from."""
    rp = Path(report_path).resolve()
    home = _resolve_home(home, rp)
    text = rp.read_text(encoding="utf-8")
    claim_index = index_claims(home)
    rctx = _RenderContext(home, text, claim_index)

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

    # Footnote families: the built-in claim/report kinds, then one per registered scheme that
    # supplies a ``note_text`` hook (the literature [lit:]/[litreview:] layer), in registration
    # order. Each family numbers its footnotes by the note's *rendered text*, not by the cited id:
    # a note cited more than once — or two distinct ids that resolve to byte-identical text (the
    # same fact asserted as separate claims) — shares ONE numbered footnote (citation reuse) rather
    # than stacking duplicate identical notes. (Restores the content-keyed dedup the old
    # endnotes.lua did, lost when citations became native per-page footnotes.) Each family's
    # ``order`` is the distinct note texts in first-seen order — both the marker number and the
    # footnote definition derive from it.
    note_schemes = [sch for sch in _CITATION_RESOLVERS.values() if sch.note_text]
    family_specs: list[tuple[str, Any, "re.Pattern[str]"]] = [
        ("claim", _note_text, _CITE_RE), ("report", _report_note_text, _REPORT_RE)]
    for sch in note_schemes:
        family_specs.append(
            (sch.scheme, (lambda cid, s=sch: s.note_text(cid, rctx)), sch.regex))
    families: dict[str, dict[str, Any]] = {
        prefix: {"order": [], "num": {}, "text": fn} for prefix, fn, _ in family_specs}
    # Distinct cited ids (first-seen) per scheme that contributes to the works-cited list — the
    # bibliography hook resolves sources per-id, so it needs the ids, not the rendered note texts.
    scheme_cids: dict[str, list[str]] = {
        sch.scheme: [] for sch in _CITATION_RESOLVERS.values() if sch.bib_entries}

    def _make_footnote_sub(prefix: str):
        """A footnote-marker substitution keyed on the note's rendered *text*: assign each
        distinct text a 1-based, first-seen number and emit ``[^<prefix>-<n>]``. Identical
        text (a re-cite, or two ids that render the same) reuses its number — one note, N cites."""
        fam = families[prefix]
        def _sub(m: re.Match) -> str:
            cid = m.group(1).strip()
            if prefix in scheme_cids and cid not in scheme_cids[prefix]:
                scheme_cids[prefix].append(cid)
            txt = fam["text"](cid)
            if txt not in fam["num"]:
                fam["order"].append(txt)
                fam["num"][txt] = len(fam["order"])
            return f"[^{prefix}-{fam['num'][txt]}]"
        return _sub

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
    # rather than drifting onto the next line. Built-in claim/report first, then the registered
    # schemes in registration order (matching the family + defs order).
    body = text
    for prefix, _fn, pattern in family_specs:
        body = re.sub(r"[^\S\n]*\n?[^\S\n]*" + pattern.pattern,
                      _make_footnote_sub(prefix), body)
    # embeds can span only a line each; substitute per match on the citation-substituted text
    body = _EMBED_RE.sub(_embed_sub, body)

    # One definition per distinct note text, numbered as the markers were (text == note body).
    defs: list[str] = []
    for prefix, _fn, _pattern in family_specs:
        fam = families[prefix]
        defs += [f"[^{prefix}-{fam['num'][t]}]: {t}" for t in fam["order"]]

    def _bibliography() -> str:
        # An auto-generated works-cited list contributed by every registered scheme with a
        # ``bib_entries`` hook (the [lit:] layer). Deferred to the author when the report already
        # has its own References/Bibliography heading. Each hook returns ``(sort_key, entry)`` for
        # its distinct cited ids; entries are merged and sorted across schemes.
        if any(_REFS_HEADING_RE.match(ln) for _, ln in _iter_lines_outside_fences(text)):
            return ""
        entries: list[tuple[tuple, str]] = []
        for sch in _CITATION_RESOLVERS.values():
            if sch.bib_entries:
                entries.extend(sch.bib_entries(scheme_cids.get(sch.scheme, []), rctx))
        if not entries:
            return ""
        entries.sort()
        return "\n\n# References\n\n" + "\n".join(f"- {e}" for _, e in entries) + "\n"

    refs = _bibliography()
    if refs:
        body = body.rstrip() + refs
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
\usepackage{refcount}   % footnotes.lua's \rptnote reuses a note's number via \getrefnumber
\makeatletter
% \rptnote{<id>}{<occ>}{<body>} — per-page citation-footnote dedup (see footnotes.lua):
% reuse this id's active note's number iff that note sits on the current page, else print a
% fresh full footnote and make it the id's new active note. Resolved across xelatex's passes.
\newcommand{\rpt@newnote}[3]{%
  \footnote{#3\label{rpt@lbl@#1@#2}}%
  \expandafter\gdef\csname rpt@active@#1\endcsname{#2}}
\newcommand{\rptnote}[3]{%
  \expandafter\ifx\csname rpt@active@#1\endcsname\relax
    \rpt@newnote{#1}{#2}{#3}%
  \else
    \ifnum\getpagerefnumber{rpt@lbl@#1@\csname rpt@active@#1\endcsname}=\value{page}\relax
      \footnotemark[\getrefnumber{rpt@lbl@#1@\csname rpt@active@#1\endcsname}]%
    \else
      \rpt@newnote{#1}{#2}{#3}%
    \fi
  \fi}
\makeatother
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


# Bundled pandoc filters: layout widens exhibits, references unnumbers the References list
# (all formats, structural AST transforms). footnotes.lua (LaTeX/PDF only, self-guards
# otherwise) collapses a claim/paper cited more than once on the SAME page into one numbered
# footnote cited N times — pandoc otherwise re-emits a note's full text at every reference,
# printing duplicate identical notes — and separates two adjacent footnote marks with a
# superscript comma ("40,41", not "4041"). (endnotes.lua — which instead relocated footnotes
# into an endnotes section — is kept in-tree but no longer wired in.)
_LAYOUT_LUA = Path(__file__).with_name("layout.lua")
_REFERENCES_LUA = Path(__file__).with_name("references.lua")
_FOOTNOTES_LUA = Path(__file__).with_name("footnotes.lua")


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
    home = _resolve_home(home, rp)
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
        # layout.lua widens tables/figures; references.lua unnumbers the References list
        # (both structural AST transforms, every target, no LaTeX package). Citations render
        # as native per-page footnotes; footnotes.lua then collapses same-page repeats to one
        # number and comma-separates adjacent marks (LaTeX only; self-guards otherwise).
        cmd = [pandoc, str(tmp_md), "-o", str(out), "--standalone",
               f"--lua-filter={_LAYOUT_LUA}", f"--lua-filter={_REFERENCES_LUA}",
               f"--lua-filter={_FOOTNOTES_LUA}",
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
_EMBED_MARK = {"current": "✅ current", "drifted": "❌ drifted", "missing": "❌ missing",
               "untracked": "❌ untracked", "dangling": "❌ dangling"}


def render_audit(result: dict[str, Any]) -> str:
    """Human-readable audit output, matching the ``sci trace`` / ``sci reproduce`` style."""
    lines = [f"{result['report']}: {result['status']}  "
             f"(scope: {result['scope']}"
             + (f", {result['exp_id']}" if result.get("exp_id") else "") + ")"]
    for w in result.get("warnings", []):
        mods = ", ".join(w.get("modules", []))
        lines.append(f"  ⚠ {w.get('kind', 'warning')}: {w['detail']}"
                     + (f" [{mods}]" if mods else ""))
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
    # Registered citation schemes render their own audit sections (the literature
    # [lit:]/[litreview:] lines) through their ``render_lines`` hook, in registration order — so the
    # engine's audit output stays literature-agnostic.
    for sch in _CITATION_RESOLVERS.values():
        if sch.render_lines:
            lines.extend(sch.render_lines(result))
    for f in result["findings"]:
        if f.get("detail"):
            loc = f.get("cite") or f.get("embed") or ""
            lines.append(f"  ! {f['kind']} (L{f['line']}) {loc}: {f['detail']}")
    adv = result.get("advisories", [])
    for a in adv:
        cites = ", ".join(a.get("cites", []))
        if a["kind"] == "weak-load-bearing":
            vals = "/".join(_fmt_qty(v) for v in a["value"])
            lines.append(f"  ~ {a['kind']} (L{a['line']}) {cites}: bound {vals} backed only by "
                         f"non-robust claim(s) — weigh centrality vs. robustness (§3)")
            for rec in a.get("claims", []):
                bits = []
                if rec.get("strength"):
                    bits.append(f"strength={rec['strength']}")
                bits.extend(w for w in rec.get("weaknesses", []) if w not in bits)
                tags = ", ".join(dict.fromkeys(bits))
                lines.append(f"      {rec['cite']}: {tags}")
                if rec.get("note"):
                    lines.append(f"        note: {rec['note']}")
        elif a.get("detail") and "value" not in a:
            # A prose-free advisory (the collapsed weak-load-bearing survey summary,
            # included-but-uncited, …) carries its own message; render it verbatim.
            loc = f" {cites}" if cites else ""
            lines.append(f"  ~ {a['kind']} (L{a['line']}){loc}: {a['detail']}")
        else:
            lines.append(f"  ~ {a['kind']} (L{a['line']}) {cites}: {_fmt_qty(a['value'])} not "
                         f"asserted by the cited claim(s) — verify it isn't a derived/mis-transcribed number")
    if adv:
        kinds = Counter(a["kind"] for a in adv)
        summary = ", ".join(f"{n} {k}" for k, n in kinds.items())
        lines.append(f"  advisories: {summary} (non-blocking; for the "
                     f"§3 review subagent — not part of GROUNDED)")
    return "\n".join(lines)


def _fmt_qty(v: float) -> str:
    """Display a normalized percent magnitude compactly (200.0 -> '200%/2×')."""
    s = f"{v:g}%"
    if v >= 100 and v % 50 == 0:
        s += f"/{v / 100:g}×"
    return s
