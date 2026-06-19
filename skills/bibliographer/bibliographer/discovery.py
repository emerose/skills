"""Literature *discovery* for bibliographer.

The resolvers in :mod:`_resolvers` turn an identifier you *already have* into a
record. Discovery is the other direction: take a free-text research question and
**find candidate papers you don't yet know about**, across many scholarly search
APIs, then merge them into one ranked, de-duplicated candidate list in
bibliographer's normal record shape — ready to print, emit as JSON, or funnel
straight into the library via the usual ``ingest_record`` path.

## Why this lives here, not in an MCP

Discovery is inherently non-reproducible (ranked-relevance engines drift, new
papers appear), but it should be *uniform, source-broad, and environment-
independent*: it must not depend on which MCP servers happen to be installed in a
given session. So each source is a small async **provider** hitting a public API
directly over httpx, reusing the resolver layer's polite-pool cache
(:func:`_resolvers._cached_get`) and normalizers. Adding a source is adding one
function to ``PROVIDERS``.

## The provider contract

A provider is ``async def search(query, client, *, limit, filters) -> list[dict]``:

* ``query``   — the free-text question (provider translates to its own syntax).
* ``client``  — a shared ``httpx.AsyncClient`` (polite-pool UA, redirects on).
* ``limit``   — max records to return (the provider's page size).
* ``filters`` — a :class:`Filters` (year range, open-access-only); a provider
  applies what it can natively and ignores the rest.

It returns records in the **same normalized shape resolvers produce** (the keys
``_meta`` understands: ``title``/``authors``/``year``/``venue``/``doi``/
``abstract``/identifiers/…), plus two discovery fields this module stamps:
``discovery_source`` (the provider name) and ``relevance_rank`` (0-based, the
provider's own ordering). A provider should **never raise** for an ordinary API
hiccup — return ``[]`` and let the sweep continue; :func:`discover` records the
error against that source.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from . import meta as _meta
from . import resolvers as _resolvers
from .resolvers import _cached_get, _drop_empty, _strip_jats, mailto


# --------------------------------------------------------------------------- #
# filters
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Filters:
    """Cross-provider search filters. A provider applies what it can natively."""

    year_min: int | None = None
    year_max: int | None = None
    open_access: bool = False


def _author_from_display_name(name: str) -> dict[str, str]:
    """Split a flat ``"Given Family"`` display name into ``{family, given}``.

    The same heuristic Semantic Scholar/arXiv normalizers use: everything before
    the last space is the given name(s). Imperfect for multi-word surnames, but
    consistent with how the rest of bibliographer renders flat author strings.
    """
    name = (name or "").strip()
    parts = name.rsplit(" ", 1)
    if len(parts) == 2 and parts[0]:
        return {"family": parts[1], "given": parts[0]}
    return {"family": name, "given": ""}


# --------------------------------------------------------------------------- #
# providers
# --------------------------------------------------------------------------- #
# Each provider: async (query, client, *, limit, filters) -> list[record].
Provider = Callable[..., Awaitable[list[dict[str, Any]]]]


def _stamp(records: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Tag a provider's records with their source + 0-based relevance rank."""
    out: list[dict[str, Any]] = []
    for i, rec in enumerate(records):
        rec = _drop_empty(rec)
        rec["discovery_source"] = source
        rec["relevance_rank"] = i
        out.append(rec)
    return out


# --- OpenAlex --------------------------------------------------------------- #
def _openalex_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """Reconstruct plain text from OpenAlex's ``abstract_inverted_index``.

    OpenAlex ships abstracts as ``{word: [positions]}`` (a licensing artifact);
    rebuild the linear text by placing each word at each of its positions.
    """
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(w for _, w in positions)


def _from_openalex(w: dict[str, Any]) -> dict[str, Any]:
    ids = w.get("ids") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "").lower() or None
    pmid = None
    if ids.get("pmid"):
        m = re.search(r"(\d+)$", str(ids["pmid"]))
        pmid = m.group(1) if m else None
    authors = [
        _author_from_display_name((a.get("author") or {}).get("display_name", ""))
        for a in (w.get("authorships") or [])
    ]
    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
    oa_url = (w.get("best_oa_location") or {}).get("pdf_url") or (
        (w.get("primary_location") or {}).get("pdf_url")
    )
    cnp = (w.get("citation_normalized_percentile") or {}).get("value")
    return _drop_empty({
        "title": _strip_jats(w.get("title")),
        "authors": authors,
        "year": w.get("publication_year"),
        "venue": venue,
        "doi": doi,
        "pmid": pmid,
        "abstract": _openalex_abstract(w.get("abstract_inverted_index")),
        "source_url": w.get("doi") or ids.get("openalex") or w.get("id"),
        "openalex_id": (w.get("id") or "").replace("https://openalex.org/", "") or None,
        "cited_by_count": w.get("cited_by_count"),
        # Field-normalized rank signals for the "highly ranked" banking bar:
        # FWCI (1.0 = field-average citations) and citation percentile (0–1).
        "fwci": round(w["fwci"], 3) if w.get("fwci") is not None else None,
        "citation_percentile": round(cnp, 4) if cnp is not None else None,
        "oa_pdf_url": oa_url,
        "bibtex_type": "article",
        "source": "openalex",
    })


async def search_openalex(
    query: str, client: httpx.AsyncClient, *, limit: int, filters: Filters
) -> list[dict[str, Any]]:
    """OpenAlex ``/works`` relevance search. Keyless; rich filters; cited-by.

    The single best free backbone: ~250M works, no key, native year/OA filters,
    and a corroborating citation count. Abstracts come back inverted and are
    rebuilt by :func:`_openalex_abstract`.
    """
    parts: list[str] = []
    if filters.year_min:
        parts.append(f"from_publication_date:{filters.year_min}-01-01")
    if filters.year_max:
        parts.append(f"to_publication_date:{filters.year_max}-12-31")
    if filters.open_access:
        parts.append("is_oa:true")
    params: dict[str, Any] = {
        "search": query,
        "per_page": min(max(limit, 1), 200),
        "mailto": mailto(),
        "select": (
            "id,ids,doi,title,publication_year,authorships,primary_location,"
            "best_oa_location,abstract_inverted_index,cited_by_count,"
            "fwci,citation_normalized_percentile,type"
        ),
    }
    if parts:
        params["filter"] = ",".join(parts)
    status, body = await _cached_get(
        client, "https://api.openalex.org/works",
        key=f"openalex-search|{query.lower()}|{','.join(parts)}|n{limit}|v1",
        params=params,
    )
    if status != 200:
        raise _resolvers.ResolveError(f"OpenAlex {status}")
    works = (json.loads(body).get("results") or [])[:limit]
    return _stamp([_from_openalex(w) for w in works], "openalex")


# --- Crossref --------------------------------------------------------------- #
async def search_crossref(
    query: str, client: httpx.AsyncClient, *, limit: int, filters: Filters
) -> list[dict[str, Any]]:
    """Crossref free-text relevance search over the DOI corpus (keyless).

    Reuses the resolver's ``_from_crossref`` normalizer. Year filtering is
    applied via Crossref's ``filter`` param; OA is not a Crossref concept, so an
    ``open_access`` filter is ignored here (other providers cover it).
    """
    fil: list[str] = []
    if filters.year_min:
        fil.append(f"from-pub-date:{filters.year_min}-01-01")
    if filters.year_max:
        fil.append(f"until-pub-date:{filters.year_max}-12-31")
    params: dict[str, Any] = {
        "query.bibliographic": query,
        "rows": min(max(limit, 1), 100),
        "select": "DOI,title,author,issued,container-title,type,abstract,volume,issue,page,publisher",
        "mailto": mailto(),
    }
    if fil:
        params["filter"] = ",".join(fil)
    status, body = await _cached_get(
        client, "https://api.crossref.org/works",
        key=f"crossref-discover|{query.lower()}|{','.join(fil)}|r{limit}|v1",
        params=params,
    )
    if status != 200:
        raise _resolvers.ResolveError(f"Crossref {status}")
    items = (json.loads(body).get("message", {}).get("items", []) or [])[:limit]
    return _stamp([_resolvers._from_crossref(m) for m in items], "crossref")


# --- Semantic Scholar ------------------------------------------------------ #
async def search_semantic_scholar(
    query: str, client: httpx.AsyncClient, *, limit: int, filters: Filters
) -> list[dict[str, Any]]:
    """Semantic Scholar ``/paper/search`` relevance search (keyless or keyed).

    Covers ~220M papers; results ranked by S2's own relevance model. Reuses
    :func:`_resolvers._from_semantic_scholar` for normalisation, then adds
    ``cited_by_count`` from the raw item (the shared per-paper normalizer omits
    it because the resolver endpoint doesn't request it).

    ``throttle=_resolvers._s2_throttle`` is forwarded so S2's ≤1 req/s limit is
    respected on a cache miss; a cache hit never sleeps. An ``S2_API_KEY`` in the
    environment raises the rate limit and is sent as the ``x-api-key`` header.
    """
    fields = (
        "title,abstract,year,venue,authors,externalIds,url,paperId,"
        "openAccessPdf,citationCount"
    )
    params: dict[str, Any] = {
        "query": query,
        "limit": min(max(limit, 1), 100),   # S2 hard cap is 100
        "fields": fields,
    }
    # Year filter — S2 accepts "<min>-<max>", "<min>-", or "-<max>".
    if filters.year_min or filters.year_max:
        params["year"] = f"{filters.year_min or ''}-{filters.year_max or ''}"
    # OA filter — presence-only flag; any value triggers the restriction.
    if filters.open_access:
        params["openAccessPdf"] = ""

    headers: dict[str, str] = {}
    if os.environ.get("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]

    year_tag = params.get("year", "")
    oa_tag = "oa" if filters.open_access else ""
    status, body = await _cached_get(
        client, "https://api.semanticscholar.org/graph/v1/paper/search",
        key=f"s2-search|{query.lower()}|{year_tag}|{oa_tag}|n{limit}|v1",
        params=params, headers=headers or None,
        throttle=_resolvers._s2_throttle,
    )
    if status != 200:
        raise _resolvers.ResolveError(f"Semantic Scholar {status}")
    data = json.loads(body).get("data") or []
    records: list[dict[str, Any]] = []
    for d in data:
        rec = _resolvers._from_semantic_scholar(d)
        rec["cited_by_count"] = d.get("citationCount")
        records.append(rec)
    return _stamp(records[:limit], "semantic_scholar")


# --- PubMed (NCBI E-utilities) --------------------------------------------- #
def _author_from_pubmed(name: str) -> dict[str, str]:
    """Split PubMed's ``"Surname Initials"`` form into ``{family, given}``.

    PubMed esummary ``authors[*].name`` is surname-*first* (``"Zhang Y"``,
    ``"van der Berg JA"``) — the reverse of the ``"Given Family"`` convention
    OpenAlex/S2/arXiv use, so :func:`_author_from_display_name` can't be reused:
    here ``family`` is everything before the last space, ``given`` the trailing
    initials.
    """
    name = (name or "").strip()
    parts = name.rsplit(" ", 1)
    if len(parts) == 2 and parts[0]:
        return {"family": parts[0], "given": parts[1]}
    return {"family": name, "given": ""}


def _from_pubmed(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise one PubMed esummary result dict to a record."""
    authors = [
        _author_from_pubmed(a.get("name", ""))
        for a in (item.get("authors") or [])
        if a.get("authtype") == "Author"   # skip CollectiveName rows
    ]
    # pubdate: "2023 Nov 22" | "2024 Mar" | "2024" — first 4-digit run is the year.
    pubdate = item.get("pubdate") or item.get("epubdate") or ""
    m = re.search(r"\d{4}", pubdate)
    year = int(m.group(0)) if m else None
    # articleids: prefer idtype "pmc" (clean "PMC123") over "pmcid" (verbose blob).
    doi: str | None = None
    pmcid: str | None = None
    for aid in item.get("articleids") or []:
        idtype, value = aid.get("idtype", ""), (aid.get("value") or "").strip()
        if idtype == "doi" and value:
            doi = value.lower()
        elif idtype == "pmc" and value.startswith("PMC"):
            pmcid = value
    pmid = str(item.get("uid") or "").strip() or None
    return _drop_empty({
        "title": _strip_jats(item.get("title")),
        "authors": authors,
        "year": year,
        "venue": item.get("fulljournalname") or item.get("source") or None,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        # esummary carries no abstract; left absent (enrichment can backfill).
        "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
        "bibtex_type": "article",
        "source": "pubmed",
    })


async def search_pubmed(
    query: str, client: httpx.AsyncClient, *, limit: int, filters: Filters
) -> list[dict[str, Any]]:
    """PubMed relevance search via NCBI E-utilities (esearch → esummary).

    esearch returns a relevance-ranked PMID list (with native year/OA filters);
    one batched esummary call then fetches metadata for those PMIDs. esummary
    keys results by uid, so we re-order by the ``uids`` list it echoes back to
    preserve esearch's relevance ranking. NCBI's keyless limit (3 req/s) is
    generous for two calls, so no per-provider throttle is added.
    """
    term = query
    if filters.open_access:
        term = f"{term} AND free full text[Filter]"
    params: dict[str, Any] = {
        "db": "pubmed", "term": term, "retmax": min(max(limit, 1), 10000),
        "retmode": "json", "sort": "relevance",
        "tool": "bibliographer", "email": mailto(),
    }
    if filters.year_min:
        params["mindate"], params["datetype"] = filters.year_min, "pdat"
    if filters.year_max:
        params["maxdate"], params["datetype"] = filters.year_max, "pdat"
    yr = f"y{filters.year_min}-{filters.year_max}" if (filters.year_min or filters.year_max) else ""
    oa = "oa" if filters.open_access else ""
    status, body = await _cached_get(
        client, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        key=f"pubmed-esearch|{query.lower()}|{yr}|{oa}|n{limit}|v1", params=params,
    )
    if status != 200:
        raise _resolvers.ResolveError(f"PubMed esearch {status}")
    pmids: list[str] = json.loads(body).get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []
    status, body = await _cached_get(
        client, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
        key=f"pubmed-esummary|{','.join(pmids)}|v1",
        params={"db": "pubmed", "id": ",".join(pmids), "retmode": "json",
                "tool": "bibliographer", "email": mailto()},
    )
    if status != 200:
        raise _resolvers.ResolveError(f"PubMed esummary {status}")
    result = json.loads(body).get("result", {})
    items = [result[uid] for uid in result.get("uids", pmids) if uid in result]
    return _stamp([_from_pubmed(it) for it in items][:limit], "pubmed")


# --- Europe PMC ------------------------------------------------------------- #
def _epmc_authors(r: dict[str, Any]) -> list[dict[str, str]]:
    """Structured authors from a Europe PMC result.

    Prefers ``authorList.author[]`` (full given names) over the flat
    ``authorString`` (initials only); falls back to comma-splitting the latter.
    """
    author_list = ((r.get("authorList") or {}).get("author") or [])
    if author_list:
        authors = []
        for a in author_list:
            ln = a.get("lastName") or ""
            fn = a.get("firstName") or a.get("initials") or ""
            if ln or fn:
                authors.append({"family": ln, "given": fn})
        if authors:
            return authors
    author_str = (r.get("authorString") or "").rstrip(".")
    if author_str:
        return [_author_from_display_name(n.strip()) for n in author_str.split(",") if n.strip()]
    return []


def _epmc_venue(r: dict[str, Any]) -> str | None:
    """Journal title from top-level ``journalTitle`` or nested ``journalInfo``."""
    if r.get("journalTitle"):
        return r["journalTitle"]
    return ((r.get("journalInfo") or {}).get("journal") or {}).get("title") or None


def _epmc_oa_pdf(r: dict[str, Any]) -> str | None:
    """First OA PDF URL in ``fullTextUrlList`` (Unpaywall/publisher before render)."""
    for u in ((r.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
        if u.get("availabilityCode") == "OA" and u.get("documentStyle") == "pdf":
            return u.get("url") or None
    return None


def _from_europepmc(r: dict[str, Any]) -> dict[str, Any]:
    """Normalise one Europe PMC ``resultList.result`` item to a record."""
    doi = (r.get("doi") or "").lower() or None
    pmid = r.get("pmid") or None
    source_url = (
        f"https://doi.org/{doi}" if doi
        else (f"https://europepmc.org/article/MED/{pmid}" if pmid else None)
    )
    return _drop_empty({
        "title": _strip_jats(r.get("title")),
        "authors": _epmc_authors(r),
        "year": int(r["pubYear"]) if r.get("pubYear") else None,
        "venue": _epmc_venue(r),
        "doi": doi,
        "pmid": pmid,
        "pmcid": r.get("pmcid") or None,
        "abstract": _strip_jats(r.get("abstractText")),
        "source_url": source_url,
        "cited_by_count": r.get("citedByCount"),
        "oa_pdf_url": _epmc_oa_pdf(r),
        "bibtex_type": "article",
        "source": "europepmc",
    })


async def search_europepmc(
    query: str, client: httpx.AsyncClient, *, limit: int, filters: Filters
) -> list[dict[str, Any]]:
    """Europe PMC search — free, no key, deep biomedical + preprint coverage.

    Complements OpenAlex with richer MeSH/PMC full-text. Year and OA filters are
    baked into the Lucene query (Europe PMC has no separate filter param):
    ``PUB_YEAR:[lo TO hi]`` and ``OPEN_ACCESS:Y``; open ends use 0/3000 sentinels.
    Abstracts arrive as JATS HTML and are tag-stripped by ``_strip_jats``.
    """
    q = query
    if filters.year_min or filters.year_max:
        q = f"{q} AND PUB_YEAR:[{filters.year_min or 0} TO {filters.year_max or 3000}]"
    if filters.open_access:
        q = f"{q} AND OPEN_ACCESS:Y"
    status, body = await _cached_get(
        client, "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        key=f"europepmc-search|{q.lower()}|n{limit}|v1",
        params={"query": q, "format": "json",
                "pageSize": min(max(limit, 1), 1000), "resultType": "core"},
    )
    if status != 200:
        raise _resolvers.ResolveError(f"Europe PMC {status}")
    results = (json.loads(body).get("resultList") or {}).get("result") or []
    return _stamp([_from_europepmc(r) for r in results][:limit], "europepmc")


# --- arXiv ------------------------------------------------------------------ #
_ARXIV_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _from_arxiv_entry(entry: ET.Element, ns: dict[str, str]) -> dict[str, Any]:
    """Normalise one Atom ``<entry>`` from the arXiv search feed to a record.

    Mirrors ``_resolvers._from_arxiv`` per entry (that fn parses only the first
    entry of a feed). The ``<id>`` is a URL carrying the current version suffix
    (``…/abs/2106.01345v1``); strip the version for the canonical ``arxiv_id``.
    """
    id_url = (entry.findtext("a:id", "", ns) or "").strip()
    m = re.search(r"/abs/(.+?)(?:v\d+)?$", id_url)
    arxiv_id = m.group(1) if m else (id_url.rsplit("/", 1)[-1] if id_url else None)
    authors = []
    for a in entry.findall("a:author", ns):
        name = (a.findtext("a:name", "", ns) or "").strip()
        authors.append(_author_from_display_name(name))
    published = entry.findtext("a:published", "", ns) or ""
    doi = entry.findtext("arxiv:doi", None, ns)
    return _drop_empty({
        "title": " ".join((entry.findtext("a:title", "", ns) or "").split()),
        "authors": authors,
        "year": int(published[:4]) if published[:4].isdigit() else None,
        "venue": "arXiv preprint",
        "doi": doi.lower() if doi else None,
        "arxiv_id": arxiv_id,
        "abstract": " ".join((entry.findtext("a:summary", "", ns) or "").split()),
        "source_url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
        "bibtex_type": "misc",
        "source": "arxiv",
    })


async def search_arxiv(
    query: str, client: httpx.AsyncClient, *, limit: int, filters: Filters
) -> list[dict[str, Any]]:
    """arXiv Atom search (relevance) — CS / physics / quant-bio preprints, all OA.

    arXiv's API has no clean date filter, so ``year_min``/``year_max`` are applied
    client-side; ``open_access`` is a no-op (everything on arXiv is open). The
    ``all:`` field prefix searches every field; httpx encodes the param (do not
    pre-quote it, or the colon double-encodes and returns nothing).
    """
    status, body = await _cached_get(
        client, "https://export.arxiv.org/api/query",
        key=f"arxiv-search|{query.lower()}|n{limit}|v1",
        params={"search_query": f"all:{query}", "max_results": min(max(limit, 1), 100),
                "sortBy": "relevance"},
    )
    if status != 200:
        raise _resolvers.ResolveError(f"arXiv {status}")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise _resolvers.ResolveError(f"arXiv parse error: {exc}") from exc
    recs = [_from_arxiv_entry(e, _ARXIV_NS) for e in root.findall("a:entry", _ARXIV_NS)]
    if filters.year_min or filters.year_max:
        lo, hi = filters.year_min or 0, filters.year_max or 9999
        recs = [r for r in recs if r.get("year") and lo <= r["year"] <= hi]
    return _stamp(recs[:limit], "arxiv")


PROVIDERS: dict[str, Provider] = {
    "openalex": search_openalex,
    "crossref": search_crossref,
    "semantic_scholar": search_semantic_scholar,
    "pubmed": search_pubmed,
    "europepmc": search_europepmc,
    "arxiv": search_arxiv,
}

# Default sweep order — broadest/most-reliable first. ``discover`` runs them
# concurrently regardless; this only sets the default selection and tie-break.
DEFAULT_SOURCES: tuple[str, ...] = (
    "openalex", "semantic_scholar", "europepmc", "pubmed", "crossref", "arxiv",
)


# --------------------------------------------------------------------------- #
# cross-provider merge / dedup
# --------------------------------------------------------------------------- #
def _dedup_key(rec: dict[str, Any]) -> str:
    """Stable identity for cross-provider dedup: best identifier, else title+year.

    Mirrors the store's ``find_duplicate`` priority (DOI > arXiv > PMCID > PMID >
    S2), falling back to normalized-title + year so the same paper surfaced by two
    sources — one with a DOI, one without — still collapses when titles match.
    """
    for key in _meta.IDENTIFIER_KEYS:
        v = rec.get(key)
        if v:
            return f"{key}:{str(v).lower()}"
    nt = _meta.norm_title(rec.get("title"))
    if nt:
        # Collapse internal whitespace runs (norm_title doesn't) so the same
        # title from two sources with slightly different spacing still merges.
        nt = " ".join(nt.split())
        return f"title:{nt}|{rec.get('year') or ''}"
    return f"id:{id(rec)}"  # unmergeable — keep as its own row


def _richness(rec: dict[str, Any]) -> int:
    """How many substantive fields a record carries (merge prefers the richer)."""
    return sum(
        1 for k in ("abstract", "doi", "venue", "authors", "year", "pmid", "arxiv_id")
        if rec.get(k)
    )


def merge_candidates(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge per-provider result lists into one ranked, de-duplicated list.

    A paper found by several sources is corroborated, so it ranks higher. For
    each dedup group we keep the richest record as the base, fill missing fields
    from the others, and stamp:

    * ``found_in``      — sorted list of sources that surfaced it
    * ``source_count``  — len(found_in) (the corroboration signal)
    * ``best_rank``     — best (lowest) per-provider relevance rank
    * ``cited_by_count``— max seen across sources (OpenAlex supplies it)

    Sort key: more sources first, then better rank, then more citations. This is
    a heuristic blend, not a calibrated cross-engine score — good enough to float
    the strongly-corroborated, highly-ranked papers to the top of a sweep.
    """
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for rec in group:
            key = _dedup_key(rec)
            cur = merged.get(key)
            if cur is None:
                base = dict(rec)
                base["found_in"] = [rec.get("discovery_source", "?")]
                base["best_rank"] = rec.get("relevance_rank", 0)
                merged[key] = base
                continue
            # Merge into the existing group entry.
            cur["found_in"].append(rec.get("discovery_source", "?"))
            cur["best_rank"] = min(cur["best_rank"], rec.get("relevance_rank", 0))
            # Promote the richer record's scalar fields, but never lose data.
            richer, poorer = (rec, cur) if _richness(rec) > _richness(cur) else (cur, rec)
            for k, v in richer.items():
                if k in ("found_in", "best_rank", "discovery_source", "relevance_rank"):
                    continue
                if v not in (None, "", [], {}) and cur.get(k) in (None, "", [], {}):
                    cur[k] = v
            for k, v in poorer.items():
                if v not in (None, "", [], {}) and cur.get(k) in (None, "", [], {}):
                    cur[k] = v
            if rec.get("cited_by_count") is not None:
                cur["cited_by_count"] = max(
                    cur.get("cited_by_count") or 0, rec["cited_by_count"]
                )

    out = list(merged.values())
    for rec in out:
        rec["found_in"] = sorted(set(rec["found_in"]))
        rec["source_count"] = len(rec["found_in"])
        rec.pop("discovery_source", None)
        rec.pop("relevance_rank", None)
    out.sort(
        key=lambda r: (
            -r.get("source_count", 1),
            r.get("best_rank", 1_000_000),
            -(r.get("cited_by_count") or 0),
        )
    )
    return out


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
async def discover(
    query: str,
    *,
    sources: list[str] | None = None,
    limit: int = 25,
    filters: Filters | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fan out a query across providers and return merged candidates + a report.

    Returns ``{"results": [record, …], "sources": {name: count|error-str}}``.
    Providers run concurrently; one source failing never aborts the sweep — its
    entry in ``sources`` becomes an error string and the rest still merge.
    """
    filters = filters or Filters()
    names = list(sources or DEFAULT_SOURCES)
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        raise ValueError(
            f"unknown source(s): {', '.join(unknown)}; "
            f"available: {', '.join(sorted(PROVIDERS))}"
        )

    own = client is None
    client = client or httpx.AsyncClient(
        timeout=30, headers={"User-Agent": _resolvers._user_agent()}, follow_redirects=True
    )
    try:
        async def _run(name: str) -> tuple[str, list[dict[str, Any]] | Exception]:
            try:
                return name, await PROVIDERS[name](query, client, limit=limit, filters=filters)
            except Exception as exc:  # noqa: BLE001 — one source must not sink the sweep
                return name, exc

        pairs = await asyncio.gather(*(_run(n) for n in names))
        groups: list[list[dict[str, Any]]] = []
        report: dict[str, Any] = {}
        for name, res in pairs:
            if isinstance(res, Exception):
                report[name] = f"error: {type(res).__name__}: {res}"
            else:
                report[name] = len(res)
                groups.append(res)
        return {"results": merge_candidates(groups), "sources": report}
    finally:
        if own:
            await client.aclose()
