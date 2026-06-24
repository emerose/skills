"""FDA Guidance Documents source.

The entire guidance corpus (~4,000 documents) is served as one JSON feed that
backs the DataTables grid on the FDA guidance search page:

    https://www.fda.gov/datatables-json/search-for-guidance.json
    (page: https://www.fda.gov/regulatory-information/search-fda-guidance-documents)

That feed sits behind Akamai bot protection — a plain request from a flagged
egress gets an HTTP 503 challenge — so :func:`fetch_corpus` sends browser-like
headers and, if that still fails, accepts a locally-saved copy via ``from_file``
(download the page's "Export"/JSON once in a real browser, or pull the feed from
an un-flagged IP, and point us at it). Once we have the row list, each guidance
**PDF** is fetched from ``https://www.fda.gov/media/<id>/download``, which is
*not* gated.

We cache the parsed corpus at ``<home>/guidance_index.json`` so search/add work
offline after one successful sync. Pure stdlib + httpx; the parser
(:func:`parse_rows`) unit-tests offline.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import httpx

# The full guidance corpus (~2,800 docs) is served as one static JSON file — the
# DataTables grid on the search page loads it client-side. This static path is
# NOT bot-gated (unlike the legacy /datatables-json/ AJAX path, which Akamai 503s).
CORPUS_URL = "https://www.fda.gov/files/api/datatables/static/search-for-guidance.json"
SEARCH_PAGE = "https://www.fda.gov/regulatory-information/search-fda-guidance-documents"

# The corpus rows are objects keyed by FDA Drupal field names. Map them to our
# record fields. The `title` cell is an HTML anchor (title text + landing-page
# href); `field_associated_media_2` is an HTML anchor to the /media/<id> PDF.
FDA_FIELDS = {
    "title": "title",                              # HTML anchor: title + landing page
    "field_associated_media_2": "pdf",             # HTML anchor: /media/<id>/download
    "field_issue_datetime": "issue_date",
    "field_issuing_office_taxonomy": "fda_org",
    "field_center": "center",
    "topics-product": "topic",
    "field_final_guidance_1": "status",
    "field_comment_close_date": "comment_close",
    "field_docket_number": "docket_number",        # HTML anchor: regulations.gov docket
    "field_communication_type": "guidance_type",
    "field_regulated_product_field": "regulated_product",
}

# Default column order of the legacy DataTables grid (used only when rows are
# positional arrays rather than keyed objects).
DEFAULT_COLUMNS = [
    "document", "issue_date", "fda_org", "topic", "status",
    "open_for_comment", "comment_close", "docket_number", "guidance_type",
]

_ANCHOR_RE = re.compile(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_MEDIA_RE = re.compile(r"/media/(\d+)/download", re.I)


def _strip_tags(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def _first_anchor(cell: str) -> tuple[str | None, str]:
    """Return ``(href, text)`` of the first anchor in an HTML table cell."""
    if not cell:
        return None, ""
    m = _ANCHOR_RE.search(cell)
    if not m:
        return None, _strip_tags(cell)
    return m.group(1), _strip_tags(m.group(2))


def _abs_url(href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.fda.gov" + href
    return href


def media_pdf_url(rec: dict[str, Any]) -> str | None:
    """The downloadable PDF URL for a guidance record, if one is discoverable."""
    for key in ("pdf_url", "source_url", "document_url"):
        u = rec.get(key)
        if u and _MEDIA_RE.search(u):
            return _abs_url(u)
    u = rec.get("source_url")
    return _abs_url(u) if u else None


def _fda_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Parse one FDA-schema corpus row (keys like ``field_issue_datetime``)."""
    mapped = {dst: row.get(src) for src, dst in FDA_FIELDS.items()}
    landing, title = _first_anchor(str(mapped.get("title") or ""))
    pdf_href, _ = _first_anchor(str(mapped.get("pdf") or ""))
    if not title:
        return None
    rec: dict[str, Any] = {
        "doc_type": "guidance",
        "title": title,
        "source_url": _abs_url(landing),
        "issue_date": _strip_tags(str(mapped.get("issue_date") or "")) or None,
        "fda_org": _strip_tags(str(mapped.get("fda_org") or "")) or None,
        "center": _strip_tags(str(mapped.get("center") or "")) or None,
        "topic": _strip_tags(str(mapped.get("topic") or "")) or None,
        "status": _strip_tags(str(mapped.get("status") or "")) or None,
        "comment_close": _strip_tags(str(mapped.get("comment_close") or "")) or None,
        "docket_number": _strip_tags(str(mapped.get("docket_number") or "")) or None,
        "guidance_type": _strip_tags(str(mapped.get("guidance_type") or "")) or None,
        "regulated_product": _strip_tags(str(mapped.get("regulated_product") or "")) or None,
    }
    pdf = _abs_url(pdf_href)
    if pdf and _MEDIA_RE.search(pdf):
        rec["pdf_url"] = pdf
        m = _MEDIA_RE.search(pdf)
        rec["guidance_id"] = "media-" + m.group(1) if m else None
    rec["guidance_id"] = rec.get("guidance_id") or (rec.get("docket_number") or title)[:80] or None
    return rec


def parse_rows(data: Any, columns: list[str] | None = None) -> list[dict[str, Any]]:
    """Parse a guidance corpus payload into records.

    Accepts the static corpus (a bare list of FDA-schema objects), the legacy
    DataTables feed (``{"data": [...]}``), and positional-array rows. FDA-schema
    rows (keyed by ``field_*`` names) are routed to :func:`_fda_row`; anything
    else falls back to the generic ``document``-cell parsing.
    """
    if isinstance(data, dict):
        rows = data.get("data") or data.get("aaData") or []
    else:
        rows = data or []
    columns = columns or DEFAULT_COLUMNS

    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and ("field_issue_datetime" in row or "field_associated_media_2" in row):
            rec = _fda_row(row)
            if rec:
                out.append(rec)
            continue
        if isinstance(row, dict):
            cells = {k.lower(): v for k, v in row.items()}
            get = lambda k: cells.get(k)  # noqa: E731
        else:
            get = lambda k, _row=row: _row[columns.index(k)] if k in columns and columns.index(k) < len(_row) else None  # noqa: E731

        doc_cell = get("document") or get("title") or ""
        href, title = _first_anchor(str(doc_cell))
        url = _abs_url(href)
        rec = {
            "doc_type": "guidance",
            "title": title or _strip_tags(str(doc_cell)),
            "source_url": url,
            "issue_date": _strip_tags(str(get("issue_date") or "")) or None,
            "fda_org": _strip_tags(str(get("fda_org") or "")) or None,
            "topic": _strip_tags(str(get("topic") or "")) or None,
            "status": _strip_tags(str(get("status") or "")) or None,
            "comment_close": _strip_tags(str(get("comment_close") or "")) or None,
            "docket_number": _strip_tags(str(get("docket_number") or "")) or None,
            "guidance_type": _strip_tags(str(get("guidance_type") or "")) or None,
        }
        if url and _MEDIA_RE.search(url):
            rec["pdf_url"] = url
            m = _MEDIA_RE.search(url)
            rec["guidance_id"] = "media-" + m.group(1) if m else None
        rec["guidance_id"] = rec.get("guidance_id") or (rec.get("docket_number") or title or "")[:80] or None
        summary = get("summary")
        if summary:
            rec["summary"] = _strip_tags(str(summary))
        if rec.get("title"):
            out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# network + cache
# --------------------------------------------------------------------------- #
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SEARCH_PAGE,
        },
        timeout=httpx.Timeout(120.0),
        follow_redirects=True,
    )


class GatedError(RuntimeError):
    """The guidance JSON feed returned a bot-challenge instead of data."""


async def fetch_corpus(
    *,
    from_file: Path | None = None,
    cl: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch + parse the full guidance corpus, or load it from ``from_file``.

    Raises :class:`GatedError` when the live feed is blocked by the bot wall, so
    the CLI can tell the user to supply a saved copy with ``--from-file``.
    """
    if from_file is not None:
        raw = Path(from_file).read_text(encoding="utf-8")
        return parse_rows(json.loads(raw))
    owns = cl is None
    cl = cl or client()
    try:
        r = await cl.get(CORPUS_URL)
        if r.status_code in (403, 503) or "text/html" in r.headers.get("content-type", ""):
            raise GatedError(
                f"guidance feed returned HTTP {r.status_code} (bot challenge). "
                "Fetch it once in a real browser / from an un-flagged IP and pass it "
                "with `reg guidance sync --from-file <saved.json>`."
            )
        r.raise_for_status()
        return parse_rows(r.json())
    finally:
        if owns:
            await cl.aclose()


def cache_path(home: Path) -> Path:
    return home / "guidance_index.json"


def save_corpus(home: Path, records: list[dict[str, Any]]) -> Path:
    p = cache_path(home)
    p.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_corpus(home: Path) -> list[dict[str, Any]]:
    p = cache_path(home)
    if not p.is_file():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def search_corpus(records: list[dict[str, Any]], query: str, *, limit: int = 25) -> list[dict[str, Any]]:
    """Simple case-insensitive AND-of-terms match over title/topic/org/docket."""
    terms = [t for t in re.split(r"\s+", query.lower()) if t]
    hits = []
    for r in records:
        hay = " ".join(str(r.get(k) or "") for k in ("title", "topic", "fda_org", "center", "regulated_product", "docket_number", "guidance_type", "summary")).lower()
        if all(t in hay for t in terms):
            hits.append(r)
        if len(hits) >= limit:
            break
    return hits


async def download(url: str, dest_dir: Path, *, cl: httpx.AsyncClient, citekey: str | None = None) -> Path:
    """Download a guidance PDF (the /media/<id>/download endpoint is ungated)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    m = _MEDIA_RE.search(url)
    name = (citekey or (("media-" + m.group(1)) if m else "guidance")) + ".pdf"
    dest = dest_dir / name
    async with cl.stream("GET", url) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in resp.aiter_bytes(1 << 16):
                fh.write(chunk)
    return dest
