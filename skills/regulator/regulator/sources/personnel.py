"""FDA personnel source — biographical dossiers on reviewers and officials.

There is **no structured FDA staff API.** The best machine signal for tying a
named reviewer to a specific application is the **electronic signature block** at
the end of every Drugs@FDA review PDF, e.g.::

    This is a representation of an electronic record that was signed
    electronically and this page is the manifestation of the electronic
    signature.
    --------------------------------------------------------------------
    /s/
    --------------------------------------------------------------------
    JOHN J JENKINS
    07/31/2014

So a dossier is assembled semi-automatically: (1) parse signatures off the
review PDFs already in the library to harvest ``(name, date, application,
review_type)`` rows; (2) aggregate by person; (3) enrich with role/division from
the FDA org charts and a web-research bio (an agent step). The dossier is stored
as a searchable Markdown document (``doc_type=personnel``).

:func:`extract_signatures` and :func:`dossier_markdown` are pure and unit-test
offline; the harvesting/aggregation orchestration lives in the CLI (it needs the
library text) and the bio enrichment is an agent task.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
# A plausible signature name: 2–4 ALL-CAPS tokens (FDA signs in caps on the
# manifest page), allowing a middle initial and hyphen/apostrophe surnames.
_NAME = r"[A-Z][A-Z'.\-]+(?:\s+[A-Z][A-Z'.\-]*){1,3}"
# A signature line is a bare name, or "SIGNER on behalf of PRINCIPAL" (proxy
# signatures, common on approval letters — the principal is the official of record).
_SIGLINE_RE = re.compile(rf"^\**({_NAME})(?:\s+on behalf of\s+({_NAME}))?\**\s*$")
_DASHES = re.compile(r"^[-_=]{3,}$")


def normalize_name(name: str) -> str:
    """Title-case a signature name for display (``JOHN J JENKINS`` -> ``John J Jenkins``)."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    out = []
    for tok in name.split(" "):
        out.append(tok if (len(tok) <= 2 and tok.isupper()) else tok.capitalize())
    return " ".join(out)


def person_slug(name: str) -> str:
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def extract_signatures(text: str) -> list[dict[str, str]]:
    """Pull ``[{name, date}]`` from a review PDF's electronic-signature page(s).

    Strategy: anchor on the "manifestation of the electronic signature" marker
    when present (precise), else fall back to scanning the whole text for an
    ALL-CAPS name line immediately followed (within a couple of lines) by an
    ``MM/DD/YYYY`` date — the canonical FDA signature shape. De-duplicated by
    ``(name, date)``.
    """
    lines = [ln.strip() for ln in (text or "").splitlines()]
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for i, ln in enumerate(lines):
        m = _SIGLINE_RE.match(ln)
        if not m:
            continue
        signer, principal = m.group(1), m.group(2)
        # the official of record is the principal when signed by proxy, else the signer
        name = normalize_name(principal or signer)
        proxy = normalize_name(signer) if principal else None
        # look ahead a few lines for a date (skipping blanks / dashes / "/s/" / "Acting on behalf…")
        date = None
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j]
            if not nxt or _DASHES.match(nxt) or nxt.lower().startswith(("/s/", "(b)", "acting on behalf")):
                continue
            dm = _DATE_RE.search(nxt)
            if dm:
                date = f"{int(dm.group(3)):04d}-{int(dm.group(1)):02d}-{int(dm.group(2)):02d}"
            break
        if date is None:
            continue
        key = (name, date)
        if key in seen:
            continue
        seen.add(key)
        rec = {"name": name, "date": date}
        if proxy:
            rec["signed_by"] = proxy
        found.append(rec)
    return found


def aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group harvested signature rows by person.

    Each ``row`` is ``{name, date, application_number?, review_type?,
    sponsor_name?, brand_name?}``. Returns ``{person_slug: dossier_dict}`` with a
    de-duplicated, date-sorted ``signed_reviews`` list.
    """
    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = normalize_name(row.get("name") or "")
        if not name:
            continue
        slug = person_slug(name)
        person = people.setdefault(slug, {
            "doc_type": "personnel",
            "person_id": slug,
            "name": name,
            "title": name,
            "signed_reviews": [],
        })
        entry = {
            "date": row.get("date"),
            "application_number": row.get("application_number"),
            "review_type": row.get("review_type"),
            "doc_subtype": row.get("doc_subtype"),
            "sponsor_name": row.get("sponsor_name"),
            "brand_name": row.get("brand_name"),
        }
        if row.get("signed_by"):
            entry["signed_by"] = row["signed_by"]
        if entry not in person["signed_reviews"]:
            person["signed_reviews"].append(entry)
    for person in people.values():
        person["signed_reviews"].sort(key=lambda e: e.get("date") or "")
        person["n_signed_reviews"] = len(person["signed_reviews"])
        rtypes = sorted({e.get("review_type") for e in person["signed_reviews"] if e.get("review_type")})
        person["review_disciplines"] = rtypes
        if rtypes:
            person["tags"] = ["personnel", *rtypes]
    return people


def dossier_markdown(person: dict[str, Any]) -> str:
    """Render a personnel dossier as Markdown (the stored, searchable document)."""
    lines = [f"# {person.get('name') or 'Unknown FDA staffer'}", ""]
    for label, key in (("Role", "role"), ("Division", "division"),
                       ("Office", "office"), ("Center", "center")):
        if person.get(key):
            lines.append(f"- **{label}:** {person[key]}")
    if person.get("review_disciplines"):
        lines.append(f"- **Review disciplines:** {', '.join(person['review_disciplines'])}")
    lines.append("")
    if person.get("bio"):
        lines += ["## Biography", "", str(person["bio"]).strip(), ""]
    reviews = person.get("signed_reviews") or []
    if reviews:
        lines += [f"## Signed reviews ({len(reviews)})", ""]
        for e in reviews:
            bits = [e.get("date") or "????",
                    e.get("application_number") or "",
                    e.get("doc_subtype") or e.get("review_type") or ""]
            who = e.get("brand_name") or e.get("sponsor_name")
            line = " — ".join(b for b in bits if b)
            if who:
                line += f" ({who})"
            lines.append(f"- {line}")
        lines.append("")
    if person.get("sources"):
        lines += ["## Sources", ""]
        for s in person["sources"]:
            lines.append(f"- {s}")
        lines.append("")
    return "\n".join(lines) + "\n"
