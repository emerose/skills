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
"""
from __future__ import annotations

import builtins
import contextvars
import hashlib
import io
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..provenance import record_provenance as _record_provenance
from ..labfiles import read_docx_text, read_pdf_text, read_pptx_text
# Pure, offline cache of literature support verdicts. Safe on the pytest path (stdlib only). No
# model lives anywhere in the tool — the verdict is produced by the orchestrating agent and
# recorded via `sci judge --record`; this path only READS the cache. See judgments.py.
from .judgments import (JudgmentCache, JUDGMENT_CACHE_NAME,
                        evidence_sha as _evidence_sha)

__all__ = [
    "load", "data", "doc", "evidence", "uses", "cross", "record",
    "derivation", "Derivation", "DocRef", "UnsupportedDocFormat", "Capture",
    "strength", "caveats", "kind", "reviewed",
    "paper", "source", "converge", "PaperRef", "LiteratureError",
    "current_capture", "registry", "TRACKED_SUFFIXES",
    "DerivationAudit", "audit_derivations", "current_audit",
    "JudgmentCache", "JUDGMENT_CACHE_NAME",
    "set_judgment_cache", "current_judgment_cache",
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


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


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


import re as _re

_INT_LIKE = _re.compile(r"^-?\d+$")


def _preserve_identifier(col, str_col):
    """Keep a column as faithful strings when pandas' numeric inference would corrupt
    identifiers. Fires only when every non-blank value is a plain integer string AND
    inference would alter it — i.e. a leading zero is present (``"01"`` -> ``1``) or the
    column was floated by blank cells (``"73"`` -> ``73.0``, NaN for the blanks). Real
    measurement columns (any decimal point, sign-less floats, clean blank-free integers
    like counts/indices) are left numeric and untouched."""
    import pandas as pd

    if not (pd.api.types.is_integer_dtype(col.dtype) or pd.api.types.is_float_dtype(col.dtype)):
        return col  # already object/string
    nonblank = str_col[str_col != ""]
    if not len(nonblank) or not nonblank.map(lambda v: bool(_INT_LIKE.match(v))).all():
        return col  # has decimals / non-integer text -> a real measurement column
    has_leading_zero = nonblank.map(lambda v: len(v) > 1 and v.lstrip("-").startswith("0")).any()
    has_blanks = (str_col == "").any()
    if has_leading_zero or has_blanks:
        return str_col  # identifier-like; keep the exact text
    return col          # clean blank-free integers (counts, indices) stay numeric


class UnsupportedDocFormat(ValueError):
    """Raised by :meth:`DocRef.text` for a suffix no built-in reader handles."""


def _collapse_ws(s: str) -> str:
    """Collapse every run of whitespace to a single space (and strip). External claims
    match *verbatim* phrases, but extractors split a sentence across runs/lines/cells
    (worst in pptx); normalizing both sides makes a short quote match reliably."""
    return " ".join(s.split())


# Unicode dash/hyphen variants that publishers and PDF extractors use interchangeably with
# ASCII "-": en/em dashes, the Unicode hyphen, non-breaking hyphen, minus sign, etc. Folding
# them (plus NFKC, which normalizes ligatures/full-width/compatibility forms) makes a verbatim
# quote match a paper's stored text without the author having to reproduce the exact glyph —
# the single most common reason a real, correct quote fails a naive substring check.
_DASHES = "‐‑‒–—―⁃−﹘﹣－"
_DASH_MAP = {ord(c): "-" for c in _DASHES}


def _fold_match(s: str) -> str:
    """Normalize text for verbatim-quote matching: NFKC-normalize, fold Unicode dashes to
    ASCII ``-``, drop Markdown emphasis markers (``*``/``_`` — the library stores parsed
    Markdown, so a gene name reads ``*Xyz1*``; that's markup, not content), then collapse
    whitespace. Case is preserved (a quote is still verbatim)."""
    import unicodedata
    folded = unicodedata.normalize("NFKC", s).translate(_DASH_MAP).replace("*", "").replace("_", "")
    return _collapse_ws(folded)


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
        (especially in decks). This is the recommended matcher for external claims."""
        hay = self.text()
        if normalize_ws:
            return _collapse_ws(phrase) in _collapse_ws(hay)
        return phrase in hay


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


# --------------------------------------------------------------------------- #
# Literature grounding — verify a quote against a paper in the bibliographer library.
# --------------------------------------------------------------------------- #
# A *literature* claim grounds a third-party statement on one or more papers held in the
# bibliographer library (BIBLIOGRAPHER_HOME). The deterministic, re-runnable part is exactly
# the doc() contract — a verbatim quote must be present in the cited paper's text — except the
# text is read from the library's LOCAL DuckDB (get_by_citekey -> chunks/abstract), not a repo
# PDF. That read is keyless and offline: only the library's *semantic query* path embeds (needs
# a key + network); reading stored chunks/abstract by citekey does not. libkit is imported
# lazily here, so a data claim that never calls paper() never pulls libkit in — the data-claim
# audit stays as light and keyless as before.
class LiteratureError(RuntimeError):
    """A literature source could not be resolved/verified (paper missing, no text, etc.)."""


_PAPER_CACHE: dict[str, "PaperRef"] = {}   # citekey -> PaperRef (per process)
_BIBLIOSTORE = None                         # cached BiblioStore class (sibling-skill import)


def _import_bibliostore():
    global _BIBLIOSTORE
    if _BIBLIOSTORE is not None:
        return _BIBLIOSTORE
    import sys
    try:                                    # already importable (scripts/ on path)
        from _store import BiblioStore      # type: ignore
        _BIBLIOSTORE = BiblioStore
        return BiblioStore
    except Exception:
        pass
    here = Path(__file__).resolve()
    for anc in here.parents:                # walk up to a sibling bibliographer skill
        cand = anc / "bibliographer" / "scripts" / "_store.py"
        if cand.is_file():
            sys.path.insert(0, str(cand.parent))
            from _store import BiblioStore   # type: ignore
            _BIBLIOSTORE = BiblioStore
            return BiblioStore
    raise LiteratureError(
        "can't locate the bibliographer skill's scripts/ to read paper text — install the "
        "bibliographer skill alongside scientist, or put its scripts/ on PYTHONPATH.")


def _load_dotenv_for(key: str) -> None:
    """Populate ``os.environ[key]`` from a .env file if it is not already set (stdlib only).

    Search: cwd, every parent of this module (a repo-root .env), then ``~/.env`` (the
    consolidated location). Real env vars and earlier files win — a later file never
    overrides a value already present. Mirrors the CLIs' ``_load_dotenv`` so a claim run
    under pytest (which never sources a shell profile) still finds BIBLIOGRAPHER_HOME."""
    if os.environ.get(key):
        return
    here = Path(__file__).resolve()
    candidates = [Path.cwd() / ".env", *[p / ".env" for p in here.parents],
                  Path.home() / ".env"]
    seen: set[Path] = set()
    for env_path in candidates:
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
        if os.environ.get(key):     # found it — stop scanning
            return


def _bib_home() -> Path:
    # Lazy + graceful: read the env var, and if unset, fall back to loading ~/.env (or a
    # repo/cwd .env) before erroring — a claim run under pytest never sources a shell
    # profile, so the var would otherwise be missing even though ~/.env defines it. An
    # already-set BIBLIOGRAPHER_HOME always wins (the loader never overrides it).
    h = os.environ.get("BIBLIOGRAPHER_HOME")
    if not h:
        _load_dotenv_for("BIBLIOGRAPHER_HOME")
        h = os.environ.get("BIBLIOGRAPHER_HOME")
    if not h:
        raise LiteratureError(
            "BIBLIOGRAPHER_HOME is not set and no .env defining it was found — literature "
            "claims read the bibliographer library; set BIBLIOGRAPHER_HOME (or put it in "
            "~/.env) before running.")
    return Path(h).expanduser()


@dataclass
class PaperRef:
    """A handle to a bibliographer-library paper, resolved by citekey. ``mode`` is
    ``"fulltext"`` (chunks ingested) or ``"abstract"`` (citation-only stub — only the abstract
    is available to match against). ``sha256`` pins the exact text the quote was checked against,
    so a re-ingest that changes the text invalidates a stamped review."""

    citekey: str
    sha256: str
    mode: str
    title: str = ""
    year: str = ""
    doi: str = ""
    is_retracted: bool = False
    credibility: dict = field(default_factory=dict, repr=False, compare=False)
    text: str = field(default="", repr=False, compare=False)
    document_id: str = field(default="", repr=False, compare=False)

    def __str__(self) -> str:
        return f"{self.citekey}@{self.sha256[:12]}({self.mode})"

    def contains(self, phrase: str, *, normalize_ws: bool = True) -> bool:
        """Verbatim-match ``phrase`` against the paper's stored text. By default normalize both
        sides (NFKC + Unicode-dash fold + whitespace-collapse) so a correct quote isn't defeated
        by an en-dash or a ligature in the extracted text; case is preserved."""
        if normalize_ws:
            return _fold_match(phrase) in _fold_match(self.text)
        return phrase in self.text

    def chunk_text(self, chunk) -> str:
        """The text of one (or several) libkit chunk(s) of this paper — the **tier-2 locator**
        span for a paragraph-spanning fact with no single quotable sentence. ``chunk`` is a chunk
        index (or an iterable of indices, joined in order); libkit already chunks documents and
        ``bib query`` returns chunk ids. Keyless/offline (reads the LOCAL library DuckDB, like
        :func:`_load_paper`). Returns ``""`` for an out-of-range index."""
        if not self.document_id:
            raise LiteratureError(
                f"chunk locator needs full text but {self.citekey} is {self.mode}-only "
                f"(no chunked document) — quote it (tier 1) or `bib fetch {self.citekey}`.")
        idxs = [chunk] if isinstance(chunk, int) else list(chunk)
        import asyncio

        BiblioStore = _import_bibliostore()
        home = _bib_home()

        async def _go():
            store = BiblioStore.open(home)
            try:
                parts = [await store.chunk_text(self.document_id, int(i)) for i in idxs]
            finally:
                await store.close()
            return parts

        return " ".join(p for p in asyncio.run(_go()) if p).strip()


def _credibility_from_rec(rec: dict) -> dict:
    """Flatten a library record's OpenAlex ``metrics`` (+ top-level ``cited_by_count``)
    into display **credibility markers** for a literature source: venue legitimacy
    (DOAJ / Scopus / type) and citation impact (FWCI / percentile / journal h-index),
    plus the ``is_retracted`` flag. These are *advisory context for the reader* — they
    surface on the claim/report but deliberately **do NOT feed into a claim's strength
    or outcome** (popularity is not correctness, and an impact gate would fight the
    cite-primary rule). Only ``is_retracted`` is load-bearing, and via its own check,
    not a score. ``None`` values are dropped."""
    m = rec.get("metrics") or {}
    v = m.get("venue") or {}
    cred = {
        "is_retracted": m.get("is_retracted"),
        "fwci": m.get("fwci"),
        "citation_percentile": m.get("citation_percentile"),
        "cited_by_count": rec.get("cited_by_count"),
        "open_access": m.get("open_access"),
        "work_type": m.get("work_type"),
        "venue_type": v.get("type"),
        "in_doaj": v.get("in_doaj"),
        "indexed_in_scopus": v.get("indexed_in_scopus"),
        "journal_impact_2yr": v.get("impact_2yr"),
        "journal_h_index": v.get("h_index"),
    }
    return {k: val for k, val in cred.items() if val is not None}


def _load_paper(citekey: str) -> PaperRef:
    """Resolve a citekey to a :class:`PaperRef`, reading its text from the LOCAL library.
    Cached per process. Keyless/offline (no embedding)."""
    if citekey in _PAPER_CACHE:
        return _PAPER_CACHE[citekey]
    import asyncio

    BiblioStore = _import_bibliostore()
    home = _bib_home()

    async def _go():
        store = BiblioStore.open(home)
        try:
            rec = await store.get_by_citekey(citekey)
            if rec is None:
                return None
            doc_id = rec.get("document_id")
            if doc_id:
                txt, mode = await store.leading_text(doc_id, chunks=100000), "fulltext"
            else:
                txt, mode = (rec.get("abstract") or ""), "abstract"
            return {"text": txt, "mode": mode, "title": rec.get("title") or "",
                    "year": str(rec.get("year") or ""), "doi": rec.get("doi") or "",
                    "document_id": str(doc_id or ""),
                    "credibility": _credibility_from_rec(rec)}
        finally:
            await store.close()

    res = asyncio.run(_go())
    if res is None:
        raise LiteratureError(
            f"paper({citekey!r}) is not in the bibliographer library — add it "
            f"(`bib add <DOI|PMID>`) or fix the citekey.")
    if not res["text"].strip():
        raise LiteratureError(
            f"paper({citekey!r}) has no readable text (citation-only stub with no abstract) — "
            f"`bib fetch {citekey}` to attach an open-access PDF, then re-run.")
    cred = res["credibility"]
    ref = PaperRef(citekey=citekey, sha256=_sha256(res["text"].encode("utf-8")),
                   mode=res["mode"], title=res["title"], year=res["year"], doi=res["doi"],
                   is_retracted=bool(cred.get("is_retracted")), credibility=cred,
                   text=res["text"], document_id=res.get("document_id", ""))
    _PAPER_CACHE[citekey] = ref
    return ref


def paper(citekey: str, *, allow_retracted: bool = False) -> PaperRef:
    """Resolve a bibliographer-library paper by citekey and record it as provenance. The
    returned :class:`PaperRef` is sha-pinned to the exact text read, so the claim is grounded in
    that content (re-ingest -> new sha -> a stamped review re-validates). Use :func:`source` to
    assert a quote in one call; use ``paper(...).contains(...)`` for a bare check.

    **Retraction integrity check:** if the library record marks the paper retracted (OpenAlex /
    Retraction Watch, refreshed each time it's re-added), this raises :class:`LiteratureError` —
    a literature claim must not ground on retracted work. Pass ``allow_retracted=True`` *only* to
    deliberately discuss the retraction itself. (The flag is as fresh as the last `bib add`/
    enrich; the check stays offline/deterministic by reading the stored value, not the network.)"""
    ref = _load_paper(citekey)
    if ref.is_retracted and not allow_retracted:
        raise LiteratureError(
            f"paper({citekey!r}) is RETRACTED (OpenAlex / Retraction Watch) — a literature claim "
            f"must not ground on retracted work. Re-source the statement from a sound paper, or "
            f"pass allow_retracted=True only to discuss the retraction itself.")
    record("paper", ref.citekey, ref.sha256, via="literature")
    return ref


# Locator ladder → max eligible strength. A source's *tier* is set by HOW precisely it locates the
# supporting text, and the tier caps the claim's strength (the audit enforces the ceiling):
#   tier 1  quote=  + paraphrase= → entailment over two short snippets   (eligible up to "strong")
#   tier 2  chunk=  + paraphrase= → entailment over one libkit chunk span (up to "moderate")
#   tier 3  paraphrase= only      → judge reads the whole document        ("weak" only; costly)
# A bare quote= with no paraphrase= is the LEGACY path: deterministic quote tripwire + a hand
# -stamped @reviewed(support=…), unchanged and not machine-judged (no tier).
# (The tier→strength-ceiling map + its enforcement live in provenance.report, the audit layer.)


def source(citekey: str, *, quote: str | None = None, paraphrase: str | None = None,
           chunk=None, test: str = "direct", system: str = "",
           primary: bool = True, group: str | None = None, allow_retracted: bool = False) -> dict:
    """One source backing a literature claim. Records the source's evidential tags for the report
    and runs the deterministic quote tripwire; with ``paraphrase=`` it also pins the **machine
    support verdict** (see below).

    - ``quote``      — a *verbatim* phrase present in the paper. The deterministic, every-audit
      tripwire (string-in-stored-text): ``AssertionError`` if absent. Required for the legacy path
      and for tier 1.
    - ``paraphrase`` — the claim's reading of the cited span. Opts the source into the
      **re-runnable, cache-pinned entailment check** "does the span fairly support P?": the verdict
      is produced by the orchestrating agent (``sci judge --list`` surfaces the work; a fresh-context
      judge subagent decides; ``sci judge --record`` writes it) and cached; this call merely reads
      the cached, pin-keyed verdict (``(evidence_sha, paraphrase)``) and asserts *supported* when
      present. No model is ever called here — the claims suite stays offline and deterministic. A
      missing/stale verdict is **non-blocking** (the audit reports ``needs-judgment`` /
      ``stale-judgment``; run ``sci judge``).
    - ``chunk``      — a libkit chunk index (or iterable of indices): the **tier-2** locator for a
      paragraph-spanning fact with no single quotable sentence. Used with ``paraphrase=`` (no
      ``quote=``); the judged span is the chunk text.
    - ``test`` / ``system`` / ``primary`` / ``group`` — evidential tags (unchanged); see
      :func:`reviewed` for how directness / primary / independence feed strength.

    The verbatim-quote tripwire and the paper-text sha are deterministic and re-run every audit;
    the *support* judgment is now executable too — a cached ``unsupported`` verdict fails the
    claim on every subsequent run (quote-mining no longer survives)."""
    ref = paper(citekey, allow_retracted=allow_retracted)

    # Deterministic tripwire (tier 1 + legacy): the verbatim quote must be in the stored text.
    if quote is not None and not ref.contains(quote):
        where = "full text" if ref.mode == "fulltext" else "abstract (citation-only — no full text)"
        raise AssertionError(
            f"literature quote not found in {citekey} ({where}):\n  quote: {quote!r}\n"
            f"  -> the paper does not contain this verbatim string; fix the quote, or if it is "
            f"in the body of a citation-only paper, `bib fetch {citekey}` to ingest full text.")

    rec = {"citekey": citekey, "test": test, "system": system,
           "primary": bool(primary), "group": group or citekey, "mode": ref.mode,
           "title": ref.title, "year": ref.year, "doi": ref.doi,
           # Display-only credibility markers (venue legitimacy + citation impact); these
           # surface on the claim/report but never feed strength/outcome — see _credibility_from_rec.
           "credibility": ref.credibility}
    if quote is not None:
        rec["quote"] = quote

    if paraphrase is None:
        # LEGACY path: deterministic quote + hand-stamped @reviewed(support=…). Unchanged.
        if quote is None:
            raise LiteratureError(
                "source() needs quote= (legacy / tier 1) or paraphrase= (machine-judged) — "
                "got neither.")
        _record_source(rec)
        return rec

    # MACHINE-JUDGED path: resolve the span the judge reads + the locator tier.
    rec["paraphrase"] = paraphrase
    if quote is not None:
        span, tier = quote, 1
    elif chunk is not None:
        span = ref.chunk_text(chunk)
        tier = 2
        if not span:
            raise AssertionError(
                f"chunk locator {chunk!r} resolved to empty text in {citekey} — fix the chunk id "
                f"(`bib query` returns chunk ids) or quote a sentence (tier 1).")
        rec["chunk"] = chunk
    else:
        span, tier = ref.text, 3        # tier 3: whole-document; costly, weak only
    rec["tier"] = tier
    rec["span"] = span if tier <= 2 else ""    # tier 1/2 spans are small → carried for the worklist
    esha = _evidence_sha(span)
    rec["evidence_sha"] = esha

    status, entry = "miss", None
    cache = _JUDGMENT_CACHE
    if cache is not None:
        status, entry = cache.lookup(citekey, esha, paraphrase)
    rec["judge_status"] = status        # fresh | stale | miss (read by the audit)
    if status == "fresh" and entry is not None:
        rec["supported"] = bool(entry.get("supported"))
        rec["judge_rationale"] = entry.get("rationale")
        rec["judged_at"] = entry.get("timestamp")
        rec["judged_by"] = entry.get("judge_id")

    _record_source(rec)

    # Assert on the CACHED, pin-keyed verdict (decision: the support judgment is executable).
    # Graceful when absent/stale: a brand-new or re-judged claim stays needs-/stale-judgment
    # (non-blocking) until `sci judge --record` runs — never a hard failure on a cache miss.
    if status == "fresh":
        assert rec.get("supported"), (
            f"literature paraphrase NOT supported by the cited span in {citekey} "
            f"(judged by {rec.get('judged_by')}): {rec.get('judge_rationale')!r}\n"
            f"  paraphrase: {paraphrase!r}\n"
            f"  -> fix the paraphrase to match the span, or re-source the fact.")
    return rec


def _record_source(rec: dict) -> None:
    cap = _CURRENT.get()
    if cap is not None:
        cap.evidence.setdefault("lit_sources", []).append(rec)


def converge(*sources: dict) -> list[dict]:
    """Group the sources backing one fact into a convergence set. Each ``source(...)`` has
    already asserted its quote and recorded its tags; ``converge`` just asserts the set is
    non-empty and returns it, so a multi-source claim reads as
    ``converge(source(...), source(...), ...)``. Strength rises with independent, direct,
    primary sources — judged by the agent in :func:`reviewed`, not computed here."""
    srcs = [s for s in sources if s]
    assert srcs, "converge() needs at least one source"
    return srcs


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
    """``@kind("result|design|external|interpretive|literature")`` — what sort of assertion
    this is. ``literature`` marks a *third-party* claim grounded on a paper in the bibliographer
    library (see :func:`source`/:func:`converge`), not on Kicho data."""
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


# --------------------------------------------------------------------------- #
# Bypass guard — make untracked source reads visible.
# --------------------------------------------------------------------------- #
# While a capture is active we wrap ``pandas.read_csv`` and ``builtins.open`` so that a
# *direct* read of a tracked source file (one not routed through ``load``/``experiments``) is
# still captured and flagged. This guarantees the captured input set is complete: a
# claim can't quietly read a CSV behind the harness's back. We capture-and-flag rather
# than hard-fail, so the grounding report still renders — the reconcile lint surfaces
# the bypass. Reads outside SCIENTIST_HOME (pytest internals, the report file, temp files)
# are ignored so the guard never interferes with the test runner itself.
_guard_installed = False
_orig_open = builtins.open
_orig_read_csv = None


def _data_root() -> Path | None:
    r = os.environ.get("SCIENTIST_HOME")
    return Path(r).resolve() if r else None


def _under_root(p: Path) -> bool:
    root = _data_root()
    if root is None:
        return False
    try:
        p.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _maybe_flag(path, via: str) -> None:
    cap = _CURRENT.get()
    if cap is None:
        return
    try:
        p = Path(path)
    except TypeError:
        return
    if p.suffix.lower() not in TRACKED_SUFFIXES or not _under_root(p):
        return
    if not p.is_file():
        return
    sha = _sha256(p.read_bytes())
    # If load() already recorded this exact path, it's tracked — nothing to flag.
    if any(inp["path"] == str(p) for inp in cap.inputs):
        return
    cap.record("bypass", p, sha, via=f"bypass:{via}")
    cap.bypassed.append(f"{via}: {p}")


def install_guard() -> None:
    """Patch pandas.read_csv + builtins.open to flag untracked tracked-file reads.
    Idempotent; installed by the plugin for the whole session (no-op when no capture
    is active, so it is safe to leave installed)."""
    global _guard_installed, _orig_read_csv
    if _guard_installed:
        return
    import pandas as pd

    _orig_read_csv = pd.read_csv

    def guarded_read_csv(filepath_or_buffer=None, *a, **k):
        # Only path-like first args are real file reads; BytesIO (our load()) is skipped.
        if isinstance(filepath_or_buffer, (str, os.PathLike)):
            _maybe_flag(filepath_or_buffer, "pandas.read_csv")
        return _orig_read_csv(filepath_or_buffer, *a, **k)

    def guarded_open(file, mode="r", *a, **k):
        if "r" in mode and isinstance(file, (str, os.PathLike)):
            _maybe_flag(file, "open")
        return _orig_open(file, mode, *a, **k)

    pd.read_csv = guarded_read_csv
    builtins.open = guarded_open
    _guard_installed = True


# --------------------------------------------------------------------------- #
# Derivation audit — re-run a derivation without touching the recorded artifacts.
# --------------------------------------------------------------------------- #
# When an audit context is active, a `derivation()` opened inside it runs in *audit
# mode*: its `write_table`/`write_fig` write to a scratch directory (never over the
# recorded `analysis/tables|fig/*`), it records NO provenance into experiment.yml, and
# every artifact it produces — plus the inputs its capture saw — is collected on the
# DerivationAudit for the reproduction check (`provenance.reproduce`). This is how the
# claim-time bypass guard is extended to derivations: the capture is live during the
# re-run, so an out-of-`data/` read is flagged exactly as it is for a claim.
@dataclass
class DerivationAudit:
    """Collects the artifacts + captured inputs of every derivation re-run under it.

    ``artifacts`` — one ``{rel, kind, name, bytes}`` per ``write_table``/``write_fig``
    (``rel`` is the experiment-relative ``analysis/tables|fig/<name>`` path, matching the
    ledger's artifact key; ``bytes`` is the regenerated content for comparison).
    ``inputs`` — the merged, de-duplicated capture inputs across all blocks (the basis for
    the reads-only-``data/`` enforcement)."""

    scratch: Path
    artifacts: list[dict] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    _seen_inputs: set = field(default_factory=set)

    def _record_input(self, inp: dict) -> None:
        key = (inp.get("kind"), inp.get("path"))
        if key in self._seen_inputs:
            return
        self._seen_inputs.add(key)
        self.inputs.append(dict(inp))


_AUDIT: contextvars.ContextVar[DerivationAudit | None] = contextvars.ContextVar(
    "derivation_audit", default=None)


def current_audit() -> DerivationAudit | None:
    return _AUDIT.get()


class audit_derivations:
    """Context manager that puts derivations into *audit mode* (see above). Use as::

        with grounding.audit_derivations(scratch_dir) as audit:
            derive_module.main()          # its derivation() re-runs into scratch
        audit.artifacts, audit.inputs     # regenerated outputs + captured reads

    Returns a fresh :class:`DerivationAudit`; nesting is not supported (one audit at a
    time per execution context)."""

    def __init__(self, scratch: Path):
        self.audit = DerivationAudit(scratch=Path(scratch))
        self._tok = None

    def __enter__(self) -> DerivationAudit:
        self._tok = _AUDIT.set(self.audit)
        return self.audit

    def __exit__(self, *exc) -> None:
        _AUDIT.reset(self._tok)


# --------------------------------------------------------------------------- #
# Derivation recorder — analysis provenance, parallel to extraction provenance.
# --------------------------------------------------------------------------- #
class Derivation:
    """Context manager for an analysis derivation.

    Inside the ``with`` block, every table read via ``experiments`` is captured as an input.
    ``write_table``/``write_fig`` write the artifact under ``analysis/`` and record a
    provenance entry (artifact + sha, inputs = the captured data files + the deriving
    recipe) into the experiment's unified ``provenance`` list via
    :func:`provenance.record_provenance` — the SAME ledger writer the extractor's
    ``data/`` edges use, so ``raw -> data -> analysis`` is one DAG in one place.
    """

    def __init__(self, study, recipe):
        self.study = study
        self.exp = Path(study.path)
        self.recipe = Path(recipe).resolve()
        self.cap = Capture(claim_id=f"derive:{study.id}")
        self.entries: list[dict] = []
        self._tok = None
        # If an audit context is active, run in audit mode: write to its scratch dir,
        # record no provenance, and hand the regenerated artifacts + captured inputs to it.
        self.audit = _AUDIT.get()

    def __enter__(self) -> "Derivation":
        install_guard()
        self._tok = _CURRENT.set(self.cap)
        base = (self.audit.scratch if self.audit is not None else self.exp / "analysis")
        self._tables_dir = base / "tables"
        self._fig_dir = base / "fig"
        self._tables_dir.mkdir(parents=True, exist_ok=True)
        self._fig_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *exc) -> None:
        _CURRENT.reset(self._tok)
        if self.audit is not None:
            # Audit mode: surface the captured inputs (for the reads-only-data check);
            # never write provenance into experiment.yml.
            if exc[0] is None:
                for inp in self.cap.inputs:
                    self.audit._record_input(inp)
            return
        if exc[0] is None and self.entries:
            self._write_provenance()

    # --- writers ---
    def write_table(self, name: str, df, **to_csv_kw) -> Path:
        """Write a derived table to ``analysis/tables/<name>`` and record provenance.
        ``index=False`` by default for stable, diffable output. Under an audit context
        the write is redirected to the scratch dir and recorded for comparison instead."""
        out = self._tables_dir / name
        to_csv_kw.setdefault("index", False)
        df.to_csv(out, **to_csv_kw)
        self._after_write(out, "table", name)
        return out

    def write_fig(self, name: str, fig) -> Path:
        """Save a matplotlib figure to ``analysis/fig/<name>`` and record provenance.
        Under an audit context the write is redirected to scratch and recorded instead."""
        out = self._fig_dir / name
        fig.savefig(out, dpi=120, bbox_inches="tight")
        self._after_write(out, "fig", name)
        return out

    def _after_write(self, out: Path, kind: str, name: str) -> None:
        """Record a just-written artifact: into the live audit (re-run, for comparison)
        or as a provenance entry (normal derivation)."""
        if self.audit is not None:
            sub = "tables" if kind == "table" else "fig"
            self.audit.artifacts.append({
                "rel": f"analysis/{sub}/{name}", "kind": kind,
                "name": name, "bytes": out.read_bytes(),
            })
        else:
            self._record_artifact(out)

    def _rel(self, p: Path) -> str:
        # Resolve BOTH sides (realpath) before relative_to: when the data-repo root is
        # reached through a symlink (e.g. macOS /tmp -> /private/var), resolving only the
        # path leaves the two prefixes mismatched and relative_to falls back to an
        # absolute path. Resolving both keeps recorded input paths repo-relative.
        try:
            return str(p.resolve().relative_to(self.exp.parent.resolve()))
        except ValueError:
            return str(p)

    def _record_artifact(self, out: Path) -> None:
        recipe_in = {"path": self._rel(self.recipe), "sha256": _sha256(self.recipe.read_bytes())}
        inputs = [{"path": self._rel(Path(i["path"])), "sha256": i["sha256"]}
                  for i in self.cap.inputs] + [recipe_in]
        self.entries.append({
            "artifact": f"analysis/{out.relative_to(self.exp / 'analysis')}".replace("\\", "/"),
            "artifact_sha256": _sha256(out.read_bytes()),
            "reviewed_at": date.today().isoformat(),
            "inputs": inputs,
        })

    def _write_provenance(self) -> None:
        # Route through the shared ledger writer: it dedups by artifact, preserves
        # entries for OTHER artifacts (data/ extractions, the README review), and
        # writes the deterministic sidecar — identical merge semantics this method
        # used to reimplement, now in one place.
        _record_provenance(self.exp, self.entries, repo_root=self.exp.parent)


def derivation(study, recipe) -> Derivation:
    """Open a :class:`Derivation` for ``study`` whose recipe is ``recipe`` (pass
    ``__file__`` from the derive.py). Use as ``with derivation(k, __file__) as d:``."""
    return Derivation(study, recipe)
