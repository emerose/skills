"""Metadata resolvers for bibliographer.

Given an identifier (DOI, arXiv id, PMID, PMCID, Semantic Scholar id) or a local
PDF, produce a normalised *record* dict (the shape :mod:`_meta` expects). libkit
does not fetch metadata — this is bibliographer's job.

Sources, all keyless (an optional ``S2_API_KEY`` raises Semantic Scholar's rate
limit):

* Crossref      — DOIs (primary; rich bibliographic fields)
* arXiv         — arXiv ids
* NCBI E-utils  — PMID/PMCID, plus the ID converter (PMCID<->DOI<->PMID)
* Semantic Scholar — any id type; used to backfill abstracts
* Unpaywall     — DOI -> open-access PDF URL (feeds fetch-then-ingest)

``resolve()`` chains them: resolve by the identifier's native source, optionally
enrich a missing abstract from Semantic Scholar, and attach an OA PDF URL from
Unpaywall when one exists.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

import _meta

# Semantic Scholar's documented limit is 1 request/second cumulative across ALL
# endpoints, so a single process-wide gate (with margin) keeps both interactive
# and bulk-import S2 traffic under it. Other sources (Crossref polite pool, NCBI,
# Unpaywall) are far more generous and need no client-side throttle here.
_S2_MIN_INTERVAL = 1.1
_s2_gate = asyncio.Lock()
_s2_last = 0.0


async def _s2_throttle() -> None:
    global _s2_last
    async with _s2_gate:
        wait = _S2_MIN_INTERVAL - (time.monotonic() - _s2_last)
        if wait > 0:
            await asyncio.sleep(wait)
        _s2_last = time.monotonic()


# Persistent response cache (same diskcache pattern libkit uses). Resolver
# lookups are idempotent by identifier, so caching the raw 200 response means a
# re-run hits the network zero times — and never waits on the S2 throttle. We
# cache the *raw* body (not the normalized record) so improving a normalizer
# takes effect without a cache bump; bump the ``|vN`` suffix in a key only when
# the request itself changes. Failures (non-200) are never cached.
_CACHE_TTL = int(os.environ.get("BIBLIOGRAPHER_CACHE_TTL", str(30 * 24 * 3600)))  # 30 days
_UNSET = object()
_MISS = object()
_resolver_cache_obj: Any = _UNSET


def _resolver_cache() -> Any:
    """Lazily open the shared on-disk cache (``None`` if disabled/unavailable)."""
    global _resolver_cache_obj
    if _resolver_cache_obj is _UNSET:
        if os.environ.get("BIBLIOGRAPHER_NO_CACHE"):
            _resolver_cache_obj = None
        else:
            try:
                import diskcache
                import platformdirs

                root = os.environ.get("BIBLIOGRAPHER_CACHE_DIR") or str(
                    platformdirs.user_cache_path("bibliographer", appauthor=False)
                )
                _resolver_cache_obj = diskcache.Cache(
                    str(Path(root) / "resolvers"),
                    size_limit=256 << 20,
                    eviction_policy="least-recently-used",
                )
            except Exception:  # noqa: BLE001 — caching is best-effort
                _resolver_cache_obj = None
    return _resolver_cache_obj


async def _cached_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    key: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    throttle: Any = None,
) -> tuple[int, bytes]:
    """GET with a persistent cache. Returns ``(status_code, body)``.

    On a cache hit, no request is made and ``throttle`` is not invoked. Only a
    200 response is stored.
    """
    cache = _resolver_cache()
    if cache is not None:
        hit = cache.get(key, _MISS)
        if hit is not _MISS:
            return hit
    if throttle is not None:
        await throttle()
    r = await client.get(url, params=params, headers=headers)
    result = (r.status_code, r.content)
    if cache is not None and r.status_code == 200:
        cache.set(key, result, expire=_CACHE_TTL)
    return result

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
ARXIV_NEW_RE = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
ARXIV_OLD_RE = re.compile(r"\b([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")
PMCID_RE = re.compile(r"\bPMC(\d+)\b", re.I)
PMID_LABEL_RE = re.compile(r"\bPMID:?\s*(\d{4,9})\b", re.I)


class ResolveError(Exception):
    """Raised when an identifier cannot be resolved to metadata."""


def mailto() -> str:
    return os.environ.get("BIBLIOGRAPHER_MAILTO", "anonymous@example.com")


def _user_agent() -> str:
    return f"bibliographer/0.2 (mailto:{mailto()})"


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #
def classify_identifier(ident: str) -> tuple[str, str]:
    """Return ``(kind, value)`` with kind in doi|arxiv|pmid|pmcid|s2|unknown."""
    s = ident.strip()
    low = s.lower()
    if low.startswith("doi:"):
        return "doi", s[4:].strip()
    if "doi.org/" in low:
        return "doi", s.split("doi.org/", 1)[1].strip()
    if low.startswith("arxiv:"):
        return "arxiv", s.split(":", 1)[1].strip()
    if "arxiv.org/abs/" in low:
        return "arxiv", s.split("/abs/", 1)[1].strip()
    if "arxiv.org/pdf/" in low:
        return "arxiv", s.split("/pdf/", 1)[1].split(".pdf")[0].strip()
    if low.startswith("pmcid:") or PMCID_RE.fullmatch(s):
        return "pmcid", "PMC" + PMCID_RE.search(s).group(1)  # type: ignore[union-attr]
    if low.startswith("pmid:"):
        return "pmid", re.sub(r"\D", "", s)
    if low.startswith("s2:") or low.startswith("corpusid:"):
        return "s2", s.split(":", 1)[1].strip()
    if DOI_RE.fullmatch(s) or s.startswith("10."):
        return "doi", s
    if ARXIV_NEW_RE.fullmatch(s) or ARXIV_OLD_RE.fullmatch(s):
        return "arxiv", s
    if s.isdigit() and 4 <= len(s) <= 9:
        return "pmid", s
    return "unknown", s


# --------------------------------------------------------------------------- #
# normalisers
# --------------------------------------------------------------------------- #
def _strip_jats(text: str | None) -> str | None:
    if not text:
        return text
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def _crossref_type(t: str) -> str:
    return {
        "journal-article": "article", "proceedings-article": "inproceedings",
        "book-chapter": "incollection", "book": "book",
        "posted-content": "misc", "report": "techreport",
    }.get(t, "article")


def _from_crossref(m: dict[str, Any]) -> dict[str, Any]:
    authors = [
        {"family": a.get("family", ""), "given": a.get("given", "")}
        for a in m.get("author", [])
    ]
    issued = (m.get("issued", {}).get("date-parts") or [[None]])[0]
    year = issued[0] if issued and issued[0] else None
    doi = m.get("DOI")
    return _drop_empty({
        # Crossref titles can contain JATS/HTML markup (e.g. <i>…</i>); strip it
        # so it doesn't leak into citekeys, filenames, or BibTeX.
        "title": _strip_jats((m.get("title") or [None])[0]),
        "authors": authors,
        "year": year,
        "venue": (m.get("container-title") or [None])[0],
        "doi": doi,
        "source_url": m.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        "bibtex_type": _crossref_type(m.get("type", "")),
        "abstract": _strip_jats(m.get("abstract")),
        "volume": m.get("volume"),
        "issue": m.get("issue"),
        "pages": m.get("page"),
        "publisher": m.get("publisher"),
        "source": "crossref",
    })


def _from_arxiv(raw: bytes, arxiv_id: str) -> dict[str, Any]:
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    entry = ET.fromstring(raw).find("a:entry", ns)
    if entry is None or entry.find("a:title", ns) is None:
        raise ResolveError(f"arXiv returned no entry for {arxiv_id}")
    authors = []
    for a in entry.findall("a:author", ns):
        name = (a.findtext("a:name", "", ns) or "").strip()
        parts = name.rsplit(" ", 1)
        authors.append(
            {"family": parts[1], "given": parts[0]} if len(parts) == 2
            else {"family": name, "given": ""}
        )
    published = entry.findtext("a:published", "", ns)
    return _drop_empty({
        "title": " ".join((entry.findtext("a:title", "", ns)).split()),
        "authors": authors,
        "year": int(published[:4]) if published[:4].isdigit() else None,
        "venue": "arXiv preprint",
        "doi": entry.findtext("arxiv:doi", None, ns),
        "arxiv_id": arxiv_id,
        "source_url": f"https://arxiv.org/abs/{arxiv_id}",
        "bibtex_type": "misc",
        "abstract": " ".join((entry.findtext("a:summary", "", ns)).split()),
        "source": "arxiv",
    })


def _from_semantic_scholar(d: dict[str, Any]) -> dict[str, Any]:
    ext = d.get("externalIds") or {}
    authors = []
    for a in d.get("authors", []):
        name = (a.get("name") or "").strip()
        parts = name.rsplit(" ", 1)
        authors.append(
            {"family": parts[1], "given": parts[0]} if len(parts) == 2
            else {"family": name, "given": ""}
        )
    return _drop_empty({
        "title": d.get("title"),
        "authors": authors,
        "year": d.get("year"),
        "venue": d.get("venue"),
        "doi": (ext.get("DOI") or "").lower() or None,
        "arxiv_id": ext.get("ArXiv"),
        "pmid": ext.get("PubMed"),
        "pmcid": ("PMC" + str(ext["PubMedCentral"])) if ext.get("PubMedCentral") else None,
        "s2_id": str(d["paperId"]) if d.get("paperId") else None,
        "source_url": d.get("url"),
        "abstract": d.get("abstract"),
        "oa_pdf_url": (d.get("openAccessPdf") or {}).get("url"),
        "bibtex_type": "article",
        "source": "semantic_scholar",
    })


def _drop_empty(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# --------------------------------------------------------------------------- #
# fetchers
# --------------------------------------------------------------------------- #
async def fetch_crossref(doi: str, client: httpx.AsyncClient) -> dict[str, Any]:
    # Crossref expects the DOI literally in the path (its slash is a separator
    # Crossref keeps), so build the URL as a string rather than letting httpx
    # percent-encode it.
    status, body = await _cached_get(
        client, f"https://api.crossref.org/works/{doi}",
        key=f"crossref|{doi.lower()}|v1", params={"mailto": mailto()},
    )
    if status != 200:
        raise ResolveError(f"Crossref {status} for {doi}")
    return _from_crossref(json.loads(body)["message"])


async def crossref_search(query: str, client: httpx.AsyncClient, *, rows: int = 3) -> list[dict[str, Any]]:
    """Bibliographic free-text search → ranked candidate records (cached).

    Used to recover metadata for files with no extractable identifier, by
    querying with the author/year/title parsed from the original filename.
    """
    status, body = await _cached_get(
        client, "https://api.crossref.org/works",
        key=f"crossref-search|{query.lower()}|r{rows}|v1",
        params={
            "query.bibliographic": query, "rows": rows,
            "select": "DOI,title,author,issued,container-title,type", "mailto": mailto(),
        },
    )
    if status != 200:
        return []
    items = json.loads(body).get("message", {}).get("items", []) or []
    return [_from_crossref(m) for m in items]


async def fetch_arxiv(arxiv_id: str, client: httpx.AsyncClient) -> dict[str, Any]:
    aid = re.sub(r"v\d+$", "", arxiv_id)
    status, body = await _cached_get(
        client, "https://export.arxiv.org/api/query",
        key=f"arxiv|{aid}|v1", params={"id_list": aid},
    )
    if status != 200:
        raise ResolveError(f"arXiv {status} for {aid}")
    return _from_arxiv(body, aid)


async def fetch_semantic_scholar(id_kind: str, value: str, client: httpx.AsyncClient) -> dict[str, Any]:
    prefix = {"doi": "DOI:", "arxiv": "arXiv:", "pmid": "PMID:", "pmcid": "PMCID:", "s2": ""}.get(id_kind, "")
    pid = f"{prefix}{value}"
    headers = {}
    if os.environ.get("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]
    fields = "title,abstract,year,venue,authors,externalIds,url,paperId,openAccessPdf"
    # Throttle only fires on a cache miss (a real request).
    status, body = await _cached_get(
        client, f"https://api.semanticscholar.org/graph/v1/paper/{pid}",
        key=f"s2|{pid}|{fields}|v1", params={"fields": fields}, headers=headers,
        throttle=_s2_throttle,
    )
    if status != 200:
        raise ResolveError(f"Semantic Scholar {status} for {pid}")
    return _from_semantic_scholar(json.loads(body))


async def ncbi_idconv(ids: str, client: httpx.AsyncClient) -> dict[str, str]:
    """Map among PMID / PMCID / DOI via the NCBI ID converter."""
    status, body = await _cached_get(
        client, "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
        key=f"idconv|{ids}|v1",
        params={"ids": ids, "format": "json", "tool": "bibliographer", "email": mailto()},
    )
    if status != 200:
        return {}
    rec = (json.loads(body).get("records") or [{}])[0]
    return _drop_empty({"pmid": rec.get("pmid"), "pmcid": rec.get("pmcid"), "doi": rec.get("doi")})


async def download_pdf(url: str, dest: Path, client: httpx.AsyncClient) -> bool:
    """Download a PDF to ``dest``. Returns True only if it really looks like a PDF.

    Many "OA" URLs are HTML landing pages; we verify the magic bytes so we never
    ingest a web page as if it were the paper.
    """
    try:
        r = await client.get(url)
    except httpx.HTTPError:
        return False
    if r.status_code != 200:
        return False
    data = r.content
    ctype = r.headers.get("content-type", "")
    if not (data[:5] == b"%PDF-" or "application/pdf" in ctype):
        return False
    dest.write_bytes(data)
    return True


async def fetch_unpaywall(doi: str, client: httpx.AsyncClient) -> str | None:
    """Return the best open-access PDF URL for a DOI, or None."""
    status, body = await _cached_get(
        client, f"https://api.unpaywall.org/v2/{doi}",
        key=f"unpaywall|{doi.lower()}|v1", params={"email": mailto()},
    )
    if status != 200:
        return None
    loc = json.loads(body).get("best_oa_location") or {}
    return loc.get("url_for_pdf") or None


async def acquire_oa_pdf(
    rec: dict[str, Any], dest: Path, client: httpx.AsyncClient
) -> str | None:
    """Try to download a legitimately-open PDF for ``rec`` to ``dest``.

    Returns the source name on success, else None. Covers the keyless OA tiers
    (preprint servers + open repositories); the browser/institutional and
    last-resort routes are manual — see references/getting-pdfs.md. Order favors
    the most reliable, freest sources first; every candidate is byte-verified as
    a real PDF by :func:`download_pdf`.
    """
    doi = (rec.get("doi") or "").strip()
    arxiv = rec.get("arxiv_id")
    pmcid = rec.get("pmcid")

    async def _try(source: str, url: str | None) -> str | None:
        if url and await download_pdf(url, dest, client):
            return source
        return None

    # 1. arXiv — direct, always-open
    if arxiv:
        aid = re.sub(r"v\d+$", "", str(arxiv))
        if await _try("arxiv", f"https://arxiv.org/pdf/{aid}.pdf"):
            return "arxiv"
    # 2. Europe PMC OA render (the PMC open-access subset)
    if pmcid:
        if await _try(
            "europepmc",
            f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf",
        ):
            return "europepmc"
    # 3. bioRxiv / medRxiv (DOI prefix 10.1101)
    if doi.startswith("10.1101/"):
        for server in ("biorxiv", "medrxiv"):
            for ver in ("v1", ""):
                if await _try(server, f"https://www.{server}.org/content/{doi}{ver}.full.pdf"):
                    return server
    # 4. an OA URL already on the record (from a prior Unpaywall/S2 resolve)
    if await _try("oa_url", rec.get("oa_pdf_url")):
        return "oa_url"
    # 5. Unpaywall (fresh)
    if doi:
        try:
            if await _try("unpaywall", await fetch_unpaywall(doi, client)):
                return "unpaywall"
        except (httpx.HTTPError, ResolveError):
            pass
    # 6. Semantic Scholar openAccessPdf (fresh)
    for kind, val in (("doi", doi), ("arxiv", arxiv), ("pmcid", pmcid)):
        if not val:
            continue
        try:
            s2 = await fetch_semantic_scholar(kind, str(val), client)
            if await _try("semantic_scholar", s2.get("oa_pdf_url")):
                return "semantic_scholar"
        except (httpx.HTTPError, ResolveError):
            pass
        break
    return None


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
async def resolve(
    identifier: str,
    *,
    client: httpx.AsyncClient | None = None,
    enrich: bool = True,
    unpaywall: bool = True,
) -> dict[str, Any]:
    """Resolve an identifier string to a normalised record (raises ResolveError)."""
    own = client is None
    client = client or httpx.AsyncClient(
        timeout=30, headers={"User-Agent": _user_agent()}, follow_redirects=True
    )
    try:
        kind, value = classify_identifier(identifier)
        if kind == "unknown":
            raise ResolveError(
                f"could not classify {identifier!r}; give a DOI, arXiv id, PMID, "
                "PMCID, Semantic Scholar id, or a local .pdf path"
            )
        rec = await _resolve_kind(kind, value, client)

        # Enrich a missing abstract from Semantic Scholar (cheap, high value).
        if enrich and not rec.get("abstract"):
            for k, v in (("doi", rec.get("doi")), ("arxiv", rec.get("arxiv_id"))):
                if v:
                    try:
                        s2 = await fetch_semantic_scholar(k, v, client)
                        for key in ("abstract", "s2_id", "pmid", "pmcid", "venue"):
                            if s2.get(key) and not rec.get(key):
                                rec[key] = s2[key]
                        break
                    except ResolveError:
                        pass

        if unpaywall and rec.get("doi") and not rec.get("oa_pdf_url"):
            try:
                url = await fetch_unpaywall(rec["doi"], client)
                if url:
                    rec["oa_pdf_url"] = url
            except (httpx.HTTPError, ResolveError):
                pass
        return rec
    finally:
        if own:
            await client.aclose()


async def _resolve_kind(kind: str, value: str, client: httpx.AsyncClient) -> dict[str, Any]:
    if kind == "doi":
        return await fetch_crossref(value, client)
    if kind == "arxiv":
        return await fetch_arxiv(value, client)
    if kind == "s2":
        return await fetch_semantic_scholar("s2", value, client)
    if kind in ("pmid", "pmcid"):
        # Prefer DOI -> Crossref (richest); fall back to Semantic Scholar.
        conv = await ncbi_idconv(value, client)
        rec: dict[str, Any] = {}
        if conv.get("doi"):
            try:
                rec = await fetch_crossref(conv["doi"], client)
            except ResolveError:
                rec = {}
        if not rec:
            rec = await fetch_semantic_scholar(kind, value, client)
        rec.setdefault("pmid", conv.get("pmid"))
        rec.setdefault("pmcid", conv.get("pmcid"))
        return _drop_empty(rec)
    raise ResolveError(f"no resolver for kind {kind!r}")


# --------------------------------------------------------------------------- #
# PDF / filename sniffing
# --------------------------------------------------------------------------- #
def pdf_text(path: Path, max_chars: int = 8000) -> str:
    """First-pages text via pdftotext, falling back to pypdf."""
    if shutil.which("pdftotext"):
        try:
            out = subprocess.run(
                ["pdftotext", "-f", "1", "-l", "2", str(path), "-"],
                capture_output=True, timeout=30, check=False,
            )
            if out.stdout:
                return out.stdout.decode("utf-8", "ignore")[:max_chars]
        except Exception:  # noqa: BLE001
            pass
    try:
        from pypdf import PdfReader

        text = ""
        for page in PdfReader(str(path)).pages[:2]:
            text += page.extract_text() or ""
        return text[:max_chars]
    except Exception:  # noqa: BLE001
        return ""


def sniff_filename(name: str) -> list[tuple[str, str]]:
    """Candidate identifiers reconstructed from a filename.

    Reconstructions are *guesses* validated downstream by actually fetching
    them (a bad guess simply fails to resolve). Handles the publisher-junk
    names common in real PDF piles.
    """
    stem = Path(name).stem
    out: list[tuple[str, str]] = []
    m = DOI_RE.search(stem.replace("_", "/", 1)) or DOI_RE.search(stem)
    if m:
        out.append(("doi", m.group(0).rstrip(".,;)")))
    # Not PMCID_RE: its \b word-boundary fails next to filename underscores
    # (e.g. "317_pmc9283931"), since "_" is itself a word character.
    m = re.search(r"(?i)pmc(\d+)", stem)
    if m:
        out.append(("pmcid", "PMC" + m.group(1)))
    # Nature-family: s41598_024_55666_6 -> 10.1038/s41598-024-55666-6
    m = re.fullmatch(r"(s\d{4,6})_(\d{2,4})_(\d{3,6})_(\d{1,2})", stem, re.I)
    if m:
        out.append(("doi", f"10.1038/{m.group(1)}-{m.group(2)}-{m.group(3)}-{m.group(4)}"))
    # Legacy Nature: nbt_3779 / nmeth_1923 -> 10.1038/nbt.3779
    m = re.fullmatch(r"([a-z]{2,6})_(\d{3,5})", stem, re.I)
    if m and m.group(1).lower() in {"nbt", "nmeth", "ng", "nm", "ni", "nn", "nchem", "nphys"}:
        out.append(("doi", f"10.1038/{m.group(1).lower()}.{m.group(2)}"))
    return out


def _embedded_meta_text(path: Path) -> str:
    """Concatenated embedded-metadata fields (often hold the DOI verbatim)."""
    try:
        from pypdf import PdfReader

        info = PdfReader(str(path)).metadata or {}
        return " ".join(
            str(info.get(k, "")) for k in ("/Title", "/Subject", "/Keywords", "/doi", "/WPS-ARTICLEDOI")
        )
    except Exception:  # noqa: BLE001
        return ""


_REFS_RE = re.compile(r"\b(references|bibliography|works cited|literature cited)\b", re.I)


def _extract_ids(text: str) -> list[tuple[str, str]]:
    """DOI / arXiv / PMC ids found in a blob of text."""
    out: list[tuple[str, str]] = []
    if not text:
        return out
    m = ARXIV_NEW_RE.search(text)
    if m and "arxiv" in text.lower():
        out.append(("arxiv", m.group(1)))
    m = DOI_RE.search(text)
    if m:
        out.append(("doi", m.group(0).rstrip(".,;)")))
    m = re.search(r"(?i)pmc(\d+)", text)
    if m:
        out.append(("pmcid", "PMC" + m.group(1)))
    return out


def _split_at_references(text: str) -> tuple[str, str]:
    """Split text at the first References/Bibliography heading (before, after)."""
    m = _REFS_RE.search(text)
    return (text, "") if not m else (text[: m.start()], text[m.start():])


def _title_overlap(title: str | None, text: str) -> float:
    """Fraction of a title's significant tokens present in text (0..1)."""
    toks = {t for t in _meta.norm_title(title).split() if len(t) > 3 and t not in _meta.STOPWORDS}
    if not toks:
        return 0.0
    low = text.lower()
    return sum(1 for t in toks if t in low) / len(toks)


def sniff_pdf(path: Path) -> list[tuple[str, str]]:
    """All candidate identifiers from filename, embedded metadata, and page text
    (de-duped). `resolve_pdf` applies trust tiers; this is the flat union."""
    out = sniff_filename(path.name) + _extract_ids(_embedded_meta_text(path)) + _extract_ids(pdf_text(path))
    seen: set[tuple[str, str]] = set()
    uniq: list[tuple[str, str]] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


async def resolve_pdf(path: Path, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Resolve a local PDF to metadata via sniffed identifiers, with trust tiers.

    A DOI found in a PDF's **reference list** is usually a *cited* work, not the
    paper itself — a real failure mode (a file resolved to a paper it merely
    cited). So:

    1. **Trusted** ids — from the filename, embedded PDF metadata, and the body
       text *before* any "References" heading — are accepted on resolve.
    2. **Suspect** ids — appearing *only* after the references heading — are
       accepted only if the resolved title's words actually appear in the
       document's front matter (otherwise it's a citation, not this paper).

    Returns a record, or ``None`` if nothing resolves/verifies (caller then falls
    back to the PDF's embedded metadata as an unverified record).
    """
    front = pdf_text(path)
    before_refs, after_refs = _split_at_references(front)
    trusted = sniff_filename(path.name) + _extract_ids(_embedded_meta_text(path)) + _extract_ids(before_refs)
    suspect = _extract_ids(after_refs)

    own = client is None
    client = client or httpx.AsyncClient(
        timeout=30, headers={"User-Agent": _user_agent()}, follow_redirects=True
    )
    try:
        seen: set[tuple[str, str]] = set()

        async def _try(kind: str, value: str) -> dict[str, Any] | None:
            try:
                return await resolve(f"{kind}:{value}" if kind != "doi" else value, client=client)
            except (ResolveError, httpx.HTTPError):
                return None

        for kind, value in trusted:
            if (kind, value) in seen:
                continue
            seen.add((kind, value))
            rec = await _try(kind, value)
            if rec is not None:
                rec["sniffed_from"] = f"{kind}:{value}"
                return rec
        for kind, value in suspect:
            if (kind, value) in seen:
                continue
            seen.add((kind, value))
            rec = await _try(kind, value)
            if rec is not None and _title_overlap(rec.get("title"), before_refs or front) >= 0.34:
                rec["sniffed_from"] = f"{kind}:{value}"
                return rec
        return None
    finally:
        if own:
            await client.aclose()
