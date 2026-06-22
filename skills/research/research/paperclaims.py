"""The paper-claims layer — a paper's *attributed* claim set, extracted once and reused.

A **paper-claim** is one assertion a third-party paper makes, normalized into the program's
own vocabulary and pinned to a verbatim span of the paper's text. It is **ATTRIBUTED, not
grounded**: it records what the paper *says*, never what is true. Internal ``kind=claim``
records are grounded — re-runnable pytest specs checked against data the program owns; a
paper-claim can only be *faithful-to-text*, so it is a JSONL **data record** with a single
runnable check (``verify``: the quoted span is still present in the retained paper). Keep the
two structurally and visually distinct — "Smith et al. report X" vs "we measured X" — and
never launder a paper's assertion into a program fact. See ``references/paper-claims.md`` for
the extraction discipline (this module is only the store + the offline checks).

## Why this layer exists

Today an external ``[lit:]`` citation is authored *lazily* per report — every citing report
re-reads the paper to re-derive the same statement, spending the writer budget twice. This
front-loads that work: a paper's claim set is extracted **once** into scientist's own store and
an ``[lit:]`` citation resolves to a pre-extracted paper-claim. (Full design:
``SPEC-litreview-phase2.md``.)

## Architecture (hard constraints, from the SPEC)

* **Scientist-side, scientist's OWN store.** The extractor *reads* the PDF from bibliographer's
  library (via the pure-Python readers scientist already has — :mod:`scientist.grounding`'s
  :class:`PaperRef`) and *writes* here. It **never writes bibliographer's DB**; bib is a
  read-only source of PDFs.
* **Grep-able per-paper JSONL is the source of truth.** One ``<citekey>.jsonl`` per source
  paper under ``<home>/paper-claims/``, loaded into memory on demand (glob + parse). **No DB on
  the critical path**; a semantic index is explicitly deferred. Per-paper sharding (not
  one-file-total, not one-file-per-claim) keeps fan-out writes concurrency-clean — each paper is
  its own file, so two extractions never race on one file.

## The record (one JSON object per line)

Required: ``id`` (``<citekey>::<claim-slug>``), ``paper`` (a ``doi:``/citekey source id),
``citekey``, ``kind`` (always ``"attributed"``), ``paraphrase`` (OUR normalized vocabulary — the
grep/search target), ``quote`` (verbatim span), ``evidence_sha`` (sha of the folded quote — the
integrity pin), ``strength`` (normalized, carrying the paper's hedging), ``methods_qualifier``
(travels with the claim; never read context-free).

Optional: ``locator`` (page/section), ``hedge`` (verbatim hedge snippet), ``n``, ``p``,
``caveats``, ``conditioned_on`` (claim-id links — "B given A"), ``precis`` (bool — the one
per-paper précis claim), ``borrowed`` (bool — background whose true source is elsewhere; don't
double-count), ``null_result`` (bool — from the explicit null/negative pass).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

# The library-wide directory holding one JSONL per source paper, at the scientist home root
# (paper-claims are shared across every review/report in the program — SPEC §3 / §7.2).
PAPER_CLAIMS_DIRNAME = "paper-claims"

# Fields every well-formed paper-claim must carry (non-empty). These are the integrity-critical
# fields + the two the extraction guide mandates on EVERY claim (strength carries the paper's
# hedging; methods_qualifier travels with the claim). The optional fields default in :func:`_norm`.
REQUIRED_FIELDS = ("id", "paper", "citekey", "kind", "paraphrase", "quote",
                   "evidence_sha", "strength", "methods_qualifier")

# Normalized strength vocabulary — reflects the PAPER's hedging, not the program's confidence in
# reality. "suggests"/"appears" → weak; "is associated with" → moderate; "demonstrates" → strong.
STRENGTHS = ("strong", "moderate", "weak")

KIND = "attributed"

# A paper-claim id is exactly ``<citekey>::<slug>`` — two non-empty ``::``-separated parts, no
# whitespace, slug in kebab-case. The citekey half MUST equal the row's ``citekey`` (ties the id
# to its source file). Distinct from an internal claim id (``<exp>::<file>::<node>``, three parts),
# so the two id namespaces never collide in ``[lit:]`` resolution.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EVIDENCE_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def paper_claims_dir(home: Path) -> Path:
    """The library-wide paper-claims directory: ``<home>/paper-claims/``."""
    return Path(home) / PAPER_CLAIMS_DIRNAME


def claims_path(home: Path, citekey: str) -> Path:
    """The per-paper JSONL for ``citekey``: ``<home>/paper-claims/<citekey>.jsonl``."""
    return paper_claims_dir(home) / f"{citekey}.jsonl"


def iter_files(home: Path, *, paper: str | None = None) -> list[Path]:
    """The per-paper JSONL files to load: one (``paper`` given) or every ``*.jsonl`` in the
    paper-claims dir, sorted. Empty when the dir (or the named file) is absent."""
    if paper is not None:
        p = claims_path(home, paper)
        return [p] if p.is_file() else []
    d = paper_claims_dir(home)
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


# --------------------------------------------------------------------------- #
# load / parse
# --------------------------------------------------------------------------- #
def _norm(obj: dict[str, Any]) -> dict[str, Any]:
    """Fill the boolean flags (``precis``/``borrowed``/``null_result``) so callers can read
    them unconditionally. Leaves every other field as-authored."""
    return {"precis": False, "borrowed": False, "null_result": False, **obj}


def load_file(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse one ``<citekey>.jsonl`` into ``(rows, findings)`` — one JSON object per non-blank
    line. A line that is not a JSON object yields a blocking ``malformed-paper-claim-row``
    finding (and is skipped); well-formed rows are normalized (:func:`_norm`) and carry a private
    ``_line`` (1-based) for diagnostics. An absent file is ``([], [])`` — callers that require the
    file (validate/verify) check existence themselves."""
    p = Path(path)
    if not p.is_file():
        return [], []
    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for i, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            findings.append({"kind": "malformed-paper-claim-row", "line": i,
                             "detail": f"line {i} is not valid JSON"})
            continue
        if not isinstance(obj, dict):
            findings.append({"kind": "malformed-paper-claim-row", "line": i,
                             "detail": f"line {i} is not a JSON object"})
            continue
        rows.append({**_norm(obj), "_line": i})
    return rows, findings


def load_paper_claims(home: Path, *, paper: str | None = None) -> dict[str, dict[str, Any]]:
    """Load the paper-claim corpus into ``{id -> claim}`` (glob + parse, on demand). With
    ``paper`` given, load just that paper's file. The whole corpus is single-digit MB at
    realistic scale, so "load all, filter in memory" is fine — nothing is paged.

    Each claim carries its source ``citekey`` (from the row, falling back to the filename stem)
    so a caller has the paper identity without re-deriving it. On a duplicate ``id`` the last
    line wins (``validate`` is what flags the duplicate); malformed lines are skipped here."""
    index: dict[str, dict[str, Any]] = {}
    for path in iter_files(home, paper=paper):
        rows, _ = load_file(path)
        stem = path.stem
        for r in rows:
            cid = str(r.get("id") or "")
            if not cid:
                continue
            index[cid] = {**r, "citekey": r.get("citekey") or stem, "_file": str(path)}
    return index


def resolve_paper_claim(cid: str, index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve an ``[lit:]`` id to a stored paper-claim by **exact id** (``<citekey>::<slug>``).
    No suffix/tail matching — the id is stable and unambiguous, and exact match keeps the
    paper-claim namespace from colliding with internal-claim tail resolution."""
    return index.get(cid.strip())


# --------------------------------------------------------------------------- #
# validate — schema / structural check (offline, no paper text needed)
# --------------------------------------------------------------------------- #
def _row_findings(r: dict[str, Any], citekey: str) -> list[dict[str, Any]]:
    """The schema findings for one row (all blocking): missing/empty required field, wrong
    ``kind``, malformed ``id``/``evidence_sha``, off-vocabulary ``strength``, ``id`` whose
    citekey-half disagrees with ``citekey``, or a non-bool flag."""
    line = r.get("_line", 0)
    out: list[dict[str, Any]] = []

    def bad(kind: str, detail: str) -> None:
        out.append({"kind": kind, "line": line, "detail": detail})

    for field in REQUIRED_FIELDS:
        val = r.get(field)
        empty = val is None or (isinstance(val, str) and not val.strip())
        if empty:
            bad("missing-field", f"row (L{line}) is missing or empty required `{field}`")

    if r.get("kind") is not None and str(r.get("kind")) != KIND:
        bad("wrong-kind", f"row (L{line}) has kind={r.get('kind')!r} — a paper-claim is always "
                          f"`{KIND}` (attributed, not grounded)")

    cid = str(r.get("id") or "")
    if cid:
        parts = cid.split("::")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            bad("malformed-id", f"id {cid!r} is not `<citekey>::<slug>` (exactly two non-empty "
                                f"`::`-separated parts)")
        else:
            ck, slug = parts
            if r.get("citekey") and ck != str(r.get("citekey")):
                bad("malformed-id", f"id {cid!r} citekey-half `{ck}` disagrees with the row's "
                                    f"citekey `{r.get('citekey')}`")
            if ck != citekey:
                bad("malformed-id", f"id {cid!r} belongs to `{ck}` but lives in `{citekey}.jsonl`")
            if not _SLUG_RE.match(slug):
                bad("malformed-id", f"id {cid!r} slug `{slug}` is not kebab-case "
                                    f"([a-z0-9] words joined by hyphens)")

    sha = str(r.get("evidence_sha") or "")
    if sha and not _EVIDENCE_SHA_RE.match(sha):
        bad("malformed-evidence-sha", f"evidence_sha {sha!r} is not a 64-char sha256 hex digest "
                                      f"(re-run `res paper-claims scaffold`/the extractor)")

    strength = r.get("strength")
    if strength is not None and str(strength).strip() and str(strength) not in STRENGTHS:
        bad("bad-strength", f"strength={strength!r} is off-vocabulary (one of {list(STRENGTHS)} — "
                            f"normalized from the paper's own hedging)")

    for flag in ("precis", "borrowed", "null_result"):
        if not isinstance(r.get(flag), bool):
            bad("malformed-flag", f"`{flag}` must be a bool (got {r.get(flag)!r})")

    co = r.get("conditioned_on")
    if co is not None and not (isinstance(co, list) and all(isinstance(x, str) for x in co)):
        bad("malformed-conditioned-on", "`conditioned_on` must be a list of claim-id strings")
    return out


def validate(home: Path, citekey: str,
             rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Schema-check ``<citekey>.jsonl``: every required field present, ``kind=="attributed"``,
    ids well-formed (``<citekey>::<slug>``), strengths on-vocabulary, the boolean flags actually
    boolean, **exactly one** ``precis`` claim, no duplicate ids, and every same-paper
    ``conditioned_on`` link resolving to a sibling row. (A cross-paper ``conditioned_on`` — a
    different citekey-half — is allowed but not resolved here; cross-paper resolution is Phase 3.)

    Returns ``{citekey, path, present, count, findings, status}`` — ``status`` is ``VALID`` iff no
    finding. Pure / offline: no paper text is read (that is :func:`verify`)."""
    path = claims_path(home, citekey)
    if rows is None:
        if not path.is_file():
            return {"citekey": citekey, "path": str(path), "present": False, "count": 0,
                    "findings": [{"kind": "missing-paper-claims", "line": 0,
                                  "detail": f"no {citekey}.jsonl in {PAPER_CLAIMS_DIRNAME}/ — "
                                            f"`res paper-claims scaffold {citekey}` first"}],
                    "status": "BROKEN"}
        rows, findings = load_file(path)
    else:
        findings = []

    findings = list(findings)
    ids: dict[str, int] = {}
    own_ids = {str(r.get("id")) for r in rows if r.get("id")}
    precis_count = 0
    for r in rows:
        findings.extend(_row_findings(r, citekey))
        cid = str(r.get("id") or "")
        if cid:
            if cid in ids:
                findings.append({"kind": "duplicate-id", "line": r.get("_line", 0),
                                 "detail": f"id {cid!r} also appears on line {ids[cid]}"})
            else:
                ids[cid] = r.get("_line", 0)
        if r.get("precis") is True:
            precis_count += 1
        for dep in (r.get("conditioned_on") or []):
            dep = str(dep)
            # Only same-paper links are resolved in Phase 2 (cross-paper is Phase 3): a dep whose
            # citekey-half is THIS paper must point at a sibling row.
            if dep.split("::", 1)[0] == citekey and dep not in own_ids:
                findings.append({"kind": "unresolved-conditioned-on", "line": r.get("_line", 0),
                                 "detail": f"conditioned_on `{dep}` resolves to no claim in "
                                           f"{citekey}.jsonl"})

    if rows and precis_count == 0:
        findings.append({"kind": "missing-precis", "line": 0,
                         "detail": "no précis claim — exactly one row must set `precis: true` "
                                   "(the paper's own headline/arc; the cheapest narrative anchor)"})
    elif precis_count > 1:
        findings.append({"kind": "multiple-precis", "line": 0,
                         "detail": f"{precis_count} rows set `precis: true` — exactly one is allowed"})

    return {"citekey": citekey, "path": str(path), "present": True, "count": len(rows),
            "findings": findings, "status": "VALID" if not findings else "BROKEN"}


# --------------------------------------------------------------------------- #
# verify — quote-integrity check (reads the retained paper text)
# --------------------------------------------------------------------------- #
def _default_paper_loader(citekey: str):
    """Resolve a paper to a :class:`PaperRef` via the grounding layer (reads the bibliographer
    library read-only, keyless/offline). Imported lazily so ``validate``/``load`` (and the
    ``[lit:]`` audit) never pull in the grounding package."""
    from research import paper as _paper
    return _paper(citekey, allow_retracted=True)


def verify(home: Path, citekey: str, *,
           paper_loader: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """The one runnable check an attributed claim supports: each claim's ``quote`` is still
    present in the retained paper text, and its stored ``evidence_sha`` still matches the folded
    quote. Flags drift if the source was re-OCR'd / replaced (quote gone) or a quote was
    hand-edited without re-extraction (sha mismatch).

    Reads the paper text via ``paper_loader`` (default: the grounding library reader); tests
    inject a fake. Returns ``{citekey, path, checked, ok, drift, findings, status}`` — ``status``
    is ``VERIFIED`` iff no claim drifted. The folded ``evidence_sha`` is computed exactly as the
    judge-cache / quote-matcher fold (one canonical identity)."""
    from research.judgments import evidence_sha as _evidence_sha

    path = claims_path(home, citekey)
    if not path.is_file():
        return {"citekey": citekey, "path": str(path), "checked": 0, "ok": 0, "drift": [],
                "findings": [{"kind": "missing-paper-claims", "line": 0,
                              "detail": f"no {citekey}.jsonl to verify"}],
                "status": "BROKEN"}

    loader = paper_loader or _default_paper_loader
    rows, findings = load_file(path)
    findings = list(findings)
    ref = loader(citekey)

    checked = ok = 0
    drift: list[dict[str, Any]] = []
    for r in rows:
        quote = str(r.get("quote") or "")
        cid = str(r.get("id") or f"L{r.get('_line', 0)}")
        if not quote:
            continue                       # a missing quote is validate's finding, not verify's
        checked += 1
        present = bool(ref.contains(quote))
        recomputed = _evidence_sha(quote)
        stored = str(r.get("evidence_sha") or "")
        if not present:
            drift.append({"id": cid, "line": r.get("_line", 0), "reason": "quote-not-found"})
            findings.append({"kind": "quote-drift", "line": r.get("_line", 0), "cite": cid,
                             "detail": f"the quoted span for `{cid}` is no longer present in "
                                       f"{citekey}'s text — the source was re-OCR'd/replaced; "
                                       f"re-extract"})
        elif stored and recomputed != stored:
            drift.append({"id": cid, "line": r.get("_line", 0), "reason": "sha-mismatch"})
            findings.append({"kind": "evidence-sha-mismatch", "line": r.get("_line", 0),
                             "cite": cid,
                             "detail": f"the quote for `{cid}` was edited without re-extraction "
                                       f"(stored evidence_sha {stored[:12]} ≠ {recomputed[:12]}) — "
                                       f"re-run the extractor so the pin matches"})
        else:
            ok += 1
    return {"citekey": citekey, "path": str(path), "checked": checked, "ok": ok, "drift": drift,
            "findings": findings, "status": "VERIFIED" if not findings else "BROKEN"}


# --------------------------------------------------------------------------- #
# scaffold — resolve the PDF in the library, open the file, emit the brief
# --------------------------------------------------------------------------- #
def scaffold(home: Path, citekey: str, *,
             paper_loader: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Prepare to extract ``citekey``'s claims: confirm the paper resolves in the bibliographer
    library (read-only), create an empty ``paper-claims/<citekey>.jsonl`` if absent, and emit the
    extraction brief pointing at ``references/paper-claims.md``. The agent reads the paper and
    authors the JSONL (extraction is judgment, guided — not code-wrapped).

    Returns ``{citekey, path, created, exists, paper, brief}`` — ``paper`` carries the resolved
    title/year/mode (``mode='abstract'`` warns that extraction will be shallow). Raises
    :class:`LiteratureError` (via the loader) if the paper is not in the library."""
    loader = paper_loader or _default_paper_loader
    ref = loader(citekey)
    paper_meta = {
        "citekey": citekey,
        "title": getattr(ref, "title", "") or "",
        "year": getattr(ref, "year", "") or "",
        "doi": getattr(ref, "doi", "") or "",
        "mode": getattr(ref, "mode", "") or "",
    }
    path = claims_path(home, citekey)
    existed = path.is_file()
    if not existed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    shallow = paper_meta["mode"] == "abstract"
    brief = (
        f"Extract {citekey}'s attributed claims into {path.name} per "
        f"references/paper-claims.md.\n"
        f"  paper: {paper_meta['title'] or citekey}"
        + (f" ({paper_meta['year']})" if paper_meta["year"] else "")
        + (f"  doi:{paper_meta['doi']}" if paper_meta["doi"] else "") + "\n"
        f"  ATTRIBUTED, not grounded — pin to what the paper SAYS, never to reality.\n"
        f"  One JSON object per line: id=<citekey>::<slug>, kind=\"attributed\", paraphrase (OUR\n"
        f"  vocabulary), quote (verbatim), evidence_sha, strength (the paper's hedging),\n"
        f"  methods_qualifier (every claim). Exactly one precis:true row; an explicit\n"
        f"  null/negative pass; conditioned_on links; mark borrowed background. Re-extraction\n"
        f"  rewrites this file from scratch (idempotent). Then `res paper-claims validate "
        f"{citekey}`."
        + ("\n  ⚠ this paper is ABSTRACT-ONLY in the library — extraction will be shallow; "
           "`bib fetch` the full text first if you can." if shallow else ""))
    return {"citekey": citekey, "path": str(path), "created": not existed, "exists": existed,
            "paper": paper_meta, "brief": brief}


# --------------------------------------------------------------------------- #
# query — substring / regex over paraphrase (the grep path; no semantic ranking)
# --------------------------------------------------------------------------- #
def query(home: Path, *, paper: str | None = None,
          query: str | None = None) -> list[dict[str, Any]]:
    """Load the paper-claim set(s) and filter for the ``--json | python3 -c`` consumption
    pattern. ``paper`` scopes to one citekey; ``query`` is a substring/regex filter over
    ``paraphrase`` (the grep target — paraphrases are in OUR consistent vocabulary, which is what
    makes plain matching adequate without a semantic index). No semantic ranking (deferred).
    Returns the matching claims sorted by ``id`` (private ``_line``/``_file`` keys stripped)."""
    idx = load_paper_claims(home, paper=paper)
    claims = sorted(idx.values(), key=lambda c: str(c.get("id") or ""))
    if query:
        try:
            pat = re.compile(query, re.IGNORECASE)
        except re.error:
            pat = re.compile(re.escape(query), re.IGNORECASE)
        claims = [c for c in claims if pat.search(str(c.get("paraphrase") or ""))]
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in claims]


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render_validate(result: dict[str, Any]) -> str:
    head = f"{result['path']}: {result['status']}  ({result['count']} claim(s))"
    lines = [head]
    for f in result.get("findings", []):
        loc = f" L{f['line']}" if f.get("line") else ""
        lines.append(f"  ! {f['kind']}{loc}: {f.get('detail', '')}")
    if result["status"] == "VALID":
        lines.append("  ✅ schema OK (run `res paper-claims verify` for quote-integrity)")
    return "\n".join(lines)


def render_verify(result: dict[str, Any]) -> str:
    lines = [f"{result['path']}: {result['status']}  "
             f"({result['ok']}/{result['checked']} quote(s) intact)"]
    for f in result.get("findings", []):
        loc = f" L{f['line']}" if f.get("line") else ""
        lines.append(f"  ! {f['kind']}{loc}: {f.get('detail', '')}")
    if result["status"] == "VERIFIED" and result["checked"]:
        lines.append("  ✅ every quote still located in the retained paper text")
    return "\n".join(lines)


def render_scaffold(result: dict[str, Any]) -> str:
    state = "created" if result["created"] else "exists"
    return f"{state} {result['path']}\n{result['brief']}"


def render_query(claims: list[dict[str, Any]]) -> str:
    if not claims:
        return "no matching paper-claims"
    lines = []
    for c in claims:
        flags = "".join(t for t, on in (("P", c.get("precis")), ("B", c.get("borrowed")),
                                        ("N", c.get("null_result"))) if on)
        tag = f" [{flags}]" if flags else ""
        lines.append(f"{c.get('id')}  ({c.get('strength')}){tag}\n    {c.get('paraphrase')}")
    return "\n".join(lines)
