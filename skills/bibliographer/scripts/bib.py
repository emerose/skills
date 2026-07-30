#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["libkit>=0.5.0", "pypdf>=4.0", "httpx>=0.27", "diskcache>=5.6", "platformdirs>=4.0"]
# ///
"""bib - a libkit-backed bibliographer for a collection of academic articles.

The collection lives in a "library" directory (default: ~/.bibliographer,
override with --home or BIBLIOGRAPHER_HOME) containing:

    <library>/
      catalog.duckdb     the libkit store (the single source of truth)
      papers/            the organized files, one per article
      index.html         a self-contained, searchable HTML viewer (auto-regenerated)

libkit (>=0.5.0) IS the store: there is no separate bibliographer database.
Each paper is one libkit document; every bibliographic field — DOI, arXiv id,
authors, venue, year, abstract, tags, citekey, file path — lives in the
document's free-form ``metadata`` JSON. Paper-level identity (citekeys, dedup
by identifier) is layered on top of libkit's byte-level identity.

Run `bib <command> --help` for details on any command.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

# The `bibliographer` package lives one level up (sibling of scripts/). Put that
# dir on sys.path so `from bibliographer import …` resolves when this script is run
# directly via its PEP-723 shebang (no install). An installed/editable package
# already has it on the path; this insert is a harmless no-op then.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bibliographer import fileorg as _fileorg  # noqa: E402
from bibliographer import meta as _meta  # noqa: E402
from bibliographer.store import BiblioStore, EmbedderConfigError  # noqa: E402

INGESTIBLE_EXTS = {".pdf", ".md", ".markdown", ".docx", ".doc", ".pptx", ".ppt", ".odt"}

# Where to look when neither --home nor $BIBLIOGRAPHER_HOME is set. Resolved lazily
# (in dispatch, AFTER _load_dotenv) — NOT at import — because $BIBLIOGRAPHER_HOME is
# commonly set in ~/.env, which is only loaded once the CLI starts. Reading it here at
# import time (the old behavior) missed that .env and silently used ~/.bibliographer.
FALLBACK_HOME = Path.home() / ".bibliographer"


def _default_home() -> Path:
    """The library dir when --home was not given: $BIBLIOGRAPHER_HOME if set (now that
    _load_dotenv has run, an .env value counts), else ~/.bibliographer."""
    h = os.environ.get("BIBLIOGRAPHER_HOME")
    return Path(h).expanduser() if h else FALLBACK_HOME


def _load_dotenv(home: Path | None = None) -> None:
    """Load KEY=VALUE pairs from .env files into the environment (stdlib only).

    Search order: the library ``home``, the current directory, every parent of
    this script (so a repo-root .env is found), then ``~/.env`` (the
    consolidated location). Real environment variables and earlier files win —
    a later file never overrides a value already set.
    """
    here = Path(__file__).resolve()
    candidates = [
        *([home / ".env"] if home is not None else []),
        Path.cwd() / ".env",
        *[p / ".env" for p in here.parents],
        Path.home() / ".env",
    ]
    seen: set[Path] = set()
    for env_path in candidates:
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def die(msg: str, code: int = 1) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def warn(msg: str) -> None:
    """Print a non-fatal warning to stderr (so it never pollutes stdout/JSON)."""
    print(f"warning: {msg}", file=sys.stderr)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def emit_json(obj: Any) -> None:
    import json

    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def print_table(records: list[dict[str, Any]]) -> None:
    if not records:
        print("(no articles)")
        return
    for r in records:
        year = r.get("year") or "????"
        title = r.get("title") or "(untitled)"
        print(f"  [{r.get('citekey', '?')}] {_meta.short_authors(r)} ({year}) — {title}")
        flags = []
        if r.get("content_state") == "stub":
            flags.append("no-file (citation-only)")
        if r.get("tags"):
            flags.append("tags: " + ", ".join(r["tags"]))
        if flags:
            print(f"        {'; '.join(flags)}")


async def write_index(store: BiblioStore) -> None:
    """Regenerate <home>/index.html — the self-contained, searchable viewer that
    makes the library folder browsable by just opening it (replaces the old
    auto-exported library.bib; BibTeX is still available on demand via `export`)."""
    from bibliographer import viewer as _viewer

    recs = await store.all_records()
    title = store.home.name or "Bibliography"
    (store.home / "index.html").write_text(_viewer.render(recs, title), encoding="utf-8")
    stale = store.home / "library.bib"  # drop the artifact the viewer replaces
    if stale.exists():
        stale.unlink()


# --------------------------------------------------------------------------- #
# identifier / PDF helpers (full resolver layer arrives in step 2)
# --------------------------------------------------------------------------- #
def classify(identifier: str) -> tuple[str, Any]:
    """Return ('file', Path) for an existing local file, else ('identifier', str)."""
    p = Path(identifier).expanduser()
    if p.exists() and p.is_file():
        return "file", p.resolve()
    return "identifier", identifier.strip()


def embedded_pdf_metadata(path: Path) -> dict[str, Any]:
    """Best-effort record from a PDF's embedded metadata (unverified)."""
    rec: dict[str, Any] = {"source": "pdf", "bibtex_type": "misc"}
    try:
        from pypdf import PdfReader

        info = PdfReader(str(path)).metadata or {}
        if info.get("/Title"):
            rec["title"] = str(info["/Title"]).strip()
        if info.get("/Author"):
            author = str(info["/Author"]).strip()
            rec["authors"] = [{"family": author, "given": ""}]
            rec["authors_text"] = author
    except Exception:  # noqa: BLE001
        pass
    rec.setdefault("title", path.stem.replace("_", " "))
    return rec


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
async def cmd_init(args: argparse.Namespace, store: BiblioStore) -> None:
    await write_index(store)
    print(f"Initialized bibliographer library at {store.home}")
    print(f"  catalog: {store.home / 'catalog.duckdb'}")
    print(f"  papers:  {store.home / 'papers'}/")
    print(f"  viewer:  {store.home / 'index.html'}  (open in a browser)")


async def cmd_viewer(args: argparse.Namespace, store: BiblioStore) -> None:
    """Regenerate the HTML viewer and print its path."""
    await write_index(store)
    print(f"Viewer written to {store.home / 'index.html'}")
    print(f"Open it: file://{store.home / 'index.html'}")


async def resolve_target(
    target: str, *, pdf_override: Path | None = None, no_network: bool = False, client: Any = None
) -> tuple[dict[str, Any], Path | None]:
    """Resolve an add/import target to ``(record, source_file)``.

    - identifier (DOI/arXiv/PMID/PMCID/S2): fetch metadata; ``pdf_override`` is the file.
    - local PDF: sniff + resolve online, else embedded metadata.
    - other local ingestible file: minimal record from the filename.
    """
    from bibliographer import resolvers as _resolvers

    kind, value = classify(target)
    if kind == "identifier":
        if no_network:
            # Raise (don't die) so a batch caller can skip this id and continue;
            # cmd_add turns it into a clean error for a single-id invocation.
            raise _resolvers.ResolveError(
                f"--no-network given but {value!r} needs an online lookup"
            )
        rec = await _resolvers.resolve(value, client=client)
        return rec, pdf_override

    # kind == "file"
    if value.suffix.lower() == ".pdf":
        rec = None
        if not no_network:
            try:
                rec = await _resolvers.resolve_pdf(value, client=client)
            except Exception:  # noqa: BLE001  (network best-effort)
                rec = None
        return (rec or embedded_pdf_metadata(value)), value
    return {"source": "file", "bibtex_type": "misc", "title": value.stem.replace("_", " ")}, value


async def ingest_record(
    store: BiblioStore,
    rec: dict[str, Any],
    *,
    src: Path | None,
    move: bool,
    fetch: bool,
    force: bool,
    on_duplicate: str,        # "report" (add) | "merge" (import)
    client: Any = None,
    enrich_meta: bool = True,
) -> dict[str, Any]:
    """Shared add/import core: dedup -> citekey -> enrich -> fetch-then-ingest -> organize -> store.

    Returns a result dict with ``status`` one of added | merged | merged-dup |
    duplicate, plus the stored ``record``. With ``enrich_meta`` (and a DOI/PMID),
    stamps OpenAlex work + venue metadata onto the record before storing.
    """
    from bibliographer import resolvers as _resolvers

    rec = dict(rec)
    if not force:
        dup = await store.find_duplicate(rec)
        if dup is not None:
            if on_duplicate == "merge":
                return {"status": "merged-dup", "record": await store.merge_duplicate(dup, rec)}
            return {"status": "duplicate", "record": dup}

    rec["citekey"] = await store.unique_citekey(_meta.make_citekey(rec))

    want_enrich = enrich_meta and (rec.get("doi") or rec.get("pmid"))
    want_fetch = src is None and fetch
    own = client is None
    if own and (want_enrich or want_fetch):
        import httpx

        client = httpx.AsyncClient(
            timeout=60, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
        )

    tmp: Path | None = None
    try:
        # Stamp work + venue metadata (impact, retraction, journal trust) — best
        # effort, never blocks an add.
        if want_enrich:
            try:
                await _resolvers.enrich_openalex(rec, client)
            except Exception:  # noqa: BLE001 — metadata enrichment is best-effort
                pass
        if want_fetch:
            tmp = Path(tempfile.mkstemp(suffix=".pdf")[1])
            pdf_source = await _resolvers.acquire_oa_pdf(rec, tmp, client)
            if pdf_source:
                src, rec["pdf_source"] = tmp, pdf_source
            else:
                tmp.unlink(missing_ok=True)
                tmp = None
    finally:
        if own and client is not None:
            await client.aclose()

    file_path: Path | None = None
    if src is not None:
        rec.setdefault("original_path", str(src))
        file_path = _fileorg.place(store.home, rec, src, move=move or tmp is not None)

    return await store.add(rec, file_path=file_path, force=True)


async def cmd_add(args: argparse.Namespace, store: BiblioStore) -> None:
    """Bank one or more papers by identifier (DOI/arXiv/PMID/PMCID/S2) or PDF path.

    Banking the keepers from a `discover` sweep is the main batch use: pass the
    judged identifiers in one call. A duplicate is *skipped and reported* (not an
    error) — sweep overlap is expected — so one already-present paper never aborts
    the rest of the batch.
    """
    ids = args.identifiers
    if args.pdf and len(ids) != 1:
        die("--pdf attaches a single file; pass exactly one identifier with --pdf")
    extra_tags = (
        {t.strip() for t in args.tags.split(",") if t.strip()} if args.tags else set()
    )

    from bibliographer import resolvers as _resolvers

    results: list[dict[str, Any]] = []
    for ident in ids:
        try:
            rec, src = await resolve_target(
                ident,
                pdf_override=Path(args.pdf).expanduser().resolve() if args.pdf else None,
                no_network=args.no_network,
            )
        except Exception as e:  # noqa: BLE001 — one bad identifier must not sink the batch
            if len(ids) == 1:
                # Single add: a resolve failure is a clean error; an unexpected
                # exception (a real bug) should still surface with its traceback.
                if isinstance(e, _resolvers.ResolveError):
                    die(str(e))
                raise
            print(f"  ! {ident}: {e}", file=sys.stderr)
            results.append({"status": "error", "identifier": ident, "error": str(e)})
            continue
        if extra_tags:
            rec["tags"] = sorted(set(rec.get("tags") or []) | extra_tags)

        # A duplicate without --force is skipped (reported), not fatal.
        if not args.force:
            dup = await store.find_duplicate(rec)
            if dup is not None:
                results.append({"status": "duplicate", "record": dup})
                if not args.json:
                    print(
                        f"= already present [{dup.get('citekey')}] {dup.get('title')}"
                        " (--force to add anyway)"
                    )
                continue

        result = await ingest_record(
            store, rec, src=src, move=args.move, fetch=not args.no_fetch,
            force=args.force, on_duplicate="report", enrich_meta=not args.no_network,
        )
        results.append(result)
        if not args.json:
            rec_out = result["record"]
            verb = "Merged into" if result["status"].startswith("merged") else "Added"
            print(f"{verb} [{rec_out.get('citekey')}] {rec_out.get('title')}")
            if rec_out.get("sniffed_from"):
                print(f"  note: identifier recovered from the PDF ({rec_out['sniffed_from']})")
            if rec_out.get("source") in ("pdf", "file"):
                print("  note: metadata from the file only — unverified; add a DOI/arXiv id to enrich")
            if rec_out.get("content_state") == "stub" and rec_out.get("oa_pdf_url"):
                print("  note: open-access PDF available; re-run without --no-fetch to attach it")

    await write_index(store)

    if args.json:
        emit_json(results[0] if len(ids) == 1 else results)
        return
    if len(ids) > 1:
        added = sum(1 for r in results if r.get("status") == "added")
        merged = sum(1 for r in results if str(r.get("status")).startswith("merged"))
        dups = sum(1 for r in results if r.get("status") == "duplicate")
        errs = sum(1 for r in results if r.get("status") == "error")
        print(
            f"\nBanked {added} added, {merged} merged, {dups} already present"
            + (f", {errs} failed" if errs else "")
        )


def topic_tag(root: Path, f: Path) -> str | None:
    """Derive a provisional ``topic:<slug>`` tag from a file's top-level folder.

    The pile's existing topic folders (e.g. ``02_target_biology``) become tags,
    not load-bearing structure — they're AI-generated and not fully trusted.
    """
    rel = f.relative_to(root)
    if len(rel.parts) < 2:
        return None
    slug = re.sub(r"^\d+[_\-]*", "", rel.parts[0]).replace("_", "-").strip("-").lower()
    return f"topic:{slug}" if slug else None


def _legacy_id(name: str) -> str | None:
    m = re.match(r"(\d{3})[_\-]", name)
    return m.group(1) if m else None


async def cmd_import(args: argparse.Namespace, store: BiblioStore) -> None:
    import httpx

    from bibliographer import resolvers as _resolvers

    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        die(f"not a directory: {root}")
    files = sorted(
        f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in INGESTIBLE_EXTS
    )
    if args.exclude:
        files = [
            f for f in files
            if not any(ex in str(f.relative_to(root)) for ex in args.exclude)
        ]
    if args.limit:
        files = files[: args.limit]
    if not files:
        die(f"no ingestible files under {root}")

    client = httpx.AsyncClient(
        timeout=60, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
    )
    rows: list[dict[str, Any]] = []
    counts = {"resolved": 0, "unverified": 0, "sniffed": 0, "duplicate": 0, "added": 0, "error": 0}
    try:
        for f in files:
            row: dict[str, Any] = {"file": str(f.relative_to(root))}
            try:
                rec, src = await resolve_target(str(f), no_network=args.no_network, client=client)
                tag = topic_tag(root, f)
                if tag:
                    rec["tags"] = sorted(set(rec.get("tags") or []) | {tag})
                rec.setdefault("original_path", str(f))
                if _legacy_id(f.name):
                    rec["legacy_id"] = _legacy_id(f.name)

                online = rec.get("source") in ("crossref", "arxiv", "semantic_scholar", "pubmed")
                counts["resolved" if online else "unverified"] += 1
                if rec.get("sniffed_from"):
                    counts["sniffed"] += 1
                row.update({
                    "citekey": _meta.make_citekey(rec), "title": rec.get("title"),
                    "year": rec.get("year"), "source": rec.get("source"),
                    "topic": tag, "sniffed_from": rec.get("sniffed_from"),
                })

                dup = await store.find_duplicate(rec)
                if dup is not None:
                    counts["duplicate"] += 1
                    row["duplicate_of"] = dup.get("citekey")

                if args.dry_run:
                    row["planned_path"] = str(
                        _fileorg.plan_path(store.home, {**rec, "citekey": row["citekey"]},
                                           f.suffix.lower()).relative_to(store.home)
                    )
                else:
                    result = await ingest_record(
                        store, rec, src=f, move=not args.copy, fetch=False,
                        force=False, on_duplicate="merge", client=client,
                        enrich_meta=not args.no_network,
                    )
                    row["status"] = result["status"]
                    row["citekey"] = result["record"].get("citekey")
                    if result["status"] == "added":
                        counts["added"] += 1
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001 — one bad file shouldn't abort the batch
                counts["error"] += 1
                row["error"] = f"{type(e).__name__}: {e}"
            rows.append(row)
    finally:
        await client.aclose()

    if not args.dry_run:
        await write_index(store)

    if args.json:
        emit_json({"root": str(root), "files": len(files), "counts": counts, "rows": rows})
        return
    mode = "DRY RUN — nothing moved or ingested" if args.dry_run else "IMPORTED"
    print(f"{mode}: {len(files)} file(s) under {root}\n")
    for r in rows:
        if r.get("error"):
            print(f"  ERROR  {r['file']}: {r['error']}")
            continue
        mark = "↳dup" if r.get("duplicate_of") else (r.get("status") or "plan")
        sniff = f"  (sniffed {r['sniffed_from']})" if r.get("sniffed_from") else ""
        print(f"  [{r.get('citekey')}] {r.get('source')}/{mark} — {r.get('title')}{sniff}")
        if args.dry_run and r.get("planned_path"):
            print(f"        -> {r['planned_path']}  {('#' + r['topic']) if r.get('topic') else ''}")
    print(
        f"\nresolved online: {counts['resolved']}  unverified(file-only): {counts['unverified']}"
        f"  sniffed: {counts['sniffed']}  duplicates: {counts['duplicate']}"
        + ("" if args.dry_run else f"  added: {counts['added']}")
        + (f"  errors: {counts['error']}" if counts["error"] else "")
    )
    if args.dry_run:
        print("\nReview above, then re-run without --dry-run to move + ingest.")


def filename_query(name: str) -> str:
    """Turn a `NNN_author_year_title` filename into a Crossref bibliographic query."""
    stem = Path(name).stem
    stem = re.sub(r"^\(n\.d\.\)\s*-\s*", "", stem)         # our stub prefix
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\bv\d+\b", "", stem, flags=re.I)        # version markers
    # keep 4-digit years, drop other bare numbers (NNN ids, etc.)
    toks = [t for t in stem.split() if not (t.isdigit() and not re.fullmatch(r"(19|20)\d\d", t))]
    return " ".join(toks).strip()


def filename_year(name: str) -> int | None:
    m = re.search(r"\b(19|20)\d\d\b", Path(name).stem)
    return int(m.group(0)) if m else None


def verify_candidate(
    candidate: dict[str, Any], content: str, fyear: int | None, fname_text: str, threshold: float
) -> tuple[bool, float]:
    """Decide whether a Crossref candidate really is this document.

    Returns ``(verified, title_overlap)``. Verified requires ALL of:
    - the candidate title has enough significant tokens to be discriminating
      (kills degenerate one-word titles like "Gene" matching any GENE_X paper);
    - a strong fraction of those tokens appear in the document's actual content
      (this is what catches mislabeled files — filename says X, content is Y);
    - the candidate's first author appears in the content or the filename;
    - the year agrees within 1 (when the filename carries a year).
    """
    toks = {
        t for t in _meta.norm_title(candidate.get("title")).split()
        if len(t) > 2 and t not in _meta.STOPWORDS
    }
    if len(toks) < 3:
        return (False, 0.0)
    low = content.lower()
    overlap = sum(1 for t in toks if t in low) / len(toks)
    authors = candidate.get("authors") or []
    fam = _meta.ascii_slug(authors[0].get("family", "")) if authors else ""
    author_ok = bool(fam) and (fam in _meta.ascii_slug(content) or fam in _meta.ascii_slug(fname_text))
    year_ok = fyear is None or (candidate.get("year") is not None and abs(candidate["year"] - fyear) <= 1)
    return (overlap >= threshold and author_ok and year_ok, overlap)


async def cmd_enrich(args: argparse.Namespace, store: BiblioStore) -> None:
    """Recover metadata for unverified/no-year records via filename → Crossref,
    verifying each candidate against the document's parsed content."""
    import httpx

    from bibliographer import resolvers as _resolvers

    # Manual override: `enrich <citekey> --doi <doi>` forces a specific identifier
    # (for mislabeled files / candidates Crossref-search can't find).
    if args.doi:
        if len(args.citekeys) != 1:
            die("--doi requires exactly one citekey")
        try:
            full = await _resolvers.resolve(args.doi, client=None)
        except _resolvers.ResolveError as e:
            die(str(e))
        try:
            new = await store.reenrich(args.citekeys[0], full, refile=not args.no_refile)
        except KeyError:
            die(f"no article with citekey '{args.citekeys[0]}'")
        await write_index(store)
        print(f"Enriched [{args.citekeys[0]}] -> [{new.get('citekey')}] via {args.doi}")
        return

    threshold = args.threshold
    recs = await store.all_records()
    targets = [r for r in recs if r.get("source") in ("pdf", "file") or not r.get("year")]
    if args.citekeys:
        want = set(args.citekeys)
        targets = [r for r in targets if r.get("citekey") in want]

    client = httpx.AsyncClient(
        timeout=60, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
    )
    applied: list[tuple[str, str, float]] = []
    review: list[dict[str, Any]] = []
    try:
        for r in targets:
            src_name = r.get("original_path") or r.get("title") or ""
            q = filename_query(src_name)
            if not q:
                review.append({"citekey": r.get("citekey"), "reason": "no query", "best": None})
                continue
            cands = await _resolvers.crossref_search(q, client, rows=3)
            content = await store.leading_text(r["document_id"])
            fy = filename_year(src_name)
            # Take the FIRST Crossref-ranked candidate that verifies; track the
            # best-overlap one only for the review display.
            chosen, best, best_ov = None, None, 0.0
            for c in cands:
                ok, ov = verify_candidate(c, content, fy, q, threshold)
                if ov > best_ov:
                    best, best_ov = c, ov
                if ok and c.get("doi"):
                    chosen = c
                    break

            if chosen and not args.dry_run and not args.review:
                full = await _resolvers.resolve(chosen["doi"], client=client)
                new = await store.reenrich(r["citekey"], full, refile=not args.no_refile)
                applied.append((r.get("citekey") or "?", new.get("citekey") or "?", best_ov))
            else:
                review.append({
                    "citekey": r.get("citekey"), "query": q,
                    "score": round(best_ov, 2), "verified": bool(chosen),
                    "best": ({"doi": best.get("doi"), "title": best.get("title"),
                              "year": best.get("year")} if best else None),
                    "content_head": " ".join(content.split())[:120],
                })
    finally:
        await client.aclose()
    if not args.dry_run and applied:
        await write_index(store)

    if args.json:
        emit_json({"applied": applied, "review": review, "threshold": threshold})
        return
    print(f"Enriched (auto-applied): {len(applied)}")
    for old, new, s in applied:
        print(f"  [{old}] -> [{new}]  (score {s:.2f})")
    print(f"\nNeeds review: {len(review)}")
    for v in review:
        b = v.get("best")
        cand = f"{b['year']} {b['doi']} — {(b['title'] or '')[:50]}" if b else "(no candidate)"
        print(f"  [{v['citekey']}] score {v.get('score', 0)} | best: {cand}")
        if b and v.get("content_head"):
            print(f"        content: {v['content_head'][:80]}")


async def cmd_fetch(args: argparse.Namespace, store: BiblioStore) -> None:
    """Acquire and attach a PDF for an existing record (e.g. a citation-only stub).

    Tries the keyless open-access tiers (arXiv, Europe PMC, bioRxiv/medRxiv,
    Unpaywall, Semantic Scholar). If none has it, reports the manual routes
    (institutional browser access, or — only if the user is authorized — a peer
    source); attach a manually-obtained PDF with `--pdf`. See
    references/getting-pdfs.md.
    """
    import httpx

    from bibliographer import resolvers as _resolvers

    rec = await store.get_by_citekey(args.citekey)
    if rec is None:
        die(f"no article with citekey '{args.citekey}'")
    if rec.get("file_path") and not args.force:
        die(f"[{args.citekey}] already has a file; pass --force to replace it")

    if args.pdf:  # attach a manually-obtained PDF (from the browser / a peer source)
        p = Path(args.pdf).expanduser()
        if not p.exists():
            die(f"pdf not found: {p}")
        new = await store.attach_pdf(args.citekey, p, move=args.move)
        await write_index(store)
        print(f"Attached PDF to [{new.get('citekey')}] — {new.get('title')}")
        return

    tmp = Path(tempfile.mkstemp(suffix=".pdf")[1])
    client = httpx.AsyncClient(
        timeout=90, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
    )
    try:
        source = await _resolvers.acquire_oa_pdf(rec, tmp, client)
    finally:
        await client.aclose()

    if source:
        new = await store.attach_pdf(args.citekey, tmp, move=True)
        await write_index(store)
        print(f"Fetched [{new.get('citekey')}] via {source} → attached: {new.get('title')}")
    else:
        tmp.unlink(missing_ok=True)
        ident = rec.get("doi") or rec.get("arxiv_id") or rec.get("pmcid") or "(no identifier)"
        print(f"No open-access PDF found for [{args.citekey}] ({ident}).")
        print("Manual options (see references/getting-pdfs.md):")
        print("  • institutional access via the browser, then:  bib fetch "
              f"{args.citekey} --pdf <downloaded.pdf>")
        print("  • if you are authorized (e.g. institutional access), a peer source by DOI,")
        print(f"    then:  bib fetch {args.citekey} --pdf <downloaded.pdf>")


def article_url(rec: dict[str, Any]) -> str | None:
    """A human-resolvable URL for the article, preferring the DOI.

    Used to seed the manual-fetch worklist: the agent opens this to reach the
    publisher/landing page (institutional access, Tier 3 in getting-pdfs.md).
    """
    doi = (rec.get("doi") or "").strip()
    if doi:
        return f"https://doi.org/{doi}"
    arxiv = rec.get("arxiv_id")
    if arxiv:
        return f"https://arxiv.org/abs/{re.sub(r'v\d+$', '', str(arxiv))}"
    if rec.get("pmcid"):
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{rec['pmcid']}/"
    if rec.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{rec['pmid']}/"
    return None


def stub_records(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The citation-only stubs (abstract searchable, no full text ingested)."""
    return [r for r in recs if r.get("content_state") == "stub"]


def worklist_entry(rec: dict[str, Any]) -> dict[str, Any]:
    """A compact, agent-actionable description of a stub still needing a PDF."""
    ids = {k: rec.get(k) for k in _meta.IDENTIFIER_KEYS if rec.get(k)}
    return {
        "citekey": rec.get("citekey"),
        "title": rec.get("title"),
        "authors": _meta.short_authors(rec),
        "year": rec.get("year"),
        "ids": ids,
        "url": article_url(rec),
    }


async def cmd_backfill(args: argparse.Namespace, store: BiblioStore) -> None:
    """Batch-acquire open-access PDFs for citation-only stubs so their full text
    gets indexed, then hand back a worklist of the stubs that have no OA copy.

    This is the bulk, hands-off counterpart to `fetch`: it runs the same keyless
    open-access ladder (`_resolvers.acquire_oa_pdf`) over *every* stub and
    attaches each PDF it finds. The stubs that come up empty need a human in the
    loop — institutional access via the browser, or (only with the user's
    explicit authorization) a peer source — so those are *reported*, not
    auto-fetched, with identifiers + a resolvable URL for the agent to escalate
    per references/getting-pdfs.md.
    """
    import httpx

    from bibliographer import resolvers as _resolvers

    recs = await store.all_records()
    stubs = stub_records(recs)
    if args.tag:
        stubs = [r for r in stubs if args.tag in (r.get("tags") or [])]
    if args.limit:
        stubs = stubs[: args.limit]

    if not stubs:
        if args.json:
            emit_json({"checked": 0, "fetched": [], "remaining": []})
        else:
            print("No citation-only stubs to backfill — every record has a file.")
        return

    if args.dry_run:
        entries = [worklist_entry(r) for r in stubs]
        if args.json:
            emit_json({"checked": len(stubs), "fetched": [], "remaining": entries})
            return
        print(f"{len(stubs)} citation-only stub(s) would be attempted:")
        for e in entries:
            print(f"  [{e['citekey']}] {e['authors']} ({e['year'] or '????'}) — {e['title']}")
        return

    fetched: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []

    if not args.json:
        print(f"{len(stubs)} citation-only stub(s); attempting open-access fetch…")
    client = httpx.AsyncClient(
        timeout=90, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
    )
    try:
        for r in stubs:
            ck = r.get("citekey")
            tmp = Path(tempfile.mkstemp(suffix=".pdf")[1])
            source: str | None = None
            try:
                source = await _resolvers.acquire_oa_pdf(r, tmp, client)
            except Exception as exc:  # one bad record must not abort the sweep
                if not args.json:
                    print(f"  ! [{ck}] fetch error: {exc}")
            if source:
                new = await store.attach_pdf(ck, tmp, move=True)
                fetched.append({"citekey": ck, "title": new.get("title"), "source": source})
                if not args.json:
                    print(f"  ✓ [{ck}] via {source} — {new.get('title')}")
            else:
                tmp.unlink(missing_ok=True)
                remaining.append(worklist_entry(r))
                if not args.json:
                    print(f"  ✗ [{ck}] no open-access copy — {r.get('title')}")
    finally:
        await client.aclose()

    if fetched:
        await write_index(store)

    if args.json:
        emit_json({"checked": len(stubs), "fetched": fetched, "remaining": remaining})
        return

    print(f"\nFetched {len(fetched)} of {len(stubs)} stub(s).")
    if remaining:
        print(f"{len(remaining)} still need a manual/interactive fetch:")
        for e in remaining:
            ids = ", ".join(f"{k}:{v}" for k, v in e["ids"].items()) or "(no identifier)"
            print(f"  - [{e['citekey']}] {e['title']}")
            print(f"      {ids}" + (f"  → {e['url']}" if e["url"] else ""))
        print("\nEscalate each per references/getting-pdfs.md — institutional access via the")
        print("browser (then `bib fetch <ck> --pdf <file>`), or, only with the user's explicit")
        print("authorization, a peer source by DOI. Confirm the file is the right paper first.")


def _metrics_stale(rec: dict[str, Any], days: int) -> bool:
    """True if a record's OpenAlex ``metrics`` are missing an ``as_of`` stamp or
    that stamp is older than ``days`` days. A record with no metrics at all is a
    *backfill*, not a staleness case, so it returns False here."""
    m = rec.get("metrics") or {}
    if not m:
        return False
    as_of = m.get("as_of")
    if not as_of:
        return True  # metrics predate the as_of field — treat as stale
    try:
        stamped = datetime.strptime(as_of, "%Y-%m-%d").date()
    except ValueError:
        return True
    return (datetime.now(timezone.utc).date() - stamped).days > days


async def cmd_refresh(args: argparse.Namespace, store: BiblioStore) -> None:
    """Backfill (or refresh) OpenAlex citation metrics on existing records.

    The default scope is the *gap*: records that have a DOI/PMID but no `metrics`
    block yet — papers added before enrichment existed, or where it failed on a
    network blip. `--stale DAYS` additionally re-fetches records whose
    `metrics.as_of` is older than DAYS, and `--all` re-fetches every eligible
    record; both bypass the 30-day resolver cache so the numbers actually move.

    OpenAlex is queried one record at a time behind a polite-pool throttle, so a
    big sweep is slow but never abusive. `--limit` (default 500) caps a run;
    re-runs are cheap because finished records are skipped and unchanged works
    stay cached. Records with neither a DOI nor a PMID can't be enriched and are
    reported, not attempted.
    """
    import httpx

    from bibliographer import resolvers as _resolvers

    recs = await store.all_records()
    if args.tag:
        recs = [r for r in recs if args.tag in (r.get("tags") or [])]

    refresh_existing = args.all or args.stale is not None
    eligible: list[dict[str, Any]] = []
    ineligible = 0
    for r in recs:
        if not (r.get("doi") or r.get("pmid")):
            if not r.get("metrics"):
                ineligible += 1  # no identifier to look up, and nothing stamped yet
            continue
        if not r.get("metrics"):
            eligible.append(r)  # a gap — always backfill
        elif args.all or (args.stale is not None and _metrics_stale(r, args.stale)):
            eligible.append(r)

    total_eligible = len(eligible)
    capped = bool(args.limit and args.limit > 0 and total_eligible > args.limit)
    if capped:
        eligible = eligible[: args.limit]

    if not eligible:
        if args.json:
            emit_json({"checked": 0, "updated": [], "failed": [],
                       "remaining": 0, "ineligible": ineligible})
        else:
            tail = f" — {ineligible} record(s) have no DOI/PMID to enrich." if ineligible else "."
            print(f"No records need metrics{tail}")
        return

    if args.dry_run:
        entries = [{"citekey": r.get("citekey"), "title": r.get("title"),
                    "as_of": (r.get("metrics") or {}).get("as_of")} for r in eligible]
        if args.json:
            emit_json({"checked": len(eligible), "would_update": entries,
                       "remaining": total_eligible - len(eligible), "ineligible": ineligible})
            return
        verb = "refreshed" if refresh_existing else "backfilled"
        print(f"{len(eligible)} record(s) would be {verb}:")
        for e in entries:
            stamp = f"as of {e['as_of']}" if e["as_of"] else "no metrics yet"
            print(f"  [{e['citekey']}] {e['title']}  ({stamp})")
        if capped:
            print(f"… and {total_eligible - len(eligible)} more (capped at --limit {args.limit}).")
        return

    updated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if not args.json:
        verb = "Refreshing" if refresh_existing else "Backfilling"
        print(f"{verb} OpenAlex metrics for {len(eligible)} record(s)…")
    client = httpx.AsyncClient(
        timeout=60, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
    )
    try:
        for r in eligible:
            ck = r.get("citekey")
            rec = dict(r)
            try:
                ok = await _resolvers.enrich_openalex(rec, client, refresh=refresh_existing)
            except Exception as exc:  # noqa: BLE001 — one bad record must not abort the sweep
                failed.append({"citekey": ck, "error": str(exc)})
                if not args.json:
                    print(f"  ! [{ck}] {exc}")
                continue
            if ok and rec.get("metrics"):
                new = await store.update_metrics(ck, rec["metrics"], rec.get("cited_by_count"))
                m = new.get("metrics") or {}
                updated.append({"citekey": ck, "cited_by_count": new.get("cited_by_count"),
                                "fwci": m.get("fwci"), "as_of": m.get("as_of")})
                if not args.json:
                    cb = new.get("cited_by_count")
                    fwci = f" · FWCI {m['fwci']:.2f}" if m.get("fwci") is not None else ""
                    print(f"  ✓ [{ck}] cited-by {cb if cb is not None else '—'}{fwci}"
                          f" · as of {m.get('as_of')}")
            else:
                failed.append({"citekey": ck, "error": "no OpenAlex match"})
                if not args.json:
                    print(f"  ✗ [{ck}] no OpenAlex match — {r.get('title')}")
    finally:
        await client.aclose()

    if updated:
        await write_index(store)

    remaining = total_eligible - len(eligible)
    if args.json:
        emit_json({"checked": len(eligible), "updated": updated, "failed": failed,
                   "remaining": remaining, "ineligible": ineligible})
        return
    print(f"\nUpdated {len(updated)} of {len(eligible)} record(s); "
          f"{len(failed)} unmatched/failed.")
    if remaining:
        print(f"{remaining} more eligible — re-run to continue (re-runs skip finished records), "
              f"or raise/drop --limit ({args.limit}).")
    if ineligible:
        print(f"{ineligible} record(s) have no DOI/PMID and can't be enriched from OpenAlex.")


async def cmd_refs(args: argparse.Namespace, store: BiblioStore) -> None:
    """Backfill each paper's outgoing reference list (citation edges) from OpenAlex.

    Stores ``references`` — the OpenAlex ids of the works a paper cites — plus the
    paper's own OpenAlex id, on every record that has a DOI/PMID/OpenAlex id but no
    reference list yet. This is the citation graph `gaps`, `cluster`, and `outliers`
    read. A published paper's reference list is static, so this is a one-time
    backfill; re-runs skip finished records (`--all` re-pulls past the cache).
    OpenAlex is queried one record at a time behind a polite-pool throttle, so a big
    sweep is slow but never abusive; `--limit` (default 500) caps a run.
    """
    import httpx

    from bibliographer import resolvers as _resolvers

    recs = await store.all_records()
    if args.tag:
        recs = [r for r in recs if args.tag in (r.get("tags") or [])]
    if args.citekeys:
        want = set(args.citekeys)
        recs = [r for r in recs if r.get("citekey") in want]

    eligible: list[dict[str, Any]] = []
    ineligible = 0
    for r in recs:
        has_id = (
            r.get("openalex_id") or (r.get("metrics") or {}).get("openalex_id")
            or r.get("doi") or r.get("pmid")
        )
        if not has_id:
            if "references" not in r:
                ineligible += 1  # nothing to look the work up by
            continue
        if "references" not in r or args.all:
            eligible.append(r)

    total_eligible = len(eligible)
    capped = bool(args.limit and args.limit > 0 and total_eligible > args.limit)
    if capped:
        eligible = eligible[: args.limit]

    if not eligible:
        if args.json:
            emit_json({"checked": 0, "updated": [], "failed": [],
                       "remaining": 0, "ineligible": ineligible})
        else:
            tail = f" — {ineligible} record(s) have no DOI/PMID/OpenAlex id." if ineligible else "."
            print(f"No records need references{tail}")
        return

    if args.dry_run:
        entries = [{"citekey": r.get("citekey"), "title": r.get("title")} for r in eligible]
        if args.json:
            emit_json({"checked": len(eligible), "would_update": entries,
                       "remaining": total_eligible - len(eligible), "ineligible": ineligible})
            return
        print(f"{len(eligible)} record(s) would have references fetched:")
        for e in entries:
            print(f"  [{e['citekey']}] {e['title']}")
        if capped:
            print(f"… and {total_eligible - len(eligible)} more (capped at --limit {args.limit}).")
        return

    updated: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if not args.json:
        print(f"Fetching OpenAlex references for {len(eligible)} record(s)…")
    client = httpx.AsyncClient(
        timeout=60, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
    )
    try:
        for r in eligible:
            ck = r.get("citekey")
            rec = dict(r)
            try:
                ok = await _resolvers.enrich_references(rec, client, refresh=args.all)
            except Exception as exc:  # noqa: BLE001 — one bad record must not abort the sweep
                failed.append({"citekey": ck, "error": str(exc)})
                if not args.json:
                    print(f"  ! [{ck}] {exc}")
                continue
            if ok:
                new = await store.update_references(
                    ck, rec.get("references") or [], rec["references_as_of"],
                    rec.get("openalex_id"),
                )
                n = len(new.get("references") or [])
                updated.append({"citekey": ck, "references": n})
                if not args.json:
                    print(f"  ✓ [{ck}] {n} reference(s)")
            else:
                failed.append({"citekey": ck, "error": "no OpenAlex match"})
                if not args.json:
                    print(f"  ✗ [{ck}] no OpenAlex match — {r.get('title')}")
    finally:
        await client.aclose()

    remaining = total_eligible - len(eligible)
    if args.json:
        emit_json({"checked": len(eligible), "updated": updated, "failed": failed,
                   "remaining": remaining, "ineligible": ineligible})
        return
    print(f"\nFetched references for {len(updated)} of {len(eligible)} record(s); "
          f"{len(failed)} unmatched/failed.")
    if remaining:
        print(f"{remaining} more eligible — re-run to continue (re-runs skip finished records), "
              f"or raise/drop --limit ({args.limit}).")
    if ineligible:
        print(f"{ineligible} record(s) have no DOI/PMID/OpenAlex id to look up.")


async def cmd_gaps(args: argparse.Namespace, store: BiblioStore) -> None:
    """Papers your library cites a lot but doesn't contain — candidates to add.

    Reads the per-paper reference lists (`bib refs` first) and ranks the external
    works cited by the most library papers, then labels the top candidates from
    OpenAlex (title/year/citations) so you can judge and `bib add` the keepers.
    Ranking is offline; only labelling touches the network (`--no-network` skips it).
    """
    from bibliographer import citations as _citations

    recs = await store.all_records()
    if args.tag:
        recs = [r for r in recs if args.tag in (r.get("tags") or [])]
    if not _citations.has_reference_data(recs):
        msg = "no reference data yet — run `bib refs` to fetch citation edges first."
        if args.json:
            emit_json({"candidates": [], "note": msg})
        else:
            print(f"No gaps to report: {msg}")
        return

    cands = _citations.gap_candidates(recs, min_citing=args.min_citing, limit=args.limit)

    labels: dict[str, dict[str, Any]] = {}
    if cands and not args.no_network:
        import httpx

        from bibliographer import resolvers as _resolvers

        client = httpx.AsyncClient(
            timeout=60, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
        )
        try:
            labels = await _resolvers.fetch_openalex_works(client, [c["work_id"] for c in cands])
        except Exception:  # noqa: BLE001 — labelling is best-effort
            labels = {}
        finally:
            await client.aclose()

    for c in cands:
        lbl = labels.get(c["work_id"])
        if lbl:
            c.update({k: lbl[k] for k in ("title", "year", "cited_by_count", "doi") if k in lbl})

    if args.json:
        emit_json({"candidates": cands, "min_citing": args.min_citing})
        return
    if not cands:
        print(f"No external work is cited by ≥{args.min_citing} of your papers "
              "(lower --min-citing, or run `bib refs` on more records).")
        return
    print(f"{len(cands)} candidate(s) cited by ≥{args.min_citing} of your papers but not in the library:\n")
    for c in cands:
        title = c.get("title") or "(unlabelled — OpenAlex " + c["work_id"] + ")"
        year = c.get("year") or "????"
        cb = c.get("cited_by_count")
        cb_s = f" · cited-by {cb}" if cb is not None else ""
        doi_s = f"  doi:{c['doi']}" if c.get("doi") else f"  {c['work_id']}"
        print(f"  [{c['citing_count']}×] ({year}) {title}{cb_s}")
        print(f"        cited by: {', '.join(c['citing_citekeys'])}{doi_s}")
    print("\nBank the keepers with `bib add <doi-or-id>` (judge each — high citing-count "
          "≠ on-topic).")


async def cmd_cluster(args: argparse.Namespace, store: BiblioStore) -> None:
    """Group papers into topic areas by bibliographic coupling (shared references).

    Two papers couple when they cite some of the same works; connected groups above
    `--min-shared` form a cluster. Reports the clusters; `--write-tags` records each
    as a `cluster:<n>` tag on its members (replacing any prior `cluster:*` tags).
    Run `bib refs` first to populate references.
    """
    from bibliographer import citations as _citations

    recs = await store.all_records()
    if args.tag:
        recs = [r for r in recs if args.tag in (r.get("tags") or [])]
    if not _citations.has_reference_data(recs):
        msg = "no reference data yet — run `bib refs` to fetch citation edges first."
        if args.json:
            emit_json({"clusters": [], "unclustered": [], "note": msg})
        else:
            print(f"No clusters: {msg}")
        return

    result = _citations.coupling_clusters(recs, min_shared=args.min_shared)
    clusters: list[list[str]] = result["clusters"]
    unclustered: list[str] = result["unclustered"]
    title_of = {r["citekey"]: r.get("title") for r in recs if r.get("citekey")}

    written = 0
    if args.write_tags:
        assign = {ck: f"cluster:{i}" for i, g in enumerate(clusters, 1) for ck in g}
        for r in recs:
            ck = r.get("citekey")
            if not ck:
                continue
            old = [t for t in (r.get("tags") or []) if t.startswith("cluster:")]
            new = assign.get(ck)
            add = [new] if new and new not in old else []
            remove = [t for t in old if t != new]
            if add or remove:
                await store.set_tags(ck, add=add, remove=remove)
                written += 1
        if written:
            await write_index(store)

    if args.json:
        emit_json({
            "clusters": [
                {"cluster": i, "size": len(g),
                 "members": [{"citekey": ck, "title": title_of.get(ck)} for ck in g]}
                for i, g in enumerate(clusters, 1)
            ],
            "unclustered": unclustered,
            "min_shared": args.min_shared,
            "tags_written": written if args.write_tags else None,
        })
        return
    if not clusters:
        print(f"No clusters at --min-shared {args.min_shared} "
              f"({len(unclustered)} paper(s) uncoupled). Try a lower threshold.")
        return
    print(f"{len(clusters)} cluster(s) at --min-shared {args.min_shared} "
          f"({len(unclustered)} unclustered):\n")
    for i, g in enumerate(clusters, 1):
        print(f"  cluster:{i}  ({len(g)} papers)")
        for ck in g:
            print(f"      [{ck}] {title_of.get(ck) or ''}")
    if args.write_tags:
        print(f"\nWrote cluster tags to {written} record(s).")
    else:
        print("\nRe-run with --write-tags to record these as `cluster:<n>` tags.")


async def cmd_outliers(args: argparse.Namespace, store: BiblioStore) -> None:
    """Flag papers that may be off-topic / added by mistake.

    A paper is suspicious when it shares (almost) no references with the rest of
    the library *and* neither cites nor is cited by any other paper in it. Reads
    the reference lists (`bib refs` first) and prints a worklist; removes nothing.
    """
    from bibliographer import citations as _citations

    recs = await store.all_records()
    if args.tag:
        recs = [r for r in recs if args.tag in (r.get("tags") or [])]
    if not _citations.has_reference_data(recs):
        msg = "no reference data yet — run `bib refs` to fetch citation edges first."
        if args.json:
            emit_json({"isolated": [], "checked": 0, "note": msg})
        else:
            print(f"No outliers to report: {msg}")
        return

    report = _citations.isolation_report(recs, min_shared=args.min_shared)
    isolated = [e for e in report if e["isolated"]]
    no_refs = sum(1 for e in report if not e["has_references"])

    if args.json:
        emit_json({"checked": len(report), "isolated": isolated,
                   "no_references": no_refs, "min_shared": args.min_shared,
                   "report": report if args.all else None})
        return
    if not isolated:
        print(f"No citation-isolated papers at --min-shared {args.min_shared} "
              f"({len(report)} checked).")
    else:
        print(f"{len(isolated)} possibly off-topic paper(s) — isolated in the citation graph:\n")
        for e in isolated:
            print(f"  [{e['citekey']}] {e.get('title') or ''}")
            print(f"        max shared refs: {e['max_coupling']} · intra-library edges: "
                  f"{e['intra_edges']} · references: {e['reference_count']}")
        print("\nThese cite (almost) nothing the rest of your library cites, and neither "
              "cite nor are cited by it. Review before `bib rm` — removes nothing on its own.")
    if no_refs:
        print(f"\n({no_refs} paper(s) have no reference data — run `bib refs` to include them.)")


async def cmd_list(args: argparse.Namespace, store: BiblioStore) -> None:
    recs = await store.all_records()
    if getattr(args, "content", False):
        # Attach a leading-content excerpt to each record (one library open) so a
        # semantic audit can compare metadata vs. the document's actual text
        # without re-opening libkit per record. See references/auditing.md.
        for r in recs:
            if r.get("document_id"):
                txt = await store.leading_text(r["document_id"], chunks=4)
                r["content_excerpt"] = " ".join(txt.split())[: args.chars]
    if args.json:
        emit_json(recs)
    else:
        print_table(recs)
        print(f"\n{len(recs)} article(s) in {store.home}")


def search_haystack(rec: dict[str, Any]) -> str:
    """The lowercased blob `bib search` matches against.

    One string per record: title, authors, venue, abstract, then the citekey and
    identifiers, then tags. Matching is substring, so a multi-word query only hits
    when those words appear verbatim and adjacent *somewhere in this blob* —
    including across a field boundary ("EEG Signature Urraca" spans title→authors).

    The identifiers are here because they are what a caller reaches for when it
    wants a DEFINITIVE answer — "is 10.1002/aur.1284 in the library?". Omitting
    them made that the single most misleading zero this command could produce: a
    present paper returned `words matching nothing: 10.1002/aur.1284`, which reads
    as proof of absence precisely because a DOI is exact.
    """
    parts = [str(rec.get(k) or "") for k in ("title", "authors_text", "venue", "abstract")]
    parts += [str(rec.get(k) or "") for k in ("citekey", *_meta.IDENTIFIER_KEYS)]
    parts += rec.get("tags") or []
    return " ".join(parts).lower()


# How many records a zero-result search SHOWS for its most distinctive word.
# Three is enough to recognise the paper you were after without turning a miss
# into a wall of output. Length is bounded per title instead (_elide_middle).
_ZERO_HINT_CANDIDATES = 3
_ZERO_HINT_TITLE_CHARS = 140


def _elide_middle(text: str, limit: int = _ZERO_HINT_TITLE_CHARS) -> str:
    """Shorten a title while keeping BOTH ends — recognition lives at the tail.

    Academic title heads are generic ("The Interstitial Duplication 15q11.2-q13
    Syndrome Includes…") and the distinguishing hook trails it ("…and a
    Characteristic EEG Signature"). A plain prefix cut therefore removes exactly
    the words that would let a reader recognise the paper they were looking for,
    which would defeat the point of showing the record at all.
    """
    if len(text) <= limit:
        return text
    head = (limit - 3) * 2 // 3
    return f"{text[:head].rstrip()} … {text[-(limit - 3 - head):].lstrip()}"


def _distinctive_word(counts: dict[str, int]) -> str | None:
    """The query word most worth showing records for: the *rarest* one that hits.

    Rarity is the whole signal. A word matching 3 records is nearly an
    identification; one matching 400 ("eeg") tells the reader nothing. Ties keep
    query order. Returns None when no word matched anything.
    """
    hits = {t: n for t, n in counts.items() if n}
    return min(hits, key=lambda t: hits[t]) if hits else None


def _zero_diagnostic(
    query: str | None, pool: list[dict[str, Any]], *, matched_before_filters: int = 0
) -> dict[str, Any]:
    """Structured explanation of a zero-result `search`, so it is never misread
    as *absence*.

    Counting each word's own records tells the caller which half of the failure it
    is looking at: words that hit plenty prove the *phrasing* failed, and only words
    matching nothing are (weak) evidence the library lacks something. But a count
    still asks the caller to run a second search to see what it found — so we also
    resolve the rarest hitting word to actual records and SHOW them. That is what
    ends the "is this paper here?" question in one call instead of two.

    ``matched_before_filters`` is how many records the free-text query matched
    *before* --author/--tag/--year narrowed them away; non-zero means the query was
    fine and the filters emptied it, which is a completely different fix.
    """
    diag: dict[str, Any] = {"absence_supported": False, "searched": len(pool)}
    if matched_before_filters:
        diag["removed_by_filters"] = matched_before_filters
    if query and pool:
        counts = {
            t: sum(1 for r in pool if t in search_haystack(r)) for t in query.lower().split()
        }
        diag["per_word"] = counts
        word = _distinctive_word(counts)
        if word:
            hits = [r for r in pool if word in search_haystack(r)]
            diag["closest_word"] = word
            diag["closest_word_total"] = len(hits)
            diag["candidates"] = [
                {"citekey": r.get("citekey"), "title": r.get("title"), "year": r.get("year")}
                for r in hits[:_ZERO_HINT_CANDIDATES]
            ]
    return diag


def _format_zero_diagnostic(query: str | None, diag: dict[str, Any]) -> str:
    """Render :func:`_zero_diagnostic` for stderr — candidates FIRST.

    The records come before the counts and the explanation because they are the
    answer: a reader who recognises a title stops there and never needs the rest.
    """
    what = f"no match for {query!r}" if query else "no records match"
    headline = (
        f"{what} — this is NOT evidence the paper is absent "
        f"(searched {diag['searched']} record(s))."
    )
    lines = [headline]
    if not diag["searched"]:
        lines.append("  (no records to match — did --tag/--year filter everything out?)")
        return "\n".join(lines)
    if diag.get("removed_by_filters"):
        lines.append(
            f"  the query DID match {diag['removed_by_filters']} record(s); "
            "--author/--tag/--year then removed all of them — loosen the filters, "
            "not the query."
        )
    if diag.get("candidates"):
        total = diag["closest_word_total"]
        shown = len(diag["candidates"])
        more = f" (showing {shown})" if total > shown else ""
        lines.append(
            f"  closest single word {diag['closest_word']!r} matches "
            f"{total} record(s){more} — is one of these yours?"
        )
        for c in diag["candidates"]:
            title = _elide_middle(c["title"] or "(untitled)")
            lines.append(f"    [{c['citekey']}] ({c['year'] or '????'}) {title}")
    counts = diag.get("per_word") or {}
    if counts:
        present = [f"{t} ({n})" for t, n in counts.items() if n]
        missing = [t for t, n in counts.items() if not n]
        if present:
            lines.append("  words that DO match records: " + ", ".join(present))
        if missing:
            lines.append("  words matching nothing: " + ", ".join(missing))
    if len(counts) > 1:
        lines.append(
            "  `bib search` tests your words as ONE literal substring of "
            "title+authors+venue+abstract+tags, so wording and order must be verbatim. "
            "Retry with a SINGLE distinctive word (surname, gene, unusual noun), or use "
            "`bib query` to search inside the papers."
        )
    elif counts:
        lines.append("  Try a shorter/looser word, or `bib query` to search inside the papers.")
    return "\n".join(lines)


async def cmd_search(args: argparse.Namespace, store: BiblioStore) -> None:
    filters: dict[str, Any] = {}
    if args.tag:
        filters["tags"] = args.tag.lower()
    if args.year:
        filters["year"] = str(args.year)
    pool = await store.all_records(filters=filters or None)
    recs = pool
    matched_by: str | None = None

    if args.query:
        q = args.query.lower()
        recs = [r for r in pool if q in search_haystack(r)]
        tokens = q.split()
        matched_by = "phrase" if recs else None
        # A phrase-only matcher makes every natural-language query ("Urraca EEG
        # beta power") a silent zero even when the paper is right there. Rather
        # than let a caller read that as absence, relax to all-words-anywhere and
        # say so — same principle as `query`'s loud [FTS-only] fallback.
        if not recs and len(tokens) > 1:
            recs = [r for r in pool if all(t in search_haystack(r) for t in tokens)]
            if recs:
                matched_by = "all-words"
                warn(
                    f"no record contains the literal phrase {args.query!r}; showing the "
                    f"{len(recs)} record(s) containing all {len(tokens)} words anywhere in "
                    "their metadata instead (`bib search` is a substring matcher, not a "
                    "ranked search engine)."
                )
    matched_query = len(recs)
    if args.author:
        a = args.author.lower()
        recs = [r for r in recs if a in (r.get("authors_text") or "").lower()]

    # Diagnose the zero AFTER every filter, so an --author/--tag that emptied a
    # perfectly good query is reported as such rather than blamed on the wording.
    diagnostic = None
    if not recs:
        diagnostic = _zero_diagnostic(
            args.query, pool, matched_before_filters=matched_query if args.query else 0
        )
        warn(_format_zero_diagnostic(args.query, diagnostic))

    if args.json:
        applied = {k: v for k, v in
                   (("tag", args.tag), ("year", args.year), ("author", args.author)) if v}
        # An object, not a bare list: a zero-result `[]` is the single most
        # absence-implying thing this command can emit, and --json is the path an
        # agent takes. Ship the epistemics WITH the results — stderr is not part
        # of a parsed payload. Mirrors `query`'s {mode, semantic, results} shape.
        payload: dict[str, Any] = {
            "query": args.query,
            "filters": applied,
            "matched_by": matched_by,  # "phrase" | "all-words" | null
            "searched": len(pool),
            "count": len(recs),
            "results": recs,
        }
        if diagnostic is not None:
            payload["diagnostic"] = diagnostic
        emit_json(payload)
    else:
        print_table(recs)
        print(f"\n{len(recs)} result(s)")


async def cmd_show(args: argparse.Namespace, store: BiblioStore) -> None:
    rec = await store.get_by_citekey(args.citekey)
    if rec is None:
        die(f"no article with citekey '{args.citekey}'")
    if args.bibtex:
        print(_meta.to_bibtex(rec))
        return
    if args.json:
        emit_json(rec)
        return
    print(f"citekey : {rec.get('citekey')}")
    print(f"title   : {rec.get('title')}")
    print(f"authors : {rec.get('authors_text')}")
    print(f"year    : {rec.get('year')}")
    print(f"venue   : {rec.get('venue')}")
    for f in (*_meta.IDENTIFIER_KEYS, "source_url", "source", "content_state"):
        if rec.get(f):
            print(f"{f:<13}: {rec[f]}")
    if rec.get("cited_by_count") is not None:
        print(f"cited-by     : {rec['cited_by_count']}")
    if "references" in rec:
        n = len(rec.get("references") or [])
        stamp = f" (as of {rec['references_as_of']})" if rec.get("references_as_of") else ""
        print(f"references   : {n} outgoing citation(s){stamp}")
    m = rec.get("metrics") or {}
    if m:
        bits = []
        if m.get("is_retracted"):
            bits.append("⚠ RETRACTED")
        if m.get("fwci") is not None:
            bits.append(f"FWCI {m['fwci']:.2f}")
        if m.get("citation_percentile") is not None:
            bits.append(f"pctile {round(m['citation_percentile'] * 100)}%")
        if m.get("open_access"):
            bits.append(f"OA {m['open_access']}")
        if m.get("as_of") and bits:
            bits.append(f"as of {m['as_of']}")
        if bits:
            print(f"impact       : {' · '.join(bits)}")
        v = m.get("venue") or {}
        vbits = []
        if v.get("type"):
            vbits.append(v["type"])
        if v.get("in_doaj"):
            vbits.append("DOAJ")
        if v.get("indexed_in_scopus"):
            vbits.append("Scopus")
        if v.get("impact_2yr") is not None:
            vbits.append(f"IF~{v['impact_2yr']:.1f}")
        if v.get("h_index") is not None:
            vbits.append(f"h-index {v['h_index']}")
        if vbits:
            print(f"journal      : {' · '.join(vbits)}")
    if rec.get("tags"):
        print(f"tags    : {', '.join(rec['tags'])}")
    print(f"file    : {rec.get('file_path') or '(none — citation-only)'}")
    print(f"doc id  : {rec.get('document_id')}")
    if rec.get("abstract"):
        print(f"\nabstract:\n{rec['abstract']}")


# Default excerpt length for `bib text` (~1k tokens): enough to orient / see the
# abstract+intro without a naive call dumping a whole paper (~20k tokens). --all / --offset
# / --chars escalate from here.
_DEFAULT_TEXT_CHARS = 4000


async def cmd_text(args: argparse.Namespace, store: BiblioStore) -> None:
    """Print one paper's full stored library text — the exact string a scientist
    ``[lit:]`` quote-check reads. To author a verbatim ``source(citekey, quote=...)``
    the quote must appear in this text, so the author needs to SEE it.

    Faithful to the grounding path (scientist/grounding/_load_paper): dump the libkit
    document's text via the same ``leading_text`` accessor (every chunk) when the record
    has a document, else fall back to the abstract. A citation-only **stub** still has a
    libkit document — a generated stub holding the metadata + abstract — so its dumped
    text is exactly what a quote-check sees, but it contains no full body; the stderr note
    flags that (``content_state == 'stub'``) so the author knows quotes can only come from
    the abstract. Default prints a bounded excerpt (the first ``_DEFAULT_TEXT_CHARS``) so a
    naive call never dumps ~20k tokens into a context window; --offset/--chars page through
    it and --all prints the whole text (the clean-pipe path, e.g. ``bib text K --all | grep``).
    The size note goes to stderr so the pipe stays pure and flags when more text remains.
    NOTE: shell ``grep`` is a coarse locator, not the quote-check — it does not fold unicode
    dashes / markdown emphasis / split whitespace the way grounding does, so a grep miss is
    not authoritative.
    """
    rec = await store.get_by_citekey(args.citekey)
    if rec is None:
        die(f"no article with citekey '{args.citekey}'")
    doc_id = rec.get("document_id")
    if doc_id:
        text = await store.leading_text(doc_id, chunks=100000)
    else:
        text = rec.get("abstract") or ""
    is_stub = rec.get("content_state") == "stub" or not doc_id
    mode = "stub" if is_stub else "fulltext"

    total = len(text)
    offset = max(0, getattr(args, "offset", 0) or 0)
    if getattr(args, "all", False):
        length = None  # whole text from offset
    else:
        length = args.chars if args.chars is not None else _DEFAULT_TEXT_CHARS
    window = text[offset:] if length is None else text[offset : offset + length]
    shown = len(window)
    shown_end = offset + shown
    remaining = total - shown_end        # chars past this window (>= 0)
    truncated = remaining > 0            # more stored text remains beyond what was shown

    if args.json:
        emit_json({
            "citekey": rec.get("citekey"),
            "content_state": rec.get("content_state"),
            "mode": mode,
            "text": window,
            "content_offset": offset,
            "content_chars": shown,      # kept for back-compat (== shown)
            "content_total": total,      # kept for back-compat (== total)
            "total": total,              # full stored-text length in chars
            "shown": shown,              # chars in this window
            "remaining": remaining,      # chars past this window (0 when complete)
            "truncated": truncated,      # True when more text remains than was shown
        })
        return

    approx_k = round(total / 4000, 1)  # ~4 chars/token, then ÷1000 for k-tokens
    tok = "<1k" if approx_k < 0.1 else f"~{approx_k:g}k"
    escalate = " — use --all (or --offset N / --chars N) for the rest"
    where = (f"first {shown:,} of {total:,} chars" if offset == 0
             else f"chars {offset + 1:,}–{shown_end:,} of {total:,}")
    if is_stub:
        # Citation-only: no full body was ingested — the whole stored text is metadata +
        # abstract, so quotes can only come from the abstract. (Distinct from a truncated
        # full-text excerpt: there is no more body to page to.)
        note = (f"[{args.citekey}] citation-only stub — no full text ingested; the stored text "
                f"is metadata + abstract only ({total:,} chars). Quotes can only come from the abstract.")
        if truncated:  # abstract itself longer than the excerpt cap (rare)
            note += f" Showing {where};{escalate}"
    elif truncated:
        # FULL-TEXT record whose excerpt is just the opening — signpost loudly that the
        # rest of the body IS stored and how to reach it, so it doesn't read as "abstract only".
        note = (f"[{args.citekey}] FULL TEXT is stored ({total:,} chars, {tok} tokens) — "
                f"showing {where}; {remaining:,} not shown{escalate}")
    else:
        # Whole stored text shown (fits under the cap, or --all): no truncation notice.
        note = f"[{args.citekey}] stored text: complete — {total:,} chars ({tok} tokens)"
    print(note, file=sys.stderr)
    if not window.strip():
        print(f"(no stored text at offset {offset})", file=sys.stderr)
    print(window)


async def cmd_tag(args: argparse.Namespace, store: BiblioStore) -> None:
    try:
        rec = await store.set_tags(args.citekey, add=args.add or [], remove=args.remove or [])
    except KeyError:
        die(f"no article with citekey '{args.citekey}'")
    await write_index(store)
    print(f"[{args.citekey}] tags: {', '.join(rec.get('tags') or []) or '(none)'}")


async def cmd_rm(args: argparse.Namespace, store: BiblioStore) -> None:
    try:
        await store.remove(args.citekey, delete_file=args.delete_file)
    except KeyError:
        die(f"no article with citekey '{args.citekey}'")
    await write_index(store)
    print(f"Removed [{args.citekey}]" + (" and its file" if args.delete_file else ""))


async def cmd_export(args: argparse.Namespace, store: BiblioStore) -> None:
    if args.citekeys:
        recs = []
        for ck in args.citekeys:
            rec = await store.get_by_citekey(ck)
            if rec is None:
                die(f"no article '{ck}'")
            recs.append(rec)
    else:
        recs = await store.all_records()
    print("\n\n".join(_meta.to_bibtex(r) for r in recs))


async def cmd_query(args: argparse.Namespace, store: BiblioStore) -> None:
    """Semantic / full-text search *inside* the papers (libkit hybrid query).

    When no embedding backend is configured the store opens FTS-only; rather than
    silently returning BM25 results dressed up as semantic ones, we warn LOUDLY on
    stderr (naming why and how to restore semantic search) and run an explicit
    full-text query. Results carry an ``[FTS-only]`` marker so the degraded mode
    is unmistakable to a human or an LLM reading the output."""
    fts_only = not store.semantic_available
    if fts_only:
        reason = store.embedder_reason or "no embedding backend is configured"
        warn(
            "semantic search is UNAVAILABLE — no embedder could be built "
            f"({reason}).\n"
            "  Falling back to FULL-TEXT (BM25) search only; results are keyword "
            "matches, not semantic.\n"
            "  To restore semantic search, install a local model "
            "(libkit[fancychunk-torch], or [fancychunk-mlx] on Apple Silicon) or set "
            "BIBLIOGRAPHER_EMBEDDING=remote with DEEPINFRA_API_KEY."
        )
    results = await store.query(args.text, limit=args.limit, fts_only=fts_only)
    mode = "fts" if fts_only else "hybrid"
    if args.json:
        emit_json(
            {
                "mode": mode,  # "hybrid" (semantic+FTS) or "fts" (FTS-only fallback)
                "semantic": not fts_only,
                "results": [
                    {
                        "score": r.score,
                        "title": r.chunk.title,
                        "citekey": (r.chunk.metadata or {}).get("citekey"),
                        "document_id": r.chunk.document_id,
                        "text": r.chunk.text,
                    }
                    for r in results
                ],
            }
        )
        return
    if fts_only:
        print("[FTS-only] keyword (BM25) matches — semantic search unavailable")
    if not results:
        print("(no matches)")
        return
    for r in results:
        ck = (r.chunk.metadata or {}).get("citekey", "?")
        print(f"  [{ck}] {r.chunk.title or '(untitled)'}  (score {r.score:.3f})")
        snippet = " ".join(r.chunk.text.split())[:200]
        print(f"        {snippet}…")


def _disc_authors(rec: dict[str, Any]) -> str:
    """Compact ``First et al.`` from a discovery record's structured authors."""
    fams = [a.get("family") for a in (rec.get("authors") or []) if a.get("family")]
    if not fams:
        return "?"
    if len(fams) == 1:
        return fams[0]
    if len(fams) == 2:
        return f"{fams[0]} & {fams[1]}"
    return f"{fams[0]} et al."


def print_discoveries(results: list[dict[str, Any]]) -> None:
    if not results:
        print("(no candidates)")
        return
    for i, r in enumerate(results, 1):
        year = r.get("year") or "????"
        title = r.get("title") or "(untitled)"
        mark = " ✓in-library" if r.get("in_library") else ""
        print(f"  {i:>3}. {_disc_authors(r)} ({year}) — {title}{mark}")
        if r.get("venue"):
            print(f"       {r['venue']}")
        # Rank signals for the "highly ranked" banking bar: corroboration
        # (how many sources surfaced it), raw citations, and — from OpenAlex —
        # field-normalized impact (percentile, FWCI where 1.0 = field-average).
        srcs = r.get("found_in") or []
        rank = [f"sources: {', '.join(srcs)} ({len(srcs)})"]
        if r.get("citation_percentile") is not None:
            rank.append(f"pctile: {round(r['citation_percentile'] * 100)}%")
        if r.get("fwci") is not None:
            rank.append(f"FWCI: {r['fwci']:.2f}")
        if r.get("cited_by_count") is not None:
            rank.append(f"cited-by: {r['cited_by_count']}")
        print(f"       {' · '.join(rank)}")
        if r.get("doi"):
            ident = f"doi: {r['doi']}"
        elif r.get("arxiv_id"):
            ident = f"arXiv: {r['arxiv_id']}"
        elif r.get("pmid"):
            ident = f"PMID: {r['pmid']}"
        else:
            ident = ""
        if ident:
            print(f"       {ident}")


async def cmd_discover(args: argparse.Namespace, store: BiblioStore) -> None:
    """Find candidate papers across scholarly search APIs (a *recall pass*).

    Fans out the query over the selected providers, merges/de-dupes the results,
    and flags which are already in the library. It banks **nothing**: the caller
    judges the candidates against the banking bar (responsive to the task, or
    germane-and-highly-ranked — see references/literature-search.md) and banks the
    keepers with `bib add <id> <id> …`. `discover` has no model of the task or the
    program, only keyword relevance and the rank signals it reports here.
    """
    import httpx

    from bibliographer import discovery as _discovery
    from bibliographer import resolvers as _resolvers

    filters = _discovery.Filters(
        year_min=args.year_min, year_max=args.year_max, open_access=args.open_access
    )
    sources = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None

    client = httpx.AsyncClient(
        timeout=30, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
    )
    try:
        try:
            out = await _discovery.discover(
                args.query, sources=sources, limit=args.limit, filters=filters, client=client
            )
        except ValueError as e:
            die(str(e))
        results = out["results"]

        # Flag candidates already in the library (so a sweep shows net-new).
        for r in results:
            dup = await store.find_duplicate(r)
            r["in_library"] = dup is not None
            if dup is not None:
                r["library_citekey"] = dup.get("citekey")
    finally:
        await client.aclose()

    if args.json:
        emit_json(out)
        return

    rep = ", ".join(f"{k}: {v}" for k, v in out["sources"].items())
    print(f"Sources — {rep}")
    print(f"{len(results)} merged candidate(s):\n")
    print_discoveries(results)
    net_new = sum(1 for r in results if not r.get("in_library"))
    print(
        f"\n{net_new} not yet in the library. Judge each against the banking bar "
        "(responsive, or germane-and-highly-ranked), then bank the keepers: "
        "`bib add <id> <id> …`."
    )


async def cmd_dedupe(args: argparse.Namespace, store: BiblioStore) -> None:
    recs = await store.all_records()
    clusters: dict[str, list[str]] = {}
    for r in recs:
        keys = []
        for idk in _meta.IDENTIFIER_KEYS:
            if r.get(idk):
                keys.append(f"{idk}:{str(r[idk]).lower()}")
        nt = _meta.norm_title(r.get("title"))
        if nt and r.get("year"):
            keys.append(f"title:{nt}:{r['year']}")
        for k in keys:
            clusters.setdefault(k, []).append(r.get("citekey", r.get("document_id", "?")))
    groups = []
    seen: set[frozenset[str]] = set()
    for k, members in clusters.items():
        cks = frozenset(members)
        if len(cks) > 1 and cks not in seen:
            seen.add(cks)
            groups.append((k, sorted(cks)))
    if args.json:
        emit_json([{"reason": k, "citekeys": cks} for k, cks in groups])
        return
    if not groups:
        print("No duplicates found.")
        return
    print(f"Found {len(groups)} duplicate group(s):")
    for k, cks in groups:
        print(f"  [{k.split(':', 1)[0]}] {', '.join(cks)}")
    print("\nReview with `bib show <citekey>`; remove extras with `bib rm <citekey>`.")


async def cmd_audit(args: argparse.Namespace, store: BiblioStore) -> None:
    """Deeper correctness review than `check`: per-record flags for misfiling,
    thin metadata, unverified/stub status, and content-vs-title mismatch.

    Emits a structured worklist (use --json) so a periodic hygiene pass — or
    several parallel agents — can pick up and fix what's flagged. See
    references/auditing.md for the parallel-agent procedure.
    """
    recs = await store.all_records()
    findings: list[dict[str, Any]] = []
    for r in recs:
        flags: list[str] = []
        for f in ("title", "authors_text", "year"):
            if not r.get(f):
                flags.append(f"missing:{f}")
        if r.get("content_state") == "stub":
            flags.append("stub")
        if r.get("source") in ("pdf", "file"):
            flags.append("unverified")
        if not r.get("doi") and not r.get("arxiv_id") and r.get("source") not in ("pdf", "file"):
            flags.append("no-identifier")

        fp = r.get("file_path")
        if fp:
            if not (store.home / fp).exists():
                flags.append("file-missing")
            elif str(Path(fp).parent) != f"papers/{_fileorg.author_dir(r)}":
                flags.append("misfiled")  # on-disk folder != what current metadata implies
        elif r.get("content_state") != "stub":
            flags.append("no-file")

        # Content-vs-title overlap — a SOFT heuristic (false-positives when a PDF's
        # leading pages are boilerplate, e.g. an ethics statement). A low score
        # means "have an agent actually read this one", not "definitely wrong".
        # Skip with --fast. Authoritative content verification is the parallel-
        # agent pass in references/auditing.md.
        if not args.fast and r.get("title") and r.get("document_id"):
            toks = {t for t in _meta.norm_title(r["title"]).split() if len(t) > 3 and t not in _meta.STOPWORDS}
            if toks:
                content = (await store.leading_text(r["document_id"], chunks=6)).lower()
                overlap = sum(1 for t in toks if t in content) / len(toks)
                if overlap < 0.30:
                    flags.append(f"low-content-overlap:{overlap:.2f}")

        if flags:
            findings.append({
                "citekey": r.get("citekey"), "flags": flags,
                "title": (r.get("title") or "")[:60], "file_path": fp,
                "original_path": r.get("original_path"),
            })

    if args.json:
        emit_json({"checked": len(recs), "flagged": len(findings), "findings": findings})
        return
    from collections import Counter
    by_flag: Counter[str] = Counter(f.split(":")[0] for v in findings for f in v["flags"])
    print(f"Audited {len(recs)} records — {len(findings)} flagged.")
    for k, n in by_flag.most_common():
        print(f"  {n:4d}  {k}")
    print()
    for v in findings[:60]:
        print(f"  [{v['citekey']}] {', '.join(v['flags'])}")
    if len(findings) > 60:
        print(f"  … and {len(findings) - 60} more (use --json for the full worklist)")


async def cmd_compact(args: argparse.Namespace, home: Path) -> None:
    """Reclaim disk bloat in the library's ``catalog.duckdb``.

    libkit keeps everything — documents, chunks, the VSS HNSW index, the FTS
    snapshot — in one DuckDB file, and DuckDB never shrinks a file in place
    (freed blocks are reused, never returned to the OS) while the experimental
    persistent HNSW index re-appends on every CHECKPOINT under churn. The file
    therefore balloons (observed: 225 GB for ~1,700 papers) past its logical
    size, and VACUUM/CHECKPOINT reclaim ~nothing. The only fix that actually
    shrinks the file is rewriting it fresh — ``COPY FROM DATABASE`` into a new
    file rebuilds a compact HNSW + FTS index — which is what this does, then
    verifies and atomically swaps. See bibliographer/compact.py for the why and
    the measured comparison of alternatives.

    This runs with the library *closed* and operates on the file directly, so it
    refuses (and dry-run reports) when a writer holds libkit's write lock, and
    takes that lock itself for the duration.
    """
    from bibliographer import compact as _compact

    db = home / "catalog.duckdb"
    if not db.exists():
        die(f"no bibliographer library at {home} (catalog.duckdb missing) — "
            "run `bib init` or `bib add <id>` first.")

    try:
        result = _compact.compact(
            home,
            dry_run=args.dry_run,
            keep_backup=args.keep_backup,
        )
    except _compact.CompactError as e:
        die(str(e))

    if args.json:
        emit_json(result)
        return

    if args.dry_run:
        size_h = result["size_before_h"]
        print(f"catalog: {result['catalog']}")
        print(f"current size: {size_h}")
        bs = result.get("block_stats") or {}
        if "error" not in bs:
            used = bs.get("used_bytes")
            free = bs.get("free_bytes")
            frac = bs.get("free_fraction")
            if used is not None:
                print(f"  used blocks: {_compact.human_size(used)} · "
                      f"free blocks: {_compact.human_size(free)}"
                      + (f" ({frac * 100:.1f}% free)" if isinstance(frac, float) else ""))
        if result.get("writer_active"):
            print("  ! a writer currently holds the lock — compact would refuse until it finishes")
        print(f"\n{result.get('reclaimable_hint', '')}")
        print(f"\nDRY RUN — nothing changed. Would: {result['would_do']}")
        return

    print(f"Compacted {result['catalog']}")
    print(f"  {result['documents']} document(s), {result['chunks']} chunk(s)")
    print(f"  {result['size_before_h']} → {result['size_after_h']}  "
          f"(reclaimed {result['reclaimed_h']}) in {result['elapsed_s']}s")
    if result.get("backup"):
        print(f"  backup kept: {result['backup']}")


async def cmd_check(args: argparse.Namespace, store: BiblioStore) -> None:
    recs = await store.all_records()
    issues: list[str] = []
    referenced: set[str] = set()
    for r in recs:
        ck = r.get("citekey", r.get("document_id", "?"))
        if not r.get("title"):
            issues.append(f"[{ck}] missing title")
        if not r.get("year"):
            issues.append(f"[{ck}] missing year")
        if r.get("content_state") == "stub":
            issues.append(f"[{ck}] citation-only — no file ingested yet")
        if r.get("source") in ("pdf", "file"):
            issues.append(f"[{ck}] metadata from the file only — unverified")
        fp = r.get("file_path")
        if fp:
            referenced.add(Path(fp).name)
            full = store.home / fp
            if not full.exists():
                issues.append(f"[{ck}] file missing: {fp}")
            elif sha256_file(full) != r.get("document_id"):
                issues.append(f"[{ck}] file bytes changed since ingest: {fp}")
    orphans = []
    papers = store.home / "papers"
    if papers.exists():
        for f in papers.rglob("*"):
            if f.is_file() and f.name not in referenced:
                orphans.append(str(f.relative_to(store.home)))
    issues += [f"orphan file (no catalog entry): {o}" for o in orphans]
    if args.json:
        emit_json({"issues": issues, "checked": len(recs), "orphans": orphans})
        return
    if not issues:
        print(f"OK — {len(recs)} article(s), no integrity issues.")
        return
    print(f"Found {len(issues)} issue(s) across {len(recs)} article(s):")
    for i in issues:
        print(f"  - {i}")


# --------------------------------------------------------------------------- #
# argument parsing / dispatch
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bib", description="Manage a collection of academic articles.")
    p.add_argument("--home", type=Path, default=None,
                   help="library directory (default: $BIBLIOGRAPHER_HOME, "
                        f"else {FALLBACK_HOME})")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the library directory, catalog, and viewer").set_defaults(func=cmd_init)

    sub.add_parser("viewer", help="(re)generate the self-contained HTML viewer (index.html)").set_defaults(func=cmd_viewer)

    sp = sub.add_parser("add", help="add one or more articles by DOI, arXiv/PMID/PMCID/S2 id, or PDF path")
    sp.add_argument("identifiers", nargs="+", metavar="identifier",
                    help="one or more: DOI, arXiv id, PMID, PMCID, Semantic Scholar id, or .pdf path "
                         "(pass several to bank a sweep's keepers in one call)")
    sp.add_argument("--pdf", help="attach this PDF file (for a single metadata-only identifier)")
    sp.add_argument("--tags", help="comma-separated tags")
    sp.add_argument("--move", action="store_true", help="move the file into the library instead of copying")
    sp.add_argument("--no-fetch", action="store_true", help="do not auto-download an open-access PDF")
    sp.add_argument("--force", action="store_true", help="add even if it looks like a duplicate")
    sp.add_argument("--no-network", action="store_true", help="do not hit metadata APIs")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("import", help="bulk-import a directory tree of papers (dry-run first)")
    sp.add_argument("directory", help="folder to walk recursively for papers")
    sp.add_argument("--dry-run", action="store_true", help="resolve + plan paths, but move/ingest nothing")
    sp.add_argument("--copy", action="store_true", help="copy files into the tree instead of moving")
    sp.add_argument("--exclude", action="append", help="skip files whose relative path contains this substring (repeatable)")
    sp.add_argument("--no-network", action="store_true", help="do not hit metadata APIs")
    sp.add_argument("--limit", type=int, help="only process the first N files (for a quick preview)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("fetch", help="acquire & attach an open-access PDF for a record (e.g. a citation-only stub)")
    sp.add_argument("citekey")
    sp.add_argument("--pdf", help="attach this manually-obtained PDF instead of searching")
    sp.add_argument("--move", action="store_true", help="move the --pdf file into the library instead of copying")
    sp.add_argument("--force", action="store_true", help="replace an existing attached file")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("backfill", help="batch-attach open-access PDFs to citation-only stubs; list the rest for manual fetch")
    sp.add_argument("--tag", help="only stubs carrying this tag")
    sp.add_argument("--limit", type=int, help="attempt at most N stubs")
    sp.add_argument("--dry-run", action="store_true", help="list the stubs that would be attempted; fetch nothing")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_backfill)

    sp = sub.add_parser("refresh", help="backfill/refresh OpenAlex citation metrics (cited-by, FWCI) onto existing records")
    sp.add_argument("--all", action="store_true", help="re-fetch every eligible record, not just those missing metrics (bypasses the cache)")
    sp.add_argument("--stale", type=int, metavar="DAYS", help="also re-fetch records whose metrics are older than DAYS (bypasses the cache)")
    sp.add_argument("--tag", help="only records carrying this tag")
    sp.add_argument("--limit", type=int, default=500, help="attempt at most N records (0 = no cap; default 500)")
    sp.add_argument("--dry-run", action="store_true", help="list what would be fetched; change nothing")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_refresh)

    sp = sub.add_parser("refs", help="backfill each paper's outgoing reference list (citation edges) from OpenAlex")
    sp.add_argument("citekeys", nargs="*", help="only these citekeys (default: all eligible)")
    sp.add_argument("--all", action="store_true", help="re-fetch references for every eligible record, not just those missing them (bypasses the cache)")
    sp.add_argument("--tag", help="only records carrying this tag")
    sp.add_argument("--limit", type=int, default=500, help="attempt at most N records (0 = no cap; default 500)")
    sp.add_argument("--dry-run", action="store_true", help="list what would be fetched; change nothing")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_refs)

    sp = sub.add_parser("gaps", help="external works your library cites a lot but doesn't contain (candidates to add)")
    sp.add_argument("--min-citing", type=int, default=2, help="only works cited by at least N library papers (default 2)")
    sp.add_argument("--limit", type=int, default=30, help="show at most N candidates (default 30; 0 = all)")
    sp.add_argument("--tag", help="only consider records carrying this tag")
    sp.add_argument("--no-network", action="store_true", help="skip OpenAlex label lookup; print bare work ids")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_gaps)

    sp = sub.add_parser("cluster", help="group papers into topic areas by bibliographic coupling (shared references)")
    sp.add_argument("--min-shared", type=int, default=2, help="min shared references to couple two papers (default 2)")
    sp.add_argument("--tag", help="only cluster records carrying this tag")
    sp.add_argument("--write-tags", action="store_true", help="record each cluster as a `cluster:<n>` tag (replaces prior cluster:* tags)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_cluster)

    sp = sub.add_parser("outliers", help="flag possibly off-topic papers (citation-isolated from the rest of the library)")
    sp.add_argument("--min-shared", type=int, default=2, help="coupling below this counts as isolated (default 2)")
    sp.add_argument("--tag", help="only consider records carrying this tag")
    sp.add_argument("--all", action="store_true", help="include the full per-paper report in --json output")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_outliers)

    sp = sub.add_parser("enrich", help="recover metadata for unverified/no-year records (filename -> Crossref, content-verified)")
    sp.add_argument("citekeys", nargs="*", help="only these citekeys (default: all unverified)")
    sp.add_argument("--dry-run", action="store_true", help="show proposed matches, change nothing")
    sp.add_argument("--review", action="store_true", help="list candidates without applying any")
    sp.add_argument("--doi", help="force a specific DOI/identifier for one citekey (manual fix)")
    sp.add_argument("--no-refile", action="store_true", help="update metadata but don't move the PDF")
    sp.add_argument("--threshold", type=float, default=0.5, help="min content-match score to auto-apply (default 0.5)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_enrich)

    sp = sub.add_parser("discover", help="find candidate papers across scholarly search APIs (OpenAlex, S2, Europe PMC, PubMed, Crossref, arXiv)")
    sp.add_argument("query", help="free-text research question / topic")
    sp.add_argument("--sources", help="comma-separated subset (default: all). Available: openalex, semantic_scholar, europepmc, pubmed, crossref, arxiv")
    sp.add_argument("--limit", type=int, default=25, help="max results per source (default 25)")
    sp.add_argument("--year-min", type=int, help="earliest publication year")
    sp.add_argument("--year-max", type=int, help="latest publication year")
    sp.add_argument("--open-access", action="store_true", help="restrict to open-access papers (where the source supports it)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser(
        "search",
        help="find records by a LITERAL SUBSTRING of their metadata (title/authors/venue/abstract/ids/tags)",
        description=(
            "Substring lookup over catalog metadata — not ranked, not semantic. The query is "
            "lowercased and tested as ONE literal substring of each record's "
            "title + authors + venue + abstract, then its citekey and identifiers "
            "(DOI/arXiv/PMID/…), then its tags — all concatenated into one blob."
        ),
        epilog=(
            "PUT WHAT YOU KNOW IN THE FIELD THAT HOLDS IT. The free-text query is a substring\n"
            "match over everything at once; --author/--year/--tag test one field and cannot be\n"
            "defeated by wording. A surname belongs in --author, not in a sentence:\n"
            "  bib search 'Urraca interstitial duplication EEG'  ✗ a sentence, at a substring matcher\n"
            "  bib search --author urraca                        ✓ the surname, in the surname field\n"
            "\n"
            "Free-text matching, concretely:\n"
            "  bib search Urraca                          ✓ phrase hit — one distinctive word\n"
            "  bib search 10.1002/aur.1284                ✓ identifiers and citekeys match too\n"
            "  bib search 'Characteristic EEG Signature'  ✓ phrase hit — verbatim and adjacent\n"
            "  bib search 'EEG Signature Urraca'          ✓ phrase hit — spans title→authors\n"
            "  bib search 'Urraca interstitial EEG'       ~ phrase MISSES; relaxed retry finds it\n"
            "  bib search 'distinctive brainwave pattern' ✗ paraphrase — nothing here is semantic\n"
            "\n"
            "When the literal phrase misses, search retries as all-words-anywhere and warns on\n"
            "stderr that it relaxed. That rescues most natural-language queries, but it is still\n"
            "an AND over substrings: one absent or paraphrased word zeroes the whole query.\n"
            "\n"
            "Records added as citation-only stubs (or without a fetched abstract) offer only a\n"
            "title/authors/venue/ids/tags to match, so most of a paper's content is invisible\n"
            "here. Use `bib query` to search INSIDE the papers.\n"
            "\n"
            "A zero result is NOT evidence a paper is absent. On a zero, search prints to stderr\n"
            "the RECORDS its rarest matching word found — read those titles before concluding\n"
            "anything — plus every word's own record count (words that hit plenty mean your\n"
            "PHRASING failed) and whether --author/--tag/--year is what emptied the result.\n"
            "Under --json all of that ships inside the payload as a `diagnostic` object, since\n"
            "an empty `results` list otherwise reads as absence. Establishing that a paper is\n"
            "really absent takes THREE steps, not one: (1) `search --author <surname>`, (2)\n"
            "`bib query` on a distinctive phrase (raise --limit; a top-N is not exhaustive),\n"
            "(3) an identifier lookup if you have a DOI/PMID/arXiv id — the only exact test.\n"
            "Say which of the three you ran when you report something missing.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp.add_argument("query", nargs="?",
                    help="literal substring to match (a single distinctive word works best)")
    sp.add_argument("--author")
    sp.add_argument("--year")
    sp.add_argument("--tag")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("list", help="list all articles")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--content", action="store_true", help="include a content excerpt per record (for semantic audit)")
    sp.add_argument("--chars", type=int, default=1500, help="content excerpt length with --content (default 1500)")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="show one article")
    sp.add_argument("citekey")
    sp.add_argument("--bibtex", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("text", help="print one paper's stored library text (the text a [lit:] quote-check reads)")
    sp.add_argument("citekey")
    sp.add_argument("--offset", type=int, default=0, help="start at this character offset (for paging)")
    sp.add_argument("--chars", type=int, default=None,
                    help=f"print at most N chars from --offset (default {_DEFAULT_TEXT_CHARS}; use --all for the whole text)")
    sp.add_argument("--all", action="store_true", help="print the entire stored text (no excerpt cap)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_text)

    sp = sub.add_parser("tag", help="add/remove tags on an article")
    sp.add_argument("citekey")
    sp.add_argument("--add", action="append")
    sp.add_argument("--remove", action="append")
    sp.set_defaults(func=cmd_tag)

    sp = sub.add_parser("rm", help="remove an article from the catalog")
    sp.add_argument("citekey")
    sp.add_argument("--delete-file", action="store_true", help="also delete the file on disk")
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("export", help="export BibTeX for some or all articles (stdout)")
    sp.add_argument("citekeys", nargs="*")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("query", help="semantic/full-text search inside the papers (libkit)")
    sp.add_argument("text")
    sp.add_argument("--limit", type=int, default=8)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("dedupe", help="find probable duplicate articles")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_dedupe)

    sp = sub.add_parser("check", help="check catalog integrity")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("audit", help="deep correctness review: misfiling, thin metadata, content/title mismatch")
    sp.add_argument("--fast", action="store_true", help="skip the content-vs-title check (no chunk reads)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("compact", help="reclaim catalog.duckdb disk bloat (rewrite the store; rebuilds a compact HNSW/FTS index)")
    sp.add_argument("--dry-run", action="store_true", help="report current size + bloat estimate and what would happen; change nothing")
    sp.add_argument("--keep-backup", action="store_true", help="keep the old file as catalog.duckdb.bloated-bak after a successful compaction")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_compact)

    return p


# Subcommands that only READ the store open it ``read_only=True`` (libkit >=0.4.0):
# a read-only open takes no exclusive write lock, so many of them run concurrently
# (parallel grounding / literature-research subagents, `bib query`/`text`) instead
# of serialising on the write lock. Everything not listed here opens read-write —
# the safe default, since a *write* command opened read-only would crash with
# ``ReadOnlyStore`` while a read command opened read-write merely keeps today's
# lock behaviour. Verified each below performs no store writes:
#   search/list/show/text/query/export — pure metadata/chunk reads.
#   dedupe — only *reports* duplicate groups (the user removes extras via `bib rm`).
#   check  — reads records + hashes on-disk files; never repairs.
#   audit  — reads records + leading chunk text; emits a worklist, never repairs.
#   gaps   — reads reference lists; OpenAlex label lookup is network, not a store write.
#   outliers — reads reference lists; emits a worklist, never writes.
# Deliberately NOT here (they write the store or a managed file): init, add, import,
# fetch, backfill, refresh, refs, enrich, discover, tag, rm — `cluster` (writes
# `cluster:*` tags with --write-tags) — and `viewer`, which regenerates index.html.
_READ_ONLY_COMMANDS = frozenset({
    "search", "list", "show", "text", "query", "export", "dedupe", "check", "audit",
    "gaps", "outliers",
})

# Commands that run libkit *vector* search and so want an embedder. Only `query`
# does; every other read is metadata/FTS and opens without an embedder (so it
# works with no embedding backend configured). `query` still opens — FTS-only,
# with a loud warning — when no embedder is available (see cmd_query).
_SEMANTIC_COMMANDS = frozenset({"query"})

# Commands that must run with NO open libkit Library handle. `compact` rewrites
# catalog.duckdb directly (via a separate DuckDB connection) and swaps the file,
# which an open Library would never tolerate; it manages libkit's write lock
# itself. These receive the resolved ``home`` path instead of a ``BiblioStore``.
_NO_STORE_COMMANDS = frozenset({"compact"})


async def dispatch(args: argparse.Namespace) -> None:
    # Load .env BEFORE resolving the default home: $BIBLIOGRAPHER_HOME often lives in
    # ~/.env, so it must be in the environment before _default_home() reads it. An
    # explicit --home always wins and skips this inference.
    _load_dotenv(Path(args.home).expanduser() if args.home else None)
    home = Path(args.home).expanduser() if args.home else _default_home()

    # `compact` rewrites catalog.duckdb out from under libkit, so it must run
    # with NO Library handle open. It takes libkit's write lock itself and
    # operates on the file directly; receive the home path, not an open store.
    if args.command in _NO_STORE_COMMANDS:
        await args.func(args, home)
        return

    read_only = args.command in _READ_ONLY_COMMANDS
    # Only `query` runs vector/semantic search, so only it asks for an embedder.
    # Every other read opens FTS-only (no embedder construction) and so works
    # even with no embedding backend configured — see BiblioStore.open.
    want_semantic = args.command in _SEMANTIC_COMMANDS
    try:
        store = BiblioStore.open(home, read_only=read_only, want_semantic=want_semantic)
    except EmbedderConfigError as e:
        die(str(e))
    except FileNotFoundError as e:
        # A read-only open never creates the store; a first-run read lands here.
        die(str(e))
    try:
        await args.func(args, store)
    finally:
        # Managed-library invariant: never leave empty folders behind.
        try:
            store.prune_empty_dirs()
        except Exception:  # noqa: BLE001 — cleanup must never mask the real result
            pass
        await store.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    asyncio.run(dispatch(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
