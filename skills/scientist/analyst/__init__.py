"""analyst — the claim-grounding + analysis-provenance harness.

This package provides the small runtime that turns plain pytest tests into
*grounded claims* and plain Python functions into *provenance-tracked
derivations*. It owns one thing: a per-run **capture context** that records every
source file read while it is active (its kind, path and sha256), plus the headline
numbers a claim chooses to surface. Everything else (typed table access) lives in
the companion `experiments` package, which simply calls :func:`record` whenever it loads
a table — so provenance is captured automatically from one tracked accessor rather
than hand-maintained.

Public API (imported by claim specs and derivations):

    load(path, kind=...) / data(...)   tracked CSV loader -> DataFrame(.attrs)
    doc(path) -> DocRef                record a non-table source (PDF/docx/pptx report);
                                       DocRef.text()/.contains() extract + match its prose
    evidence(**kv)                     record headline numbers for the report
    uses(claim_id)                     pull a prior claim's evidence + inputs (transitive)
    derivation(study, recipe)          context for an analysis derivation (writes + records)
    strength(...) / caveats(...) / kind(...)   pytest markers carrying the judgment

The pytest plugin (``analyst.plugin``, auto-loaded via the ``pytest11`` entry point)
wraps every test in a capture, enforces the bypass guard, runs the reconcile lint,
and emits the grounding report.
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

from provenance import record_provenance as _record_provenance
from labfiles import read_docx_text, read_pdf_text, read_pptx_text

__all__ = [
    "load", "data", "doc", "evidence", "uses", "cross", "record",
    "derivation", "Derivation", "DocRef", "UnsupportedDocFormat", "Capture",
    "strength", "caveats", "kind",
    "current_capture", "registry", "TRACKED_SUFFIXES",
]

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


def cross(study):
    """Declare an *intentional* cross-experiment dependency. A claim's `experiment`
    fixture covers its home experiment; reading any *other* experiment is flagged by the
    reconcile lint as an accidental cross-read unless declared. Wrap a second study in
    ``cross(...)`` to register it as expected and return it for use:

        from experiments import k1_000000        # some other experiment
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
    """``@kind("result|design|external|interpretive")`` — what sort of assertion this is."""
    return _marker("kind")(category)


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

    def __enter__(self) -> "Derivation":
        install_guard()
        self._tok = _CURRENT.set(self.cap)
        (self.exp / "analysis" / "tables").mkdir(parents=True, exist_ok=True)
        (self.exp / "analysis" / "fig").mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *exc) -> None:
        _CURRENT.reset(self._tok)
        if exc[0] is None and self.entries:
            self._write_provenance()

    # --- writers ---
    def write_table(self, name: str, df, **to_csv_kw) -> Path:
        """Write a derived table to ``analysis/tables/<name>`` and record provenance.
        ``index=False`` by default for stable, diffable output."""
        out = self.exp / "analysis" / "tables" / name
        to_csv_kw.setdefault("index", False)
        df.to_csv(out, **to_csv_kw)
        self._record_artifact(out)
        return out

    def write_fig(self, name: str, fig) -> Path:
        """Save a matplotlib figure to ``analysis/fig/<name>`` and record provenance."""
        out = self.exp / "analysis" / "fig" / name
        fig.savefig(out, dpi=120, bbox_inches="tight")
        self._record_artifact(out)
        return out

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
