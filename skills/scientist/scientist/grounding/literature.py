"""scientist.grounding.literature — verify a quote against a paper in the bibliographer library.

A *literature* claim grounds a third-party statement on one or more papers held in the
bibliographer library (BIBLIOGRAPHER_HOME). The deterministic, re-runnable part is exactly
the doc() contract — a verbatim quote must be present in the cited paper's text — except the
text is read from the library's LOCAL DuckDB (get_by_citekey -> chunks/abstract), not a repo
PDF. That read is keyless and offline: only the library's *semantic query* path embeds (needs
a key + network); reading stored chunks/abstract by citekey does not. libkit is imported
lazily here, so a data claim that never calls paper() never pulls libkit in — the data-claim
audit stays as light and keyless as before.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .judgments import evidence_sha as _evidence_sha
from ._text import _sha256, _match_phrase


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


# A directory carrying one of these marks the root of the checkout the module lives in.
# The parent walk stops there so it can resolve a *repo-root* .env without climbing past
# the repo into an unrelated parent (e.g. the real ``$HOME``). ``.git`` is the repo root
# (a worktree's ``.git`` is a file, hence ``.exists()`` not ``.is_dir()``).
_REPO_ROOT_MARKERS = (".git",)


def _parent_env_candidates(start: Path) -> list[Path]:
    """``.env`` paths from ``start``'s ancestors up to — and including — the nearest repo
    root, identified by a :data:`_REPO_ROOT_MARKERS` mark *or* by reaching ``$HOME``.

    The bound is what keeps the search hermetic: without it a module installed deep under
    ``$HOME`` (the normal case) would always pull in ``$HOME/.env`` through the parent walk,
    so a HOME sandbox could never produce a "no .env found" state. Stopping at the repo root
    (or, as a backstop, at ``$HOME`` itself — never climbing *into* it) means the walk only
    ever sees ``.env`` files inside the module's own checkout, exactly the "repo-root .env"
    this is meant to find. cwd/.env and ``~/.env`` remain separate candidates handled by the
    caller, so a real claims run still finds them."""
    home = Path.home().resolve()
    envs: list[Path] = []
    for anc in start.resolve().parents:
        if anc == home:
            break              # backstop: never walk into or above the real home dir
        envs.append(anc / ".env")
        if any((anc / m).exists() for m in _REPO_ROOT_MARKERS):
            break              # reached the repo root — stop before climbing out of it
    return envs


def _load_dotenv_for(key: str) -> None:
    """Populate ``os.environ[key]`` from a .env file if it is not already set (stdlib only).

    Search: cwd, every parent of this module up to its repo root (a repo-root .env), then
    ``~/.env`` (the consolidated location). Real env vars and earlier files win — a later
    file never overrides a value already present. Mirrors the CLIs' ``_load_dotenv`` so a
    claim run under pytest (which never sources a shell profile) still finds BIBLIOGRAPHER_HOME.
    The parent walk is bounded at the repo root (see :func:`_parent_env_candidates`) so it
    cannot escape the checkout into the real ``$HOME`` and read an unrelated ``~/.env``."""
    if os.environ.get(key):
        return
    here = Path(__file__).resolve()
    candidates = [Path.cwd() / ".env", *_parent_env_candidates(here),
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
    authors_text: str = ""     # "Family, Given; Family, Given" — for the report bibliography
    venue: str = ""            # journal / venue name — for the report bibliography
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
        return _match_phrase(phrase, self.text, normalize_ws=normalize_ws)

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
            # Pure read of stored chunk text — open read-only so parallel grounding
            # + judge subagents don't serialise on the library's write lock.
            store = BiblioStore.open(home, read_only=True)
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
        # When the metrics were last fetched (OpenAlex `updated_date`/an enrich timestamp). Used by a
        # bibliometric claim (:func:`metric`) to stamp the snapshot's `as_of` and to age-check it.
        # Absent until the library records it (None is dropped); a bibliometric claim then stamps
        # `as_of=None` and the audit nudges to re-`bib enrich`.
        "as_of": m.get("as_of") or m.get("updated_date"),
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
        # Pure read of paper metadata + stored text — open read-only so parallel
        # grounding + judge subagents don't serialise on the library's write lock.
        store = BiblioStore.open(home, read_only=True)
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
                    "authors_text": rec.get("authors_text") or "", "venue": rec.get("venue") or "",
                    "document_id": str(doc_id or ""),
                    "credibility": _credibility_from_rec(rec)}
        finally:
            await store.close()

    try:
        res = asyncio.run(_go())
    except FileNotFoundError:
        # A read-only open never creates the store, so a not-yet-initialised
        # library raises here rather than returning an empty result. Surface the
        # same "paper not in library" guidance a missing citekey gives.
        res = None
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
                   authors_text=res["authors_text"], venue=res["venue"],
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
    from . import record

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
    # Resolve `paper` through the package namespace (not the module-local name) so a test or
    # caller that monkeypatches ``scientist.grounding.paper`` is honored, exactly as when
    # source() and paper() lived in the same module.
    from . import current_judgment_cache, paper as _paper

    ref = _paper(citekey, allow_retracted=allow_retracted)

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
           "authors_text": ref.authors_text, "venue": ref.venue,
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
    cache = current_judgment_cache()
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
    from . import current_capture

    cap = current_capture()
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


# --------------------------------------------------------------------------- #
# Bibliometric (meta) claims — a claim ABOUT the literature, not a quote IN it.
#
# A *bibliometric* claim ("X is the most-cited result on this question", "Y is rarely
# replicated") is a fact about a paper's standing in the field, not a quote in its text. The
# quote-in-paper machinery (`source`) cannot represent it — there is no sentence in any paper that
# asserts its own citation count — so these read a stored OpenAlex metric (e.g. `cited_by_count`)
# off the library record and assert a plain-Python relation in the test body. `metric()` is the
# **capture seam**: it reads the value, records it (value + as_of + source) as provenance so the
# claim is citeable/traceable/stale-able, and returns the bare number so the test asserts normally:
#
#     @kind("bibliometric")
#     @strength("moderate")
#     @reviewed(date=…, by=…, support=True, note="comparison set = …; metric = cited_by_count",
#               sha="…")   # the audit prints the value+as_of pin to stamp
#     def test_x_is_not_the_most_cited():
#         "Among the loss-tolerance papers, Silva-Santos 2015 and Daily 2011 are the most cited."
#         assert cited_by("silvasantos2015ube") > cited_by("sonzogni2020assessing")
#
# Why no `metric(rel=…)` operator DSL: claims are pytests, so the relation is just Python (`>`,
# top-k, ratios, membership). `metric()` only captures provenance; correctness is the assert.
# The captured value+as_of is what `@reviewed(sha=)` pins over (see provenance.report
# metric_review_sha) so a refreshed count re-opens review — exactly the "caught for review" guarantee.
# --------------------------------------------------------------------------- #
def metric(citekey: str, name: str, *, source: str = "openalex"):
    """Read a stored bibliometric metric for a library paper, record it as provenance, and return
    the bare value so a ``@kind("bibliometric")`` claim can assert a plain-Python relation.

    Resolves the paper (recording it + running the retraction check, like :func:`paper`), reads
    ``name`` from the library record's flattened credibility markers (e.g. ``"cited_by_count"``),
    captures ``{citekey, metric, value, as_of, source}`` into the active capture's
    ``metric_sources`` (so the claim is citeable, traceable, and stale-able), and returns the value.

    The value + ``as_of`` are what the bibliometric review pin is computed over: a refreshed count
    (a new ``bib enrich``) re-opens the recorded ``@reviewed`` (see provenance.report
    :func:`metric_review_sha`). Raises :class:`LiteratureError` if the metric is not in the record
    yet — ``bib enrich <citekey>`` fetches OpenAlex metrics — so a missing metric fails loudly
    rather than silently grounding on ``None``."""
    from . import paper as _paper

    ref = _paper(citekey)                       # resolve + record provenance + retraction check
    value = (ref.credibility or {}).get(name)
    if value is None:
        raise LiteratureError(
            f"metric({citekey!r}, {name!r}): not in the library record — `bib enrich {citekey}` to "
            f"fetch OpenAlex metrics (cited_by_count, FWCI, …), then re-run. A bibliometric claim "
            f"must not ground on a missing metric.")
    rec = {"citekey": citekey, "metric": name, "value": value,
           "as_of": (ref.credibility or {}).get("as_of"), "source": source}
    _record_metric(rec)
    return value


def cited_by(citekey: str) -> int:
    """The OpenAlex cited-by count for a library paper, recorded as bibliometric provenance.
    Convenience for ``metric(citekey, "cited_by_count")`` — the common bibliometric backing."""
    return metric(citekey, "cited_by_count")


def _record_metric(rec: dict) -> None:
    from . import current_capture

    cap = current_capture()
    if cap is not None:
        cap.evidence.setdefault("metric_sources", []).append(rec)
