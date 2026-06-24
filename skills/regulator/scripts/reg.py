#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["libkit>=0.5.0", "pypdf>=4.0", "httpx>=0.27", "diskcache>=5.6", "platformdirs>=4.0"]
# ///
"""reg — a libkit-backed library of FDA regulatory documents.

The collection lives in a "library" directory (default: ~/.regulator, override
with --home or REGULATOR_HOME) containing:

    <library>/
      catalog.duckdb        the libkit store (the single source of truth)
      docs/                 the organized documents, grouped by type
      guidance_index.json   a cached copy of the FDA guidance corpus
      index.html            a self-contained, searchable HTML viewer

libkit (>=0.5.0) IS the store: there is no separate regulator database. Each
regulatory document is one libkit document; every field — doc_type, title, FDA
org, application number, status — lives in the document's free-form metadata.

Sources (one subcommand group each):
  drugsfda   openFDA metadata + accessdata approval-package PDFs (clean API)
  guidance   the FDA guidance-document corpus (one JSON feed; may be bot-gated)
  adcomm     advisory-committee briefing docs / transcripts / rosters (scraped)
  personnel  biographical dossiers from review-PDF signatures + research

Run `reg <command> --help` for details on any command.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from regulator import meta as _meta  # noqa: E402
from regulator import viewer as _viewer  # noqa: E402
from regulator import importer as _importer  # noqa: E402
from regulator.store import RegStore, EmbedderConfigError  # noqa: E402
from regulator.sources import drugsfda, guidance, adcomm, personnel  # noqa: E402

FALLBACK_HOME = Path.home() / ".regulator"


# --------------------------------------------------------------------------- #
# env + home
# --------------------------------------------------------------------------- #
def _default_home() -> Path:
    h = os.environ.get("REGULATOR_HOME")
    return Path(h).expanduser() if h else FALLBACK_HOME


def _load_dotenv(home: Path | None = None) -> None:
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
    print(f"warning: {msg}", file=sys.stderr)


def emit_json(obj: Any) -> None:
    import json
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _mailto() -> str | None:
    return os.environ.get("REGULATOR_MAILTO")


def _download_dir(home: Path) -> Path:
    d = home / ".download"
    d.mkdir(parents=True, exist_ok=True)
    return d


def print_records(records: list[dict[str, Any]]) -> None:
    if not records:
        print("(no documents)")
        return
    for r in records:
        print("  " + _meta.one_line(r))


# --------------------------------------------------------------------------- #
# shared library commands
# --------------------------------------------------------------------------- #
async def cmd_init(args: argparse.Namespace, home: Path) -> None:
    store = RegStore.open(home)
    try:
        recs = await store.all_records()
        _viewer.write(home, recs)
    finally:
        await store.close()
    print(f"initialized regulator library at {home}")


async def cmd_list(args: argparse.Namespace, home: Path) -> None:
    store = RegStore.open(home, read_only=True)
    try:
        filters = {"doc_type": args.type} if args.type else None
        recs = await store.all_records(filters)
    finally:
        await store.close()
    recs.sort(key=lambda r: (r.get("doc_type") or "", r.get("citekey") or ""))
    if args.json:
        emit_json(recs)
    else:
        print_records(recs)
        print(f"\n{len(recs)} document(s)")


async def cmd_show(args: argparse.Namespace, home: Path) -> None:
    store = RegStore.open(home, read_only=True)
    try:
        rec = await store.get_by_citekey(args.citekey)
    finally:
        await store.close()
    if rec is None:
        die(f"no document with citekey {args.citekey!r}")
    if args.json:
        emit_json(rec)
    else:
        for k in sorted(rec):
            if k.startswith("_"):
                continue
            print(f"{k:20} {rec[k]}")


async def cmd_search(args: argparse.Namespace, home: Path) -> None:
    """Substring metadata search over the catalog (title + key fields)."""
    store = RegStore.open(home, read_only=True)
    try:
        filters = {"doc_type": args.type} if args.type else None
        recs = await store.all_records(filters)
    finally:
        await store.close()
    terms = args.query.lower().split()
    fields = ("title", "sponsor_name", "brand_name", "active_ingredient", "fda_org",
              "topic", "docket_number", "committee", "name", "review_type", "guidance_type",
              "role", "division", "office", "center", "program", "regulated_product")
    hits = [
        r for r in recs
        if all(any(t in str(r.get(f) or "").lower() for f in fields) for t in terms)
    ]
    hits.sort(key=lambda r: (r.get("doc_type") or "", r.get("citekey") or ""))
    if args.json:
        emit_json(hits)
    else:
        print_records(hits)
        print(f"\n{len(hits)} match(es)")


async def cmd_query(args: argparse.Namespace, home: Path) -> None:
    """Semantic / full-text search *inside* the documents (libkit)."""
    store = RegStore.open(home, read_only=True, want_semantic=not args.fts)
    try:
        fts_only = args.fts or not store.semantic_available
        if not args.fts and not store.semantic_available:
            warn(f"[FTS-only] no usable embedder ({store.embedder_reason}); running keyword search")
        hits = await store.query(args.text, limit=args.limit, fts_only=fts_only)
        out = []
        for h in hits:
            md = h.chunk.metadata or {}
            out.append({
                "citekey": md.get("citekey"),
                "doc_type": md.get("doc_type"),
                "title": h.chunk.title or md.get("title"),
                "score": round(h.score, 4),
                "snippet": " ".join((h.chunk.text or "").split())[:280],
                "source_url": md.get("source_url"),
            })
    finally:
        await store.close()
    if args.json:
        emit_json(out)
    else:
        if not out:
            print("(no matches)")
        for h in out:
            print(f"  [{h['citekey']}] ({h['doc_type']}) score={h['score']}  {h['title']}")
            if h["snippet"]:
                print(f"      … {h['snippet'].strip()[:200]} …")


async def cmd_text(args: argparse.Namespace, home: Path) -> None:
    store = RegStore.open(home, read_only=True)
    try:
        rec = await store.get_by_citekey(args.citekey)
        if rec is None:
            die(f"no document with citekey {args.citekey!r}")
        text = await store.document_text(rec["document_id"])
    finally:
        await store.close()
    print(text)


async def cmd_tag(args: argparse.Namespace, home: Path) -> None:
    store = RegStore.open(home)
    try:
        rec = await store.set_tags(args.citekey, add=args.add or [], remove=args.remove or [])
        _viewer.write(home, await store.all_records())
    except KeyError:
        die(f"no document with citekey {args.citekey!r}")
    finally:
        await store.close()
    print(f"[{rec['citekey']}] tags: {', '.join(rec.get('tags') or []) or '(none)'}")


async def cmd_rm(args: argparse.Namespace, home: Path) -> None:
    store = RegStore.open(home)
    try:
        rec = await store.remove(args.citekey, delete_file=args.delete_file)
        store.prune_empty_dirs()
        _viewer.write(home, await store.all_records())
    except KeyError:
        die(f"no document with citekey {args.citekey!r}")
    finally:
        await store.close()
    print(f"removed [{rec['citekey']}] {rec.get('title')}")


async def cmd_viewer(args: argparse.Namespace, home: Path) -> None:
    store = RegStore.open(home, read_only=True)
    try:
        recs = await store.all_records()
    finally:
        await store.close()
    out = _viewer.write(home, recs)
    print(f"wrote {out} ({len(recs)} documents)")


async def cmd_check(args: argparse.Namespace, home: Path) -> None:
    store = RegStore.open(home, read_only=True)
    try:
        recs = await store.all_records()
    finally:
        await store.close()
    problems = []
    seen_ck: dict[str, int] = {}
    for r in recs:
        ck = r.get("citekey")
        seen_ck[ck] = seen_ck.get(ck, 0) + 1
        if r.get("content_state") == "full" and r.get("file_path"):
            if not (home / r["file_path"]).exists():
                problems.append(f"missing file: [{ck}] {r['file_path']}")
        if not r.get("doc_type"):
            problems.append(f"no doc_type: [{ck}]")
    for ck, n in seen_ck.items():
        if n > 1:
            problems.append(f"duplicate citekey: {ck} ({n}×)")
    if args.json:
        emit_json({"documents": len(recs), "problems": problems})
    else:
        print(f"{len(recs)} documents; {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")


async def cmd_import(args: argparse.Namespace, home: Path) -> None:
    """Index an existing folder of regulatory documents *in place* (no move)."""
    root = Path(args.dir).expanduser().resolve() if args.dir else home
    if not root.is_dir():
        die(f"not a directory: {root}")
    skip = () if args.include_docs else (".download", ".stubs", "docs")
    files = _importer.walk(root, skip_dirs=skip)
    records = [(f, _importer.classify_path(f, home)) for f in files]
    if not records:
        die(f"no ingestible files under {root}")

    if args.dry_run:
        from collections import Counter
        counts = Counter(r["doc_type"] for _, r in records)
        for f, r in records:
            print(f"  {r['doc_type']:9} {r['file_path']}")
        print(f"\n{len(records)} file(s): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        print("(re-run without --dry-run to ingest + embed in place)")
        return

    store = RegStore.open(home)
    added = dup = failed = 0
    try:
        for f, rec in records:
            try:
                if await store.find_duplicate(rec) is not None:
                    dup += 1
                    continue
                res = await store.add(rec, file_path=f)  # ingest in place, no move
                if res["status"] == "added":
                    added += 1
                    print(f"  + [{res['record']['citekey']}] {rec['doc_type']}: {rec['file_path']}")
                else:
                    dup += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                warn(f"failed to ingest {rec['file_path']}: {e}")
        _viewer.write(home, await store.all_records())
    finally:
        await store.close()
    print(f"\nindexed {added}, already-had {dup}, failed {failed}")


async def cmd_add(args: argparse.Namespace, home: Path) -> None:
    """Ingest one document from a URL or local file as an arbitrary doc_type.

    The general-purpose escape hatch for documents the source ingesters don't
    cover — an FDA PFDD report, a patient-experience report, a hand-written
    landscape note. URLs download into the tree; local files are ingested in place.
    """
    src = args.source
    rec: dict[str, Any] = {"doc_type": args.type, "title": args.title}
    if args.program:
        rec["program"] = args.program
    if args.tag:
        rec["tags"] = [args.type, *args.tag]
    is_url = bool(re.match(r"^https?://", src))
    store = RegStore.open(home)
    try:
        if is_url:
            rec["source_url"] = src
            if not rec["title"]:
                rec["title"] = src.rstrip("/").rsplit("/", 1)[-1]
            rec.setdefault("citekey", await store.unique_citekey(_meta.make_citekey(rec)))
            dl = _download_dir(home)
            ext = ".pdf" if ".pdf" in src.lower() or "/download" in src.lower() else (Path(src).suffix or ".pdf")
            tmp = dl / (re.sub(r"[^A-Za-z0-9._-]", "_", rec["citekey"])[:80] + ext)
            async with httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
                timeout=httpx.Timeout(180.0), follow_redirects=True,
            ) as cl:
                async with cl.stream("GET", src) as resp:
                    resp.raise_for_status()
                    with tmp.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(1 << 16):
                            fh.write(chunk)
            res = await store.add_document(rec, src=tmp, move=True)
        else:
            p = Path(src).expanduser().resolve()
            if not p.is_file():
                die(f"no such file: {p}")
            if not rec["title"]:
                rec["title"] = p.stem
            res = await store.add(rec, file_path=p)  # in place
        _viewer.write(home, await store.all_records())
    finally:
        await store.close()
    if res["status"] == "duplicate":
        print(f"already have: [{res['record']['citekey']}] {res['record'].get('title')}")
    else:
        print(f"{res['status']}: [{res['record']['citekey']}] {res['record'].get('title')}")


# --------------------------------------------------------------------------- #
# drugsfda
# --------------------------------------------------------------------------- #
async def cmd_drugsfda_search(args: argparse.Namespace, home: Path) -> None:
    results = await drugsfda.search(
        args.query, field=args.field, limit=args.limit, mailto=_mailto()
    )
    if args.json:
        emit_json(results)
        return
    if not results:
        print("(no applications)")
        return
    for a in results:
        print(f"  {a['application_number']}  {a.get('sponsor_name','')}")
        bits = []
        if a.get("brand_names"):
            bits.append("/".join(a["brand_names"][:3]))
        if a.get("active_ingredients"):
            bits.append(", ".join(a["active_ingredients"][:3]))
        if a.get("latest_approval"):
            bits.append(f"approved {a['latest_approval']}")
        if bits:
            print(f"      {' · '.join(bits)}")
    print(f"\n{len(results)} application(s)")


async def cmd_drugsfda_add(args: argparse.Namespace, home: Path) -> None:
    summary, docs = await drugsfda.gather_docs(
        args.appno, pdf_only=True, mailto=_mailto()
    )
    if summary is None:
        die(f"no Drugs@FDA application {args.appno!r}")
    if args.submission:
        want = {s.lower() for s in args.submission}
        docs = [d for d in docs if d.get("submission", "").lower() in want]
    if args.type:
        want_t = {t.lower() for t in args.type}
        docs = [d for d in docs if d.get("review_type", "").lower() in want_t]
    if not docs:
        die("no documents matched (try without --submission/--type, or check `reg drugsfda search`)")
    print(f"{args.appno}: {summary.get('sponsor_name','')} — {len(docs)} document(s) to ingest")
    if args.dry_run:
        for d in docs:
            print(f"  {d['submission']:6} {d['review_type']:14} {d['doc_url']}")
        return

    store = RegStore.open(home)
    cl = drugsfda.client(mailto=_mailto())
    added = dup = failed = 0
    try:
        for d in docs:
            existing = await store.find_duplicate(d)
            if existing is not None:
                dup += 1
                print(f"  = [{existing['citekey']}] already have {d['review_type']}")
                continue
            try:
                path = await drugsfda.download(d["doc_url"], _download_dir(home), cl=cl)
            except Exception as e:  # noqa: BLE001
                failed += 1
                warn(f"download failed for {d['doc_url']}: {e}")
                continue
            res = await store.add_document(d, src=path, move=True)
            if res["status"] in ("added", "merged"):
                added += 1
                print(f"  + [{res['record']['citekey']}] {d['review_type']} ({d['submission']})")
            else:
                dup += 1
        _viewer.write(home, await store.all_records())
    finally:
        await cl.aclose()
        await store.close()
    print(f"\nadded {added}, already-had {dup}, failed {failed}")


# --------------------------------------------------------------------------- #
# guidance
# --------------------------------------------------------------------------- #
async def cmd_guidance_sync(args: argparse.Namespace, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    try:
        records = await guidance.fetch_corpus(
            from_file=Path(args.from_file) if args.from_file else None
        )
    except guidance.GatedError as e:
        die(str(e))
    path = guidance.save_corpus(home, records)
    print(f"cached {len(records)} guidance records → {path}")


async def cmd_guidance_search(args: argparse.Namespace, home: Path) -> None:
    records = guidance.load_corpus(home)
    if not records:
        die("no cached guidance corpus — run `reg guidance sync` first")
    hits = guidance.search_corpus(records, args.query, limit=args.limit)
    if args.json:
        emit_json(hits)
        return
    for i, r in enumerate(hits):
        print(f"  [{i}] {r.get('title')}")
        bits = [r.get("fda_org"), r.get("status"), r.get("issue_date"), r.get("docket_number")]
        print(f"      {' · '.join(b for b in bits if b)}")
    print(f"\n{len(hits)} match(es) — `reg guidance add <#>` to ingest by index")


async def _ingest_one_guidance(store: RegStore, cl: Any, home: Path, rec: dict[str, Any]) -> dict[str, Any]:
    """Download (or stub) + ingest one guidance record; return the store result."""
    rec = dict(rec)
    pdf = guidance.media_pdf_url(rec)
    if not pdf:
        return await store.add_document(rec, src=None)
    rec.setdefault("citekey", await store.unique_citekey(_meta.make_citekey(rec)))
    path = await guidance.download(pdf, _download_dir(home), cl=cl, citekey=rec.get("citekey"))
    return await store.add_document(rec, src=path, move=True)


async def cmd_guidance_add(args: argparse.Namespace, home: Path) -> None:
    # Resolve the candidate list: a direct URL, or corpus hit(s).
    candidates: list[dict[str, Any]] = []
    if args.target.startswith("http"):
        candidates = [{"doc_type": "guidance", "title": args.title or args.target,
                       "source_url": args.target, "pdf_url": args.target,
                       "guidance_id": args.target}]
    else:
        records = guidance.load_corpus(home)
        if not records:
            die("no cached guidance corpus — run `reg guidance sync` first")
        hits = guidance.search_corpus(records, args.target, limit=500 if args.all else 50)
        if args.all:
            candidates = hits
        elif args.index is not None:
            if args.index >= len(hits):
                die(f"index {args.index} out of range ({len(hits)} hits)")
            candidates = [hits[args.index]]
        elif len(hits) == 1:
            candidates = hits
        else:
            print(f"{len(hits)} matches — re-run with --index N (or --all to add them all):")
            for i, r in enumerate(hits[:25]):
                print(f"  [{i}] {r.get('title')} — {r.get('fda_org','')}")
            return
    if not candidates:
        die("no guidance documents matched")

    store = RegStore.open(home)
    cl = guidance.client()
    added = dup = failed = 0
    try:
        for rec in candidates:
            if await store.find_duplicate(rec) is not None:
                dup += 1
                continue
            try:
                res = await _ingest_one_guidance(store, cl, home, rec)
            except Exception as e:  # noqa: BLE001
                failed += 1
                warn(f"failed: {rec.get('title')}: {e}")
                continue
            if res["status"] in ("added", "merged"):
                added += 1
                print(f"  + [{res['record']['citekey']}] {res['record'].get('title')}")
            else:
                dup += 1
        _viewer.write(home, await store.all_records())
    finally:
        await cl.aclose()
        await store.close()
    print(f"\nadded {added}, already-had {dup}, failed {failed}")


# --------------------------------------------------------------------------- #
# adcomm
# --------------------------------------------------------------------------- #
async def cmd_adcomm_sync(args: argparse.Namespace, home: Path) -> None:
    materials = await adcomm.sync_meeting(
        args.url, committee=args.committee, committee_abbr=args.abbr,
        meeting_date=args.date,
    )
    if not args.add:
        if args.json:
            emit_json(materials)
        else:
            for m in materials:
                print(f"  {m['material_type']:12} {m['title']}")
                print(f"      {m['doc_url']}")
            print(f"\n{len(materials)} material(s) — re-run with --add to ingest")
        return
    store = RegStore.open(home)
    cl = adcomm.client()
    added = dup = failed = 0
    try:
        for m in materials:
            if await store.find_duplicate(m) is not None:
                dup += 1
                continue
            try:
                m.setdefault("citekey", await store.unique_citekey(_meta.make_citekey(m)))
                path = await adcomm.download(m["doc_url"], _download_dir(home), cl=cl, citekey=m["citekey"])
            except Exception as e:  # noqa: BLE001
                failed += 1
                warn(f"download failed for {m['doc_url']}: {e}")
                continue
            res = await store.add_document(m, src=path, move=True)
            if res["status"] in ("added", "merged"):
                added += 1
                print(f"  + [{res['record']['citekey']}] {m['material_type']}: {m['title']}")
            else:
                dup += 1
        _viewer.write(home, await store.all_records())
    finally:
        await cl.aclose()
        await store.close()
    print(f"\nadded {added}, already-had {dup}, failed {failed}")


# --------------------------------------------------------------------------- #
# personnel
# --------------------------------------------------------------------------- #
def _merge_person(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Overlay ``new``'s non-empty fields onto ``existing`` (each side is
    authoritative for what it carries): a signature harvest keeps a hand-authored
    bio, and an authored bio keeps the harvested signed-review list."""
    merged = dict(existing or {})
    for k, v in (new or {}).items():
        if v in (None, "", [], {}):
            continue
        merged[k] = v
    merged["doc_type"] = "personnel"
    if merged.get("signed_reviews"):
        merged["n_signed_reviews"] = len(merged["signed_reviews"])
    return merged


async def _upsert_person(store: RegStore, home: Path, person: dict[str, Any]) -> dict[str, Any]:
    """Create or update a personnel dossier, merging with any existing record."""
    ck = _meta.make_citekey(person)
    existing = await store.get_by_citekey(ck)
    merged = _merge_person(existing or {}, person)
    slug = merged.get("person_id") or personnel.person_slug(merged.get("name") or "unknown")
    md = personnel.dossier_markdown(merged)
    tmp = _download_dir(home) / f"{slug}.md"
    tmp.write_text(md, encoding="utf-8")
    if existing:
        await store.remove(existing["citekey"], delete_file=True)
    return await store.add_document(merged, src=tmp, move=True, force=True)


async def cmd_personnel_add(args: argparse.Namespace, home: Path) -> None:
    """Author or enrich a dossier for one person (e.g. a leader who signed nothing)."""
    person: dict[str, Any] = {
        "doc_type": "personnel", "name": args.name,
        "person_id": personnel.person_slug(args.name), "title": args.name,
    }
    for k in ("role", "division", "office", "center"):
        v = getattr(args, k, None)
        if v:
            person[k] = v
    if args.bio:
        person["bio"] = args.bio
    elif args.bio_file:
        person["bio"] = Path(args.bio_file).expanduser().read_text(encoding="utf-8")
    if args.source:
        person["sources"] = args.source
    person["tags"] = ["personnel", *(args.tag or [])]
    store = RegStore.open(home)
    try:
        res = await _upsert_person(store, home, person)
        _viewer.write(home, await store.all_records())
    finally:
        await store.close()
    print(f"{res['status']}: [{res['record']['citekey']}] {res['record'].get('name')}")


async def cmd_personnel_build(args: argparse.Namespace, home: Path) -> None:
    """Harvest reviewer signatures from the drugsfda docs already in the library."""
    store = RegStore.open(home, read_only=True)
    rows: list[dict[str, Any]] = []
    try:
        recs = await store.all_records({"doc_type": "drugsfda"})
        for r in recs:
            if r.get("review_type") in ("label", "toc"):
                continue
            text = await store.document_text(r["document_id"])
            for sig in personnel.extract_signatures(text):
                rows.append({
                    **sig,
                    "application_number": r.get("application_number"),
                    "review_type": r.get("review_type"),
                    "doc_subtype": r.get("doc_subtype"),
                    "sponsor_name": r.get("sponsor_name"),
                    "brand_name": r.get("brand_name"),
                })
    finally:
        await store.close()
    people = personnel.aggregate(rows)
    if not people:
        print("no signatures found (ingest some Drugs@FDA reviews first, e.g. `reg drugsfda add NDA…`)")
        return
    if args.dry_run:
        for p in sorted(people.values(), key=lambda x: x.get("person_id") or ""):
            print(f"  {p['name']:30} {p['n_signed_reviews']} review(s)  [{', '.join(p.get('review_disciplines') or [])}]")
        print(f"\n{len(people)} person(s) — re-run without --dry-run to write dossiers")
        return

    store = RegStore.open(home)
    written = 0
    try:
        for p in people.values():
            res = await _upsert_person(store, home, p)  # preserves any hand-authored bio/role
            written += 1
            print(f"  + [{res['record']['citekey']}] {p['name']} ({p['n_signed_reviews']} reviews)")
        _viewer.write(home, await store.all_records())
    finally:
        await store.close()
    print(f"\nwrote {written} dossier(s)")


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reg", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--home", type=Path, help="library directory (default ~/.regulator or $REGULATOR_HOME)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn)
        return sp

    add("init", cmd_init, "create the library + viewer")

    sp = add("list", cmd_list, "list documents")
    sp.add_argument("--type", choices=_meta.DOC_TYPES)
    sp.add_argument("--json", action="store_true")

    sp = add("show", cmd_show, "show one document")
    sp.add_argument("citekey")
    sp.add_argument("--json", action="store_true")

    sp = add("search", cmd_search, "substring metadata search over the catalog")
    sp.add_argument("query")
    sp.add_argument("--type", choices=_meta.DOC_TYPES)
    sp.add_argument("--json", action="store_true")

    sp = add("query", cmd_query, "semantic / full-text search inside the documents")
    sp.add_argument("text")
    sp.add_argument("--limit", type=int, default=8)
    sp.add_argument("--fts", action="store_true", help="force keyword (BM25) search")
    sp.add_argument("--json", action="store_true")

    sp = add("text", cmd_text, "print one document's stored text")
    sp.add_argument("citekey")

    sp = add("tag", cmd_tag, "add/remove tags")
    sp.add_argument("citekey")
    sp.add_argument("--add", nargs="*")
    sp.add_argument("--remove", nargs="*")

    sp = add("rm", cmd_rm, "remove a document")
    sp.add_argument("citekey")
    sp.add_argument("--delete-file", action="store_true")

    add("viewer", cmd_viewer, "(re)build the HTML viewer")

    sp = add("check", cmd_check, "integrity check")
    sp.add_argument("--json", action="store_true")

    sp = add("import", cmd_import, "index an existing folder of documents in place (no move)")
    sp.add_argument("dir", nargs="?", help="directory to import (default: the library home)")
    sp.add_argument("--dry-run", action="store_true", help="preview classification without ingesting")
    sp.add_argument("--include-docs", action="store_true", help="also re-walk the managed docs/ tree")

    sp = add("add", cmd_add, "ingest one document from a URL or local file (any doc_type)")
    sp.add_argument("source", help="an http(s) URL or a local file path")
    sp.add_argument("--type", default="other", choices=_meta.DOC_TYPES, help="doc_type (default: other)")
    sp.add_argument("--title", help="document title")
    sp.add_argument("--program", help="program/grouping label")
    sp.add_argument("--tag", nargs="*", help="extra tags")

    # ---- drugsfda group ----
    g = sub.add_parser("drugsfda", help="Drugs@FDA: openFDA metadata + accessdata PDFs")
    gs = g.add_subparsers(dest="sub", required=True)
    s = gs.add_parser("search", help="search applications")
    s.set_defaults(fn=cmd_drugsfda_search)
    s.add_argument("query")
    s.add_argument("--field", choices=["ingredient", "sponsor", "brand", "generic", "appno"])
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--json", action="store_true")
    s = gs.add_parser("add", help="ingest an application's approval-package PDFs")
    s.set_defaults(fn=cmd_drugsfda_add)
    s.add_argument("appno", help="application number, e.g. NDA205834 or BLA761234")
    s.add_argument("--submission", nargs="*", help="limit to submission tags, e.g. s000 s017")
    s.add_argument("--type", nargs="*", help="limit to review types, e.g. medical clinpharm summary letter")
    s.add_argument("--dry-run", action="store_true")

    # ---- guidance group ----
    g = sub.add_parser("guidance", help="FDA guidance documents")
    gs = g.add_subparsers(dest="sub", required=True)
    s = gs.add_parser("sync", help="fetch + cache the full guidance corpus")
    s.set_defaults(fn=cmd_guidance_sync)
    s.add_argument("--from-file", help="parse a locally-saved feed JSON (escape hatch for the bot wall)")
    s = gs.add_parser("search", help="search the cached corpus")
    s.set_defaults(fn=cmd_guidance_search)
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--json", action="store_true")
    s = gs.add_parser("add", help="ingest a guidance doc by corpus match or URL")
    s.set_defaults(fn=cmd_guidance_add)
    s.add_argument("target", help="a search string, or a direct /media/<id>/download URL")
    s.add_argument("--index", type=int, help="pick hit N when a search string is ambiguous")
    s.add_argument("--all", action="store_true", help="ingest ALL matches of the search string")
    s.add_argument("--title", help="title to use when adding by raw URL")

    # ---- adcomm group ----
    g = sub.add_parser("adcomm", help="advisory-committee materials")
    gs = g.add_subparsers(dest="sub", required=True)
    s = gs.add_parser("sync", help="extract (and optionally ingest) a meeting/hub page's materials")
    s.set_defaults(fn=cmd_adcomm_sync)
    s.add_argument("url", help="a meeting announcement or year-materials hub URL")
    s.add_argument("--committee")
    s.add_argument("--abbr", help="committee abbreviation, e.g. ODAC")
    s.add_argument("--date", help="meeting date YYYY-MM-DD (else inferred)")
    s.add_argument("--add", action="store_true", help="download + ingest the materials")
    s.add_argument("--json", action="store_true")

    # ---- personnel group ----
    g = sub.add_parser("personnel", help="biographical dossiers on FDA staff")
    gs = g.add_subparsers(dest="sub", required=True)
    s = gs.add_parser("build", help="harvest reviewer signatures from ingested Drugs@FDA reviews")
    s.set_defaults(fn=cmd_personnel_build)
    s.add_argument("--dry-run", action="store_true")
    s = gs.add_parser("add", help="author/enrich a dossier for one person (e.g. a non-signer leader)")
    s.set_defaults(fn=cmd_personnel_add)
    s.add_argument("name")
    s.add_argument("--role")
    s.add_argument("--division")
    s.add_argument("--office")
    s.add_argument("--center")
    s.add_argument("--bio", help="biography text")
    s.add_argument("--bio-file", help="read biography from a markdown/text file")
    s.add_argument("--source", nargs="*", help="source URLs/citations")
    s.add_argument("--tag", nargs="*", help="extra tags")

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    explicit_home = args.home or (Path(os.environ["REGULATOR_HOME"]).expanduser()
                                  if os.environ.get("REGULATOR_HOME") else None)
    _load_dotenv(explicit_home)
    home = (args.home or _default_home()).expanduser()
    try:
        asyncio.run(args.fn(args, home))
    except EmbedderConfigError as e:
        die(str(e))
    except FileNotFoundError as e:
        die(str(e))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
