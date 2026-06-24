"""Drugs@FDA source: openFDA metadata + accessdata approval-package PDFs.

This is the cleanest FDA source. The openFDA ``drug/drugsfda`` endpoint returns,
for each application, a ``submissions[].application_docs[]`` array that already
lists every approval-package PDF on accessdata.fda.gov — typed and dated — so the
whole corpus is API-enumerable end-to-end with **no HTML scraping**. The PDFs
themselves live on accessdata.fda.gov, which is not bot-gated.

    https://api.fda.gov/drug/drugsfda.json        (metadata + doc URLs)
    https://www.accessdata.fda.gov/drugsatfda_docs/...   (the PDFs)

openFDA allows 1,000 requests/day with no key; set ``OPENFDA_API_KEY`` for
120,000/day. Pure stdlib + httpx; no libkit, so this module unit-tests offline
against fixture JSON (see :func:`parse_application`, :func:`classify_doc`).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx

OPENFDA_URL = "https://api.fda.gov/drug/drugsfda.json"

# accessdata review-PDF filename stem -> (review_type code, human label). The
# stem is the trailing word before ``.pdf`` (e.g. ``205834Orig1s000MedR.pdf``).
# Two filename generations coexist: the older split reviews (MedR/ClinPharmR/…)
# and the modern integrated multidisciplinary review (MultidisciplineR/IntegratedR).
_REVIEW_STEMS: list[tuple[str, str, str]] = [
    ("multidiscipliner", "multidiscipline", "Multidisciplinary Review"),
    ("integratedr", "multidiscipline", "Integrated/Multidisciplinary Review"),
    ("medr", "medical", "Medical Review"),
    ("clinpharmr", "clinpharm", "Clinical Pharmacology Review"),
    ("biopharmr", "clinpharm", "Biopharmaceutics Review"),
    ("statr", "statistical", "Statistical Review"),
    ("chemr", "chemistry", "Chemistry Review"),
    ("pharmr", "pharmtox", "Pharmacology/Toxicology Review"),
    ("micror", "microbiology", "Microbiology Review"),
    ("clinmicror", "microbiology", "Clinical Microbiology Review"),
    ("riskr", "risk", "Risk Assessment / REMS Review"),
    ("rems", "risk", "REMS"),
    ("sumr", "summary", "Summary Review"),
    ("summaryr", "summary", "Summary Review"),
    ("ods", "ods", "Office of Drug Safety Review"),
    ("namer", "name", "Proprietary Name Review"),
    ("oelist", "exclusivity", "Exclusivity / OE List"),
    ("admincorres", "admin", "Administrative & Correspondence"),
    ("approv", "letter", "Approval Letter"),
    ("ltr", "letter", "Approval Letter"),
    ("lbl", "label", "Label"),
    ("prntlbl", "label", "Printed Labeling"),
    ("otherr", "other", "Other Review"),
    ("toc", "toc", "Table of Contents"),
]


def classify_doc(url: str, openfda_type: str | None = None) -> tuple[str, str]:
    """Map an accessdata doc URL to ``(review_type, label)``.

    Uses the filename stem first (most precise), falling back to the openFDA
    ``type`` field. Returns ``("doc", openfda_type or "Document")`` when unknown.
    """
    name = url.rsplit("/", 1)[-1].lower()
    stem = re.sub(r"\.[a-z0-9]+$", "", name)  # strip extension
    # trailing alpha run, e.g. "205834orig1s000medr" -> "medr"; but appletters
    # are like "205834orig1s000ltr". Test each known stem as a suffix.
    for suffix, code, label in _REVIEW_STEMS:
        if stem.endswith(suffix):
            return code, label
    if openfda_type:
        t = openfda_type.strip().lower()
        if "summary" in t:
            return "summary", openfda_type
        if "letter" in t:
            return "letter", openfda_type
        if "label" in t:
            return "label", openfda_type
        if "review" in t:
            return "review", openfda_type
        return re.sub(r"[^a-z0-9]+", "", t)[:16] or "doc", openfda_type
    return "doc", "Document"


def _openfda_first(openfda: dict[str, Any], key: str) -> str | None:
    v = (openfda or {}).get(key)
    if isinstance(v, list) and v:
        return str(v[0])
    return str(v) if v else None


def parse_application(app: dict[str, Any]) -> dict[str, Any]:
    """Summarise one openFDA application record (no docs), for search results."""
    products = app.get("products") or []
    brands = sorted({p.get("brand_name") for p in products if p.get("brand_name")})
    ingredients = sorted({
        ai.get("name")
        for p in products for ai in (p.get("active_ingredients") or [])
        if ai.get("name")
    })
    routes = sorted({p.get("route") for p in products if p.get("route")})
    subs = app.get("submissions") or []
    approvals = [
        s.get("submission_status_date")
        for s in subs
        if s.get("submission_status") == "AP" and s.get("submission_status_date")
    ]
    openfda = app.get("openfda") or {}
    return {
        "application_number": app.get("application_number"),
        "sponsor_name": app.get("sponsor_name"),
        "brand_names": brands,
        "active_ingredients": ingredients,
        "routes": routes,
        "n_submissions": len(subs),
        "first_approval": min(approvals) if approvals else None,
        "latest_approval": max(approvals) if approvals else None,
        "pharm_class_epc": (openfda.get("pharm_class_epc") or [None])[0],
    }


def enumerate_docs(app: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten an application's ``submissions[].application_docs[]`` into records.

    One returned dict per downloadable document, carrying the application-level
    context (sponsor/brand/ingredient) plus the per-doc classification. Only
    http(s) URLs are kept; non-PDF entries (e.g. a TOC ``.html``) are tagged so
    the caller can skip or stub them.
    """
    application_number = app.get("application_number")
    sponsor_name = app.get("sponsor_name")
    products = app.get("products") or []
    brand = next((p.get("brand_name") for p in products if p.get("brand_name")), None)
    ingredient = next(
        (ai.get("name") for p in products for ai in (p.get("active_ingredients") or []) if ai.get("name")),
        None,
    )
    openfda = app.get("openfda") or {}
    generic = _openfda_first(openfda, "generic_name")
    app_kind = "BLA" if str(application_number or "").startswith("BLA") else "NDA"

    out: list[dict[str, Any]] = []
    for sub in app.get("submissions") or []:
        st = (sub.get("submission_type") or "").strip()
        snum = (sub.get("submission_number") or "").strip()
        # canonical short submission tag, e.g. ORIG/1 -> s000 ; SUPPL/17 -> s017
        if st.upper().startswith("ORIG"):
            sub_tag = "s000"
        else:
            sub_tag = "s" + snum.zfill(3) if snum.isdigit() else (st.lower() + snum)
        approval_date = sub.get("submission_status_date") if sub.get("submission_status") == "AP" else None
        klass = sub.get("submission_class_code_description")
        for doc in sub.get("application_docs") or []:
            url = (doc.get("url") or "").strip()
            if not url.lower().startswith("http"):
                continue
            review_type, label = classify_doc(url, doc.get("type"))
            is_pdf = url.lower().endswith(".pdf")
            out.append({
                "doc_type": "drugsfda",
                "application_number": application_number,
                "application_kind": app_kind,
                "sponsor_name": sponsor_name,
                "brand_name": brand,
                "generic_name": generic,
                "active_ingredient": ingredient,
                "submission": sub_tag,
                "submission_type": st,
                "submission_number": snum,
                "submission_class": klass,
                "review_type": review_type,
                "doc_subtype": label,
                "openfda_doc_type": doc.get("type"),
                "doc_id": doc.get("id"),
                "doc_url": url,
                "source_url": url,
                "doc_date": doc.get("date"),
                "approval_date": approval_date,
                "is_pdf": is_pdf,
                "title": f"{application_number} {sub_tag} — {label}"
                         + (f" ({brand})" if brand else ""),
                "tags": ["drugsfda", app_kind.lower(), review_type],
            })
    return out


# --------------------------------------------------------------------------- #
# network
# --------------------------------------------------------------------------- #
def client(*, mailto: str | None = None) -> httpx.AsyncClient:
    ua = "regulator/0.1 (+https://github.com/emerose/skills)"
    if mailto:
        ua += f" mailto:{mailto}"
    return httpx.AsyncClient(
        headers={"User-Agent": ua, "Accept": "application/json"},
        timeout=httpx.Timeout(60.0),
        follow_redirects=True,
    )


def _params(extra: dict[str, Any], api_key: str | None) -> dict[str, Any]:
    p = dict(extra)
    key = api_key or os.environ.get("OPENFDA_API_KEY")
    if key:
        p["api_key"] = key
    return p


async def search(
    query: str,
    *,
    field: str | None = None,
    limit: int = 25,
    api_key: str | None = None,
    mailto: str | None = None,
    cl: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Search Drugs@FDA applications.

    ``field`` chooses the openFDA search field; the convenient defaults are
    ``ingredient`` (active ingredient), ``sponsor``, ``brand``, or ``appno``.
    With no field, a raw openFDA ``search`` expression is passed through.
    """
    expr = {
        "ingredient": f'products.active_ingredients.name:"{query}"',
        "sponsor": f'sponsor_name:"{query}"',
        "brand": f'openfda.brand_name:"{query}"',
        "generic": f'openfda.generic_name:"{query}"',
        "appno": f"application_number:{query}",
    }.get(field or "", query)
    owns = cl is None
    cl = cl or client(mailto=mailto)
    try:
        r = await cl.get(OPENFDA_URL, params=_params({"search": expr, "limit": limit}, api_key))
        if r.status_code == 404:
            return []
        r.raise_for_status()
        results = r.json().get("results", [])
        return [parse_application(a) for a in results]
    finally:
        if owns:
            await cl.aclose()


async def fetch_application(
    appno: str,
    *,
    api_key: str | None = None,
    mailto: str | None = None,
    cl: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Fetch one application's full openFDA record by application number."""
    owns = cl is None
    cl = cl or client(mailto=mailto)
    try:
        r = await cl.get(
            OPENFDA_URL,
            params=_params({"search": f"application_number:{appno}", "limit": 1}, api_key),
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0] if results else None
    finally:
        if owns:
            await cl.aclose()


async def download(url: str, dest_dir: Path, *, cl: httpx.AsyncClient) -> Path:
    """Download a document to ``dest_dir`` (named from the URL); return the path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1] or "document.pdf"
    dest = dest_dir / name
    async with cl.stream("GET", url) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in resp.aiter_bytes(1 << 16):
                fh.write(chunk)
    return dest


async def gather_docs(
    appno: str,
    *,
    pdf_only: bool = True,
    api_key: str | None = None,
    mailto: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Convenience: fetch the application and return ``(summary, doc_records)``."""
    async with client(mailto=mailto) as cl:
        app = await fetch_application(appno, api_key=api_key, cl=cl)
        if app is None:
            return None, []
        docs = enumerate_docs(app)
        if pdf_only:
            docs = [d for d in docs if d.get("is_pdf")]
        return parse_application(app), docs
