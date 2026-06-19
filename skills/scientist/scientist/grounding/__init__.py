"""scientist.grounding — the claim-grounding + analysis-provenance harness.

This package provides the small runtime that turns plain pytest tests into
*grounded claims* and plain Python functions into *provenance-tracked
derivations*. It owns one thing: a per-run **capture context** that records every
source file read while it is active (its kind, path and sha256), plus the headline
numbers a claim chooses to surface. Everything else (typed table access) lives in
the companion `scientist.experiments` package, which simply calls :func:`record`
whenever it loads a table — so provenance is captured automatically from one tracked
accessor rather than hand-maintained.

Public API (imported by claim specs and derivations):

    load(path, kind=...) / data(...)   tracked CSV loader -> DataFrame(.attrs)
    doc(path) -> DocRef                record a non-table source (PDF/docx/pptx report);
                                       DocRef.text()/.contains() extract + match its prose
    evidence(**kv)                     record headline numbers for the report
    uses(claim_id)                     pull a prior claim's evidence + inputs (transitive)
    derivation(study, recipe)          context for an analysis derivation (writes + records)
    strength(...) / caveats(...) / kind(...)   pytest markers carrying the judgment

The pytest plugin (``scientist.grounding.plugin``, auto-loaded via the ``pytest11``
entry point) wraps every test in a capture, enforces the bypass guard, runs the
reconcile lint, and emits the grounding report.

Module layout (this file re-exports the full public surface so ``from
scientist.grounding import X`` and ``scientist.grounding.X`` keep working for every
existing call site):

    __init__.py        core capture context + tracked loaders (load/doc/evidence/uses) + DocRef + markers
    literature.py      paper()/source()/converge() + PaperRef + LiteratureError (the bibliographer-library grounding)
    bypass_guard.py    the data-root open/read_csv guard (install_guard + helpers)
    derivation.py      Derivation recorder + the audit harness (audit_derivations)
    _text.py           shared text/identifier helpers (_sha256, fold/collapse, _match_phrase, _preserve_identifier)
    normalize.py       the one verbatim-quote normalizer (shared with judgments.py)
    judgments.py       offline literature support-verdict cache
"""
from __future__ import annotations

import contextvars
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..labfiles import read_docx_text, read_pdf_text, read_pptx_text
# Pure, offline cache of literature support verdicts. Safe on the pytest path (stdlib only). No
# model lives anywhere in the tool — the verdict is produced by the orchestrating agent and
# recorded via `sci judge --record`; this path only READS the cache. See judgments.py.
from .judgments import JudgmentCache, JUDGMENT_CACHE_NAME
# Shared text/identifier helpers (leaf module). _collapse_ws/_fold_match are re-exported from
# normalize.py via _text so there is one canonical fold — see normalize.py / _text.py.
from ._text import (_sha256, _collapse_ws, _fold_match, _match_phrase,
                    _preserve_identifier)
# Bypass guard, literature grounding, and the derivation recorder/audit live in their own
# submodules; re-exported below so the public surface is unchanged. These submodules import the
# core primitives (Capture / _CURRENT / record / TRACKED_SUFFIXES / current_judgment_cache)
# lazily (inside functions, via `from . import …`), so this top-of-file import order is safe —
# none of them touches a core name at import time.
from .bypass_guard import install_guard, _data_root, _under_root, _maybe_flag
from .literature import (LiteratureError, PaperRef, paper, source, converge,
                         metric, cited_by,
                         _load_paper, _credibility_from_rec, _import_bibliostore,
                         _load_dotenv_for, _bib_home, _record_source, _record_metric,
                         _PAPER_CACHE, _BIBLIOSTORE)
from .derivation import (Derivation, derivation, DerivationAudit, audit_derivations,
                         current_audit)

__all__ = [
    "load", "data", "doc", "evidence", "uses", "cross", "record",
    "derivation", "Derivation", "DocRef", "UnsupportedDocFormat", "Capture",
    "strength", "caveats", "kind", "reviewed", "must_confront",
    "paper", "source", "converge", "metric", "cited_by", "PaperRef", "LiteratureError",
    "current_capture", "registry", "TRACKED_SUFFIXES",
    "DerivationAudit", "audit_derivations", "current_audit",
    "JudgmentCache", "JUDGMENT_CACHE_NAME",
    "set_judgment_cache", "current_judgment_cache",
    "install_guard",
]


# --------------------------------------------------------------------------- #
# Literature support-verdict cache — read on the pytest path, written by the
# refresh step (`sci judge`). The plugin loads it once per session and sets it
# here; `source(paraphrase=…)` consults it. NEVER calls a model (none exists in the tool).
# --------------------------------------------------------------------------- #
_JUDGMENT_CACHE: "JudgmentCache | None" = None


def set_judgment_cache(cache: "JudgmentCache | None") -> None:
    """Install the literature support-verdict cache for the session (called by the plugin)."""
    global _JUDGMENT_CACHE
    _JUDGMENT_CACHE = cache


def current_judgment_cache() -> "JudgmentCache | None":
    return _JUDGMENT_CACHE

# Source-file kinds we consider "tracked": reading one of these while a capture is
# active is provenance the claim/derivation depends on. The bypass guard watches the
# same set. (.csv = tidy data + derived tables; the rest = raw CRO deliverables a doc
# claim might cite — incl. .pptx/.ppt TC decks, which are often the only narrative source.)
TRACKED_SUFFIXES = {".csv", ".pzfx", ".xlsx", ".xls", ".pdf", ".docx", ".pptx", ".ppt",
                    ".yml", ".yaml"}


# --------------------------------------------------------------------------- #
# Capture context — the heart of automatic provenance.
# --------------------------------------------------------------------------- #
@dataclass
class Capture:
    """Records every tracked source read + every headline number for one claim or
    derivation. A claim's id + its captured inputs + its evidence form a *computed*
    record — never hand-maintained."""

    claim_id: str | None = None
    inputs: list[dict] = field(default_factory=list)   # {kind, path, sha256, via}
    evidence: dict[str, Any] = field(default_factory=dict)
    declared: set[str] = field(default_factory=set)    # fixtures the claim requested
    bypassed: list[str] = field(default_factory=list)  # untracked reads the guard caught
    _seen: set = field(default_factory=set)

    def record(self, kind: str, path, sha: str, via: str = "tracked") -> None:
        key = (kind, str(path))
        if key in self._seen:
            return
        self._seen.add(key)
        self.inputs.append({"kind": kind, "path": str(path), "sha256": sha, "via": via})

    def merge(self, other: "Capture") -> None:
        """Pull another capture's inputs in transitively (used by ``uses``)."""
        for inp in other.inputs:
            self.record(inp["kind"], inp["path"], inp["sha256"], via="uses")


_CURRENT: contextvars.ContextVar[Capture | None] = contextvars.ContextVar(
    "analyst_capture", default=None)


def current_capture() -> Capture | None:
    return _CURRENT.get()


def record(kind: str, path, sha: str, via: str = "tracked") -> None:
    """Record a (kind, path, sha) into the active capture, if any. Called by
    ``experiments`` on every table access and by :func:`load`/:func:`doc` here."""
    cap = _CURRENT.get()
    if cap is not None:
        cap.record(kind, path, sha, via)


# A session-wide registry of completed claim records, keyed by node id. Populated by
# the plugin so ``uses(claim_id)`` can pull a prior claim's evidence + inputs.
registry: dict[str, dict] = {}


# --------------------------------------------------------------------------- #
# Tracked loaders (the API experiments-package + claim bodies call directly).
# --------------------------------------------------------------------------- #
def load(path, kind: str = "data"):
    """Read a CSV into a DataFrame, sha-pin it, and record it as provenance.

    The DataFrame carries ``.attrs["source"]`` and ``.attrs["sha256"]``. Reading is
    done from the file bytes (so the sha is of exactly what was parsed); the parse
    itself goes through a ``BytesIO`` so the bypass guard never double-counts it.

    Identifier-column fidelity: pandas infers an all-numeric column to int/float, which
    silently corrupts identifier columns whose values only look numeric — e.g. identifier columns
    ``"01"``/``"08"`` become ``1``/``8`` (leading zero lost, and ``"01"`` now collides
    with ``"1"``). We guard against that by re-reading the column as faithful strings
    whenever the inferred integer form does not round-trip to the original text; such a
    column is kept as strings so ``row["guide_id"] == "73"`` works and leading zeros survive.
    Genuine measurement columns (floats, clean integers) are unaffected."""
    import pandas as pd

    p = Path(path)
    raw = p.read_bytes()
    sha = _sha256(raw)
    record(kind, p, sha)
    df = pd.read_csv(io.BytesIO(raw))
    str_df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    for col in df.columns:
        df[col] = _preserve_identifier(df[col], str_df[col])
    df.attrs["source"] = str(p)
    df.attrs["sha256"] = sha
    return df


data = load  # spec spells the tracked loader both ways


class UnsupportedDocFormat(ValueError):
    """Raised by :meth:`DocRef.text` for a suffix no built-in reader handles."""


# --- per-format text readers (pure-Python; the [reports] extra) ------------- #
# suffix -> reader. The actual parsers live in `labfiles` (the one document-parsing
# layer, alongside the table readers). Pure-Python formats only; legacy .doc/.ppt
# (which would need LibreOffice) are intentionally absent and raise UnsupportedDocFormat.
# See labfiles.read_*_text for why these are NOT routed through libkit's loaders.
_TEXT_READERS = {
    ".pdf": read_pdf_text,
    ".docx": read_docx_text,
    ".pptx": read_pptx_text,
}

_PRESENTATION_SUFFIXES = {".pptx", ".ppt", ".odp"}


@dataclass
class DocRef:
    """A handle to a non-table source (a CRO report PDF/docx, or a TC .pptx deck)
    recorded as evidence. Returned by :func:`doc` so a claim can quote it and keep the
    citation traceable. :meth:`text`/:meth:`contains` extract and match its prose so
    external claims stop hand-rolling per-format extraction."""

    path: Path
    sha256: str
    _text: str | None = field(default=None, init=False, repr=False, compare=False)

    def __str__(self) -> str:
        return f"{self.path.name}@{self.sha256[:12]}"

    @property
    def is_presentation(self) -> bool:
        """True for slide decks (.pptx/.ppt/.odp). A deck is *weaker* evidence than a
        signed report (summary, rounded numbers, scattered text) — author such external
        claims at ``strength="moderate"`` (max) with a caveat that the source is a deck."""
        return self.path.suffix.lower() in _PRESENTATION_SUFFIXES

    def text(self) -> str:
        """Extract the document's plain text, dispatching on suffix: ``.pdf`` (pdfplumber),
        ``.docx`` (python-docx), ``.pptx`` (python-pptx). Needs the ``[reports]`` extra.
        Cached on the instance so repeated substring checks don't re-parse. Raises
        :class:`UnsupportedDocFormat` for any other suffix (e.g. legacy ``.doc``/``.ppt``)."""
        if self._text is None:
            # Deliberately NOT libkit's loaders (decided, not a stopgap): grounding and
            # embedding are different extraction contracts. Quote-matching needs raw text
            # that is a *pure function of the bytes* — deterministic (a claim re-run must
            # not flip because an extractor changed), verbatim (libkit loaders emit Markdown,
            # which breaks substring matching), and keyless/local (claims run constantly in
            # CI/fan-out with no secrets; libkit's PDF path uploads bytes to Datalab + needs
            # a key, the office path needs `soffice`). The pinned pure-Python readers in
            # `labfiles` satisfy that contract; libkit's structure-rich/OCR/hosted loaders
            # serve the store/embedding side, where those are features, not liabilities.
            reader = _TEXT_READERS.get(self.path.suffix.lower())
            if reader is None:
                raise UnsupportedDocFormat(
                    f"doc().text() can't extract {self.path.suffix!r} ({self.path.name}): "
                    f"supported formats are {', '.join(sorted(_TEXT_READERS))} "
                    f"(install the [reports] extra). Legacy .doc/.ppt and other office "
                    f"formats are not supported.")
            try:
                self._text = reader(self.path)
            except ImportError as exc:
                name = getattr(exc, "name", None) or "a reader"
                raise ImportError(
                    f"{name} is required to read {self.path.suffix} — install the scientist "
                    f"[reports] extra: pip install -e 'skills/scientist[reports]' "
                    f"(or run via: uv run --with-editable 'skills/scientist[reports]' pytest …)") from exc
        return self._text

    def contains(self, phrase: str, *, normalize_ws: bool = True) -> bool:
        """Substring-check ``phrase`` against the extracted :meth:`text`. With
        ``normalize_ws`` (default), collapse whitespace on both sides first — the robust
        way to match a verbatim quote whose extractor split it across runs/lines/cells
        (especially in decks). This is the recommended matcher for external claims.

        Delegates to the shared :func:`_match_phrase` (the single quote matcher both this
        and :meth:`PaperRef.contains` use)."""
        return _match_phrase(phrase, self.text(), normalize_ws=normalize_ws)


def doc(path, kind: str = "doc"):
    """Record a non-table source (a PDF/docx CRO report, or a .pptx TC deck) as a
    provenance input and return a :class:`DocRef`. Use for *external* claims that quote
    a report: the quote is grounded in the bytes of the cited document, sha-pinned like
    any table. Call :meth:`DocRef.contains` (or :meth:`DocRef.text`) to verify the quote."""
    p = Path(path)
    sha = _sha256(p.read_bytes())
    record(kind, p, sha)
    return DocRef(p, sha)


def evidence(**kv) -> None:
    """Record headline numbers for the grounding report (e.g. ``evidence(kd_pct=53)``).
    Kept *out* of the assert so the assertion stays a pure grounding/drift check."""
    cap = _CURRENT.get()
    if cap is not None:
        cap.evidence.update(kv)


def cross(study):
    """Declare an *intentional* cross-experiment dependency. A claim's `experiment`
    fixture covers its home experiment; reading any *other* experiment is flagged by the
    reconcile lint as an accidental cross-read unless declared. Wrap a second study in
    ``cross(...)`` to register it as expected and return it for use:

        from scientist.experiments import k1_000000   # some other experiment
        other = cross(k1_000000)                  # declares the cross-experiment dep
        tbl = other.analysis.some_summary         # ...then read it, captured as usual

    Returns the study unchanged (so it composes inline)."""
    cap = _CURRENT.get()
    code = getattr(study, "id", None)
    if cap is not None and code:
        cap.declared.add(str(code).upper())
    return study


def uses(claim_id: str) -> dict:
    """Compose on another claim: merge its recorded inputs into this capture
    (transitive provenance) and return its evidence dict. The referenced claim must
    have run earlier in the session (pytest collection order). Enables
    cross-experiment / cross-claim composition without re-reading source.

    ``claim_id`` may be a full node id or a bare function name. A bare name can be
    ambiguous across experiments (two may define a claim by the same name); when it is,
    prefer a candidate **in the calling claim's own test file** (the common same-file
    composition case), so a short ``uses("test_x")`` stays robust whether the suite runs
    one experiment or the whole program. For a genuine cross-experiment reference, pass
    a qualified id (``"<file>::test_x"``)."""
    cap = _CURRENT.get()
    rec = registry.get(claim_id)
    if rec is None:
        cand = [k for k in registry
                if k == claim_id or k.endswith("::" + claim_id) or k.split("::")[-1] == claim_id]
        if len(cand) > 1 and cap is not None and cap.claim_id:
            my_file = cap.claim_id.split("::")[0]   # prefer a same-file candidate
            same = [k for k in cand if k.split("::")[0] == my_file]
            if same:
                cand = same
        if len(cand) == 1:
            rec = registry.get(cand[0])
        elif len(cand) > 1:
            raise LookupError(
                f"uses({claim_id!r}) is ambiguous across experiments — qualify it as "
                f"'<file>::{claim_id.split('::')[-1]}'. Candidates: {sorted(cand)}")
    if rec is None:
        raise LookupError(
            f"uses({claim_id!r}): no completed claim with that id has run yet "
            f"(known: {sorted(registry)})")
    if cap is not None:
        for inp in rec["inputs"]:
            cap.record(inp["kind"], inp["path"], inp["sha256"], via="uses")
    return dict(rec.get("evidence", {}))


# --------------------------------------------------------------------------- #
# Markers — the non-binary judgment, kept out of the assert.
# --------------------------------------------------------------------------- #
def _marker(name):
    import pytest
    return getattr(pytest.mark, name)


def strength(level: str):
    """``@strength("strong|moderate|weak|...")`` — how strongly the evidence supports
    the statement. Metadata, not a pass/fail input; edits across git commits are the
    belief-change ledger."""
    return _marker("strength")(level)


def caveats(text: str):
    """``@caveats("...")`` — scope/limits a reader must keep in mind."""
    return _marker("caveats")(text)


def kind(category: str):
    """``@kind("result|design|external|interpretive|literature|bibliometric")`` — what sort of
    assertion this is. ``literature`` marks a *third-party* claim grounded on a quote in a paper in
    the bibliographer library (see :func:`source`/:func:`converge`), not on Kicho data.
    ``bibliometric`` marks a claim *about the literature itself* (e.g. "most-cited") grounded on a
    stored OpenAlex metric via :func:`metric`/:func:`cited_by`, not on a quote — see
    :func:`scientist.grounding.metric`."""
    return _marker("kind")(category)


def reviewed(**verdict):
    """``@reviewed(date=..., by=..., support=True, primary=True, independent_groups=N, note=...)``
    — the agent's one-time *support review* of a literature claim, recorded on the spec.

    The tool check (quote present in the cited paper) is deterministic and re-runs every audit.
    Judging whether the quote actually *supports* the paraphrase, whether the source is the
    *primary* one (not a relay — the telephone problem), and whether the cited papers are
    *independent* groups is irreducibly a reading task: an agent does it once, at authoring, and
    stamps the verdict here. ``support=False`` means the agent judged the paper does **not**
    support the statement → the claim is treated as broken. Re-run the review only when the
    quote or the paper text changes (carry a ``sha=`` of what was reviewed to make that
    checkable). A literature claim with no ``@reviewed`` is *needs-review* and does not back a
    ``[lit:]`` citation. Has no effect on non-literature claims."""
    return _marker("reviewed")(**verdict)


def must_confront(reason: str):
    """``@must_confront("why any report here must address this")`` — mark a literature claim as
    part of a litreview's **must-confront** obligation set: the pivotal, contested, or
    disconfirming assertions any report citing the litreview must reckon with (cite or explicitly
    waive). The ``reason`` is one line on *why* a report must address it.

    This is a litreview's most important neutral judgment — made *before and independent of* any
    thesis, which is what makes the obligation trustworthy. It drives the `[litreview:]` omissions
    audit and the `stale-litreview` staleness boundary (see references/litreview.md). Metadata
    only (no effect on the claim's pass/fail); surfaces as ``must_confront`` in the grounding
    report. Marks the *contested core*, not every on-topic fact."""
    return _marker("must_confront")(reason)
