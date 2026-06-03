#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["libkit>=0.2.2", "pypdf>=4.0", "httpx>=0.27", "diskcache>=5.6", "platformdirs>=4.0"]
# ///
"""bib - a libkit-backed bibliographer for a collection of academic articles.

The collection lives in a "library" directory (default: ~/.bibliographer,
override with --home or BIBLIOGRAPHER_HOME) containing:

    <library>/
      catalog.duckdb     the libkit store (the single source of truth)
      papers/            the organized files, one per article
      index.html         a self-contained, searchable HTML viewer (auto-regenerated)

libkit (>=0.2.2) IS the store: there is no separate bibliographer database.
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
from pathlib import Path
from typing import Any, NoReturn

# Sibling modules live next to this script; uv puts the script dir on sys.path,
# but we make it explicit so `bib.py` is robust to how it's invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _fileorg  # noqa: E402
import _meta  # noqa: E402
from _store import BiblioStore, EmbedderConfigError  # noqa: E402

INGESTIBLE_EXTS = {".pdf", ".md", ".markdown", ".docx", ".doc", ".pptx", ".ppt", ".odt"}

DEFAULT_HOME = Path(os.environ.get("BIBLIOGRAPHER_HOME", Path.home() / ".bibliographer"))


def _load_dotenv(home: Path) -> None:
    """Load KEY=VALUE pairs from .env files into the environment (stdlib only).

    Search order: the library ``home``, the current directory, every parent of
    this script (so a repo-root .env is found), then ``~/.env`` (the
    consolidated location). Real environment variables and earlier files win —
    a later file never overrides a value already set.
    """
    here = Path(__file__).resolve()
    candidates = [
        home / ".env",
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
    import _viewer

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
    import _resolvers

    kind, value = classify(target)
    if kind == "identifier":
        if no_network:
            die(f"--no-network given but {value!r} needs an online lookup")
        try:
            rec = await _resolvers.resolve(value, client=client)
        except _resolvers.ResolveError as e:
            die(str(e))
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
) -> dict[str, Any]:
    """Shared add/import core: dedup -> citekey -> fetch-then-ingest -> organize -> store.

    Returns a result dict with ``status`` one of added | merged | merged-dup |
    duplicate, plus the stored ``record``.
    """
    import _resolvers

    rec = dict(rec)
    if not force:
        dup = await store.find_duplicate(rec)
        if dup is not None:
            if on_duplicate == "merge":
                return {"status": "merged-dup", "record": await store.merge_duplicate(dup, rec)}
            return {"status": "duplicate", "record": dup}

    rec["citekey"] = await store.unique_citekey(_meta.make_citekey(rec))

    tmp: Path | None = None
    if src is None and fetch:
        tmp = Path(tempfile.mkstemp(suffix=".pdf")[1])
        own = client is None
        import httpx

        client = client or httpx.AsyncClient(
            timeout=60, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
        )
        try:
            pdf_source = await _resolvers.acquire_oa_pdf(rec, tmp, client)
            if pdf_source:
                src, rec["pdf_source"] = tmp, pdf_source
            else:
                tmp.unlink(missing_ok=True)
                tmp = None
        finally:
            if own:
                await client.aclose()

    file_path: Path | None = None
    if src is not None:
        rec.setdefault("original_path", str(src))
        file_path = _fileorg.place(store.home, rec, src, move=move or tmp is not None)

    return await store.add(rec, file_path=file_path, force=True)


async def cmd_add(args: argparse.Namespace, store: BiblioStore) -> None:
    rec, src = await resolve_target(
        args.identifier,
        pdf_override=Path(args.pdf).expanduser().resolve() if args.pdf else None,
        no_network=args.no_network,
    )
    if args.tags:
        rec["tags"] = sorted(set(rec.get("tags") or []) | {
            t.strip() for t in args.tags.split(",") if t.strip()
        })

    # An explicit duplicate without --force should stop before any file work.
    if not args.force:
        dup = await store.find_duplicate(rec)
        if dup is not None:
            if args.json:
                emit_json({"status": "duplicate", "record": dup})
            else:
                die(
                    f"duplicate of [{dup.get('citekey')}] ({dup.get('title')}). "
                    "Use --force to add anyway."
                )
            return

    result = await ingest_record(
        store, rec, src=src, move=args.move, fetch=not args.no_fetch,
        force=args.force, on_duplicate="report",
    )
    await write_index(store)
    rec_out = result["record"]
    if args.json:
        emit_json(result)
        return
    verb = "Merged into" if result["status"].startswith("merged") else "Added"
    print(f"{verb} [{rec_out.get('citekey')}] {rec_out.get('title')}")
    if rec_out.get("sniffed_from"):
        print(f"  note: identifier recovered from the PDF ({rec_out['sniffed_from']})")
    if rec_out.get("source") in ("pdf", "file"):
        print("  note: metadata from the file only — unverified; add a DOI/arXiv id to enrich")
    if rec_out.get("content_state") == "stub" and rec_out.get("oa_pdf_url"):
        print(f"  note: open-access PDF available; re-run without --no-fetch to attach it")


def topic_tag(root: Path, f: Path) -> str | None:
    """Derive a provisional ``topic:<slug>`` tag from a file's top-level folder.

    The pile's existing topic folders (e.g. ``02_ube3a_biology``) become tags,
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

    import _resolvers

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

                online = rec.get("source") in ("crossref", "arxiv", "semantic_scholar")
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
      (kills degenerate one-word titles like "Ube" matching any UBE3A paper);
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

    import _resolvers

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

    import _resolvers

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


async def cmd_search(args: argparse.Namespace, store: BiblioStore) -> None:
    filters: dict[str, Any] = {}
    if args.tag:
        filters["tags"] = args.tag.lower()
    if args.year:
        filters["year"] = str(args.year)
    recs = await store.all_records(filters=filters or None)

    if args.query:
        q = args.query.lower()

        def hit(r: dict[str, Any]) -> bool:
            hay = " ".join(
                str(r.get(k) or "") for k in ("title", "authors_text", "venue", "abstract")
            ).lower()
            hay += " " + " ".join(r.get("tags") or []).lower()
            return q in hay

        recs = [r for r in recs if hit(r)]
    if args.author:
        a = args.author.lower()
        recs = [r for r in recs if a in (r.get("authors_text") or "").lower()]

    if args.json:
        emit_json(recs)
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
    if rec.get("tags"):
        print(f"tags    : {', '.join(rec['tags'])}")
    print(f"file    : {rec.get('file_path') or '(none — citation-only)'}")
    print(f"doc id  : {rec.get('document_id')}")
    if rec.get("abstract"):
        print(f"\nabstract:\n{rec['abstract']}")


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
    """Semantic / full-text search *inside* the papers (libkit hybrid query)."""
    results = await store.query(args.text, limit=args.limit)
    if args.json:
        emit_json(
            [
                {
                    "score": r.score,
                    "title": r.chunk.title,
                    "citekey": (r.chunk.metadata or {}).get("citekey"),
                    "document_id": r.chunk.document_id,
                    "text": r.chunk.text,
                }
                for r in results
            ]
        )
        return
    if not results:
        print("(no matches)")
        return
    for r in results:
        ck = (r.chunk.metadata or {}).get("citekey", "?")
        print(f"  [{ck}] {r.chunk.title or '(untitled)'}  (score {r.score:.3f})")
        snippet = " ".join(r.chunk.text.split())[:200]
        print(f"        {snippet}…")


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
    p.add_argument("--home", type=Path, default=DEFAULT_HOME,
                   help=f"library directory (default: {DEFAULT_HOME})")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the library directory, catalog, and viewer").set_defaults(func=cmd_init)

    sub.add_parser("viewer", help="(re)generate the self-contained HTML viewer (index.html)").set_defaults(func=cmd_viewer)

    sp = sub.add_parser("add", help="add an article by DOI, arXiv/PMID/PMCID/S2 id, or PDF path")
    sp.add_argument("identifier", help="DOI, arXiv id, PMID, PMCID, Semantic Scholar id, or a .pdf path")
    sp.add_argument("--pdf", help="attach this PDF file (for metadata-only identifiers)")
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

    sp = sub.add_parser("enrich", help="recover metadata for unverified/no-year records (filename -> Crossref, content-verified)")
    sp.add_argument("citekeys", nargs="*", help="only these citekeys (default: all unverified)")
    sp.add_argument("--dry-run", action="store_true", help="show proposed matches, change nothing")
    sp.add_argument("--review", action="store_true", help="list candidates without applying any")
    sp.add_argument("--doi", help="force a specific DOI/identifier for one citekey (manual fix)")
    sp.add_argument("--no-refile", action="store_true", help="update metadata but don't move the PDF")
    sp.add_argument("--threshold", type=float, default=0.5, help="min content-match score to auto-apply (default 0.5)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_enrich)

    sp = sub.add_parser("search", help="search catalog metadata (title/authors/venue/abstract/tags)")
    sp.add_argument("query", nargs="?")
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

    return p


async def dispatch(args: argparse.Namespace) -> None:
    home = Path(args.home).expanduser()
    _load_dotenv(home)
    try:
        store = BiblioStore.open(home)
    except EmbedderConfigError as e:
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
