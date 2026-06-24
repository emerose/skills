"""FDA Advisory Committee (AdComm) materials source.

There is no JSON listing API for advisory-committee materials. Each committee has
HTML hub pages that link the documents through the generic media service:

    https://www.fda.gov/advisory-committees/<committee>/<year>-meeting-materials-<committee>
    https://www.fda.gov/advisory-committees/advisory-committee-calendar/<meeting-slug>
    materials served at: https://www.fda.gov/media/<id>/download   (ungated)

So ingestion is: fetch a hub/meeting page, extract every ``/media/<id>/download``
(or ``.pdf``) link, classify each by its anchor text (briefing document / roster
/ transcript / agenda / presentation / minutes), and download. The human HTML
pages are generally reachable with a browser User-Agent; only the AJAX/JSON
paths are bot-gated. The link extractor (:func:`extract_materials`) unit-tests
offline on saved HTML.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

import httpx

BASE = "https://www.fda.gov"

_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_MEDIA_RE = re.compile(r"/media/(\d+)/download", re.I)
_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b",
    re.I,
)
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}

# Committee abbreviations seen in URLs / names.
COMMITTEE_ABBR = {
    "oncologic-drugs": "ODAC",
    "peripheral-and-central-nervous-system-drugs": "PCNS",
    "psychopharmacologic-drugs": "PDAC",
    "cellular-tissue-and-gene-therapies": "CTGTAC",
    "antimicrobial-drugs": "AMDAC",
    "cardiovascular-and-renal-drugs": "CRDAC",
    "vaccines-and-related-biological-products": "VRBPAC",
    "pediatric": "PAC",
    "gastrointestinal-drugs": "GIDAC",
    "pulmonary-allergy-drugs": "PADAC",
}


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", s or ""))).strip()


def _clean_title(text: str) -> str:
    """Tidy a verbose AdComm anchor like
    '06. September 26, 2024 Meeting of the Oncologic Drugs Advisory Committee- AM- FDA Briefing Document'
    down to the distinctive tail ('AM- FDA Briefing Document')."""
    t = re.sub(r"^\s*\d+\.\s*", "", text)  # drop leading "06. "
    t = re.split(r"Advisory Committee\s*-\s*", t, maxsplit=1)
    return (t[1].strip(" -") if len(t) > 1 else t[0]).strip() or text.strip()


def _abs_url(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BASE + href
    return href


def classify_material(text: str) -> str:
    """Classify an AdComm document by its anchor text."""
    t = (text or "").lower()
    if "transcript" in t:
        return "transcript"
    if "roster" in t or "membership" in t or "members" in t:
        return "roster"
    if "minutes" in t:
        return "minutes"
    if "agenda" in t:
        return "agenda"
    if "presentation" in t or "slides" in t or "slide" in t:
        return "presentation"
    if "question" in t:
        return "questions"
    if "briefing" in t or "background" in t:
        return "briefing"
    if "errata" in t or "amendment" in t:
        return "errata"
    return "material"


def guess_committee(url_or_text: str) -> tuple[str | None, str | None]:
    """Return ``(committee_slug_name, abbreviation)`` guessed from a URL or title."""
    s = (url_or_text or "").lower()
    for slug, abbr in COMMITTEE_ABBR.items():
        if slug in s or abbr.lower() in s:
            name = slug.replace("-", " ").title() + " Advisory Committee"
            return name, abbr
    return None, None


def guess_meeting_date(text: str) -> str | None:
    """Pull an ISO ``YYYY-MM-DD`` from the first 'Month D, YYYY' in the text."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"


def extract_materials(page_html: str, *, page_url: str = "", committee: str | None = None,
                      committee_abbr: str | None = None, meeting_date: str | None = None) -> list[dict[str, Any]]:
    """Extract AdComm material records from a hub/meeting HTML page.

    One record per ``/media/<id>/download`` (or ``.pdf``) link, classified by its
    anchor text. Committee/date are inferred from the page when not supplied.
    """
    if committee is None or committee_abbr is None:
        c, a = guess_committee(page_url or page_html[:4000])
        committee = committee or c
        committee_abbr = committee_abbr or a
    page_date = meeting_date or guess_meeting_date(page_html)

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in _ANCHOR_RE.finditer(page_html):
        href, inner = m.group(1), _strip(m.group(2))
        is_media = bool(_MEDIA_RE.search(href))
        is_pdf = href.lower().split("?")[0].endswith(".pdf")
        if not (is_media or is_pdf):
            continue
        url = _abs_url(href)
        if url in seen or not inner:
            continue
        seen.add(url)
        mid = _MEDIA_RE.search(href)
        out.append({
            "doc_type": "adcomm",
            "committee": committee,
            "committee_abbr": committee_abbr,
            "meeting_date": page_date,
            "material_type": classify_material(inner),
            "title": _clean_title(inner),
            "media_id": mid.group(1) if mid else None,
            "doc_url": url,
            "source_url": url,
            "page_url": page_url or None,
            "tags": [t for t in ("adcomm", (committee_abbr or "").lower(), classify_material(inner)) if t],
        })
    return out


# --------------------------------------------------------------------------- #
# network
# --------------------------------------------------------------------------- #
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=httpx.Timeout(120.0),
        follow_redirects=True,
    )


async def fetch_page(url: str, *, cl: httpx.AsyncClient) -> str:
    r = await cl.get(url)
    r.raise_for_status()
    return r.text


_MEETING_HREF_RE = re.compile(r'href=["\'](/advisory-committees/[^"\']+)["\']', re.I)


def extract_meeting_links(page_html: str) -> list[str]:
    """From a committee *year-materials hub*, return the per-meeting page URLs.

    The hub itself carries no ``/media`` links — it indexes individual meeting
    announcement pages (under ``advisory-committee-calendar`` or
    ``…meeting…announcement…``). Year-hub, roster, and charter links are excluded.
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _MEETING_HREF_RE.finditer(page_html):
        href = m.group(1)
        low = href.lower()
        if "meeting-materials" in low or "roster" in low or "charter" in low:
            continue
        if "meeting" not in low and "calendar" not in low:
            continue
        if low.rstrip("/").endswith("committees-and-meeting-materials"):
            continue
        url = _abs_url(href)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


async def sync_meeting(url: str, *, committee: str | None = None, committee_abbr: str | None = None,
                       meeting_date: str | None = None, recurse: bool = True, max_meetings: int = 40,
                       cl: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Fetch a meeting page (or a year hub) and return its material records.

    If the page is a meeting page, its ``/media`` materials are returned directly.
    If it's a year hub (no materials of its own) and ``recurse`` is set, each
    indexed meeting page is fetched and the materials are aggregated.
    """
    owns = cl is None
    cl = cl or client()
    try:
        page = await fetch_page(url, cl=cl)
        mats = extract_materials(page, page_url=url, committee=committee,
                                 committee_abbr=committee_abbr, meeting_date=meeting_date)
        if mats or not recurse:
            return mats
        agg: list[dict[str, Any]] = []
        for murl in extract_meeting_links(page)[:max_meetings]:
            try:
                mpage = await fetch_page(murl, cl=cl)
            except httpx.HTTPError:
                continue
            agg.extend(extract_materials(mpage, page_url=murl, committee=committee,
                                         committee_abbr=committee_abbr, meeting_date=meeting_date))
        return agg
    finally:
        if owns:
            await cl.aclose()


async def download(url: str, dest_dir: Path, *, cl: httpx.AsyncClient, citekey: str | None = None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    m = _MEDIA_RE.search(url)
    name = (citekey or (("media-" + m.group(1)) if m else "material")) + ".pdf"
    dest = dest_dir / name
    async with cl.stream("GET", url) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in resp.aiter_bytes(1 << 16):
                fh.write(chunk)
    return dest
