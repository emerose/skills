"""Pure metadata helpers for regulator: the document model, citekeys, and the
mapping between a regulator *record* and a libkit document's ``metadata`` dict.

Dependency-free (stdlib only) so it imports and unit-tests without libkit or a
network.

## The record model

A regulator *record* is a plain ``dict`` describing one regulatory document — an
FDA guidance, one PDF from a Drugs@FDA approval package, an advisory-committee
material, or a personnel dossier. Every record carries a ``doc_type`` that says
which kind it is; the type-specific fields hang off that. When a document is
stored, the record is flattened into a single libkit ``metadata`` mapping (see
:func:`record_to_metadata`); libkit promotes four keys to real columns
(``title``, ``date``, ``source_url``, ``content_type``) and keeps everything
else as free-form JSON. Reading a libkit ``Document`` back into a record is
:func:`document_to_record`.

libkit owns the byte-level identity (``document_id`` = SHA-256 of the file).
Document-level identity — the human-readable ``citekey`` and dedup by a
type-specific natural key — is regulator's job and lives in these metadata keys.

## Document types

``guidance``       — a published FDA guidance document
``drugsfda``       — one PDF from a Drugs@FDA application (review / label / letter)
``adcomm``         — an advisory-committee material (briefing / transcript / roster / …)
``personnel``      — a biographical dossier on an FDA staffer (markdown)
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# libkit promotes these to top-level columns; everything else in the dict we
# pass to ``ingest(metadata=...)`` is stored as free-form JSON. We deliberately
# never use these names for regulator-specific data.
LIBKIT_TOP_LEVEL = frozenset({"title", "date", "source_url", "content_type"})

DOC_TYPES = ("guidance", "drugsfda", "adcomm", "personnel")

# Per-type natural-key fields, used for document-level dedup (layered over
# libkit's byte identity). The first present key whose value matches an existing
# record means "same document". ``citekey`` is always a fallback dedup key.
NATURAL_KEYS = {
    "guidance": ("guidance_id", "docket_number"),
    "drugsfda": ("doc_url",),  # the accessdata PDF URL is the stable identity
    "adcomm": ("media_id", "doc_url"),
    "personnel": ("person_id",),
}

STOPWORDS = {
    "the", "a", "an", "of", "on", "in", "and", "or", "for", "to", "with",
    "via", "using", "from", "by", "is", "are", "at", "as", "into", "guidance",
    "draft", "final", "industry",
}


# --------------------------------------------------------------------------- #
# slugs / normalisation
# --------------------------------------------------------------------------- #
def ascii_slug(text: str) -> str:
    """Lowercase ASCII fold, keep alphanumerics only."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def slug_words(text: str, *, limit: int = 5, drop_stopwords: bool = True) -> str:
    """A hyphenated slug of the first ``limit`` significant words of ``text``."""
    out: list[str] = []
    for tok in re.findall(r"[A-Za-z0-9]+", text or ""):
        low = tok.lower()
        if drop_stopwords and low in STOPWORDS:
            continue
        out.append(ascii_slug(tok))
        if len(out) >= limit:
            break
    return "-".join(w for w in out if w)


def norm_title(title: str | None) -> str:
    """Normalised title for fuzzy duplicate matching."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", "", t.lower()).strip()


def _year_of(rec: dict[str, Any]) -> str:
    for key in ("year", "date", "issue_date", "approval_date", "meeting_date"):
        v = rec.get(key)
        if v:
            m = re.search(r"(19|20)\d{2}", str(v))
            if m:
                return m.group(0)
    return "nd"


# --------------------------------------------------------------------------- #
# citekeys (the stable human handle, per doc_type)
# --------------------------------------------------------------------------- #
def make_citekey(rec: dict[str, Any]) -> str:
    """Build a readable, type-specific citekey.

    * guidance  -> ``guidance-<year>-<title-slug>``
    * drugsfda  -> ``<APPNO>-<submission>-<reviewtype>`` e.g. ``NDA205834-s000-medr``
    * adcomm    -> ``<committee>-<date>-<material>`` e.g. ``odac-2024-07-25-briefing``
    * personnel -> ``person-<name-slug>``

    The result is later uniquified by the store (``unique_citekey``) if it
    collides.
    """
    dt = rec.get("doc_type")
    if dt == "drugsfda":
        appno = ascii_slug(rec.get("application_number") or "app").upper()
        sub = ascii_slug(rec.get("submission") or rec.get("submission_number") or "s000")
        rtype = ascii_slug(rec.get("review_type") or rec.get("doc_subtype") or "doc")
        return f"{appno}-{sub}-{rtype}"
    if dt == "adcomm":
        comm = ascii_slug(rec.get("committee_abbr") or rec.get("committee") or "adcomm")
        date = re.sub(r"[^0-9-]", "", str(rec.get("meeting_date") or rec.get("date") or "")) or _year_of(rec)
        mat = ascii_slug(rec.get("material_type") or "material")
        return "-".join(p for p in (comm, date, mat) if p)
    if dt == "personnel":
        return "person-" + (slug_words(rec.get("name") or "", limit=3, drop_stopwords=False) or "unknown")
    # guidance (and a sane default for anything else)
    year = _year_of(rec)
    body = slug_words(rec.get("title") or rec.get("docket_number") or "", limit=5)
    return "-".join(p for p in ("guidance", year, body) if p) or "guidance"


# --------------------------------------------------------------------------- #
# record  <->  libkit metadata
# --------------------------------------------------------------------------- #
def record_to_metadata(rec: dict[str, Any]) -> dict[str, Any]:
    """Flatten a record into the single ``metadata`` mapping passed to
    ``Library.ingest(metadata=...)``.

    Includes the libkit top-level keys (``title``/``date``/``source_url``/
    ``content_type``) when present — libkit splits them into columns — and every
    other non-empty record key as free-form JSON. ``None``/empty values are
    dropped so they don't clobber existing data on a merge.
    """
    meta: dict[str, Any] = {}
    for key, value in rec.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        meta[key] = value
    return meta


def document_to_record(doc: Any) -> dict[str, Any]:
    """Build a record dict from a libkit ``Document``.

    The free-form ``metadata`` JSON carries everything; we overlay libkit's
    authoritative top-level fields and the immutable ``document_id`` /
    ``content_hash`` so callers always see the byte identity.
    """
    rec: dict[str, Any] = dict(doc.metadata or {})
    if doc.title:
        rec["title"] = doc.title
    rec["document_id"] = doc.document_id
    rec["content_hash"] = doc.content_hash
    rec.setdefault("source_url", doc.source_url)
    rec.setdefault("content_type", doc.content_type)
    rec["_page_count"] = doc.page_count
    rec["_chunk_count"] = doc.chunk_count
    return rec


# --------------------------------------------------------------------------- #
# human rendering
# --------------------------------------------------------------------------- #
def one_line(rec: dict[str, Any]) -> str:
    """A compact one-line description for list/table views."""
    dt = rec.get("doc_type") or "?"
    title = rec.get("title") or "(untitled)"
    year = _year_of(rec)
    if dt == "drugsfda":
        who = rec.get("sponsor_name") or rec.get("brand_name") or rec.get("application_number") or ""
        what = rec.get("review_type") or rec.get("doc_subtype") or "doc"
        return f"[{rec.get('citekey','?')}] {rec.get('application_number','')} {what} — {who} ({year})"
    if dt == "guidance":
        status = rec.get("status") or ""
        org = rec.get("fda_org") or rec.get("center") or ""
        return f"[{rec.get('citekey','?')}] {title} — {org} {status} ({year})".replace("  ", " ")
    if dt == "adcomm":
        comm = rec.get("committee_abbr") or rec.get("committee") or ""
        mat = rec.get("material_type") or ""
        return f"[{rec.get('citekey','?')}] {comm} {mat}: {title} ({rec.get('meeting_date', year)})"
    if dt == "personnel":
        role = rec.get("role") or ""
        div = rec.get("division") or rec.get("office") or rec.get("center") or ""
        return f"[{rec.get('citekey','?')}] {rec.get('name', title)} — {role}, {div}".rstrip(", ")
    return f"[{rec.get('citekey','?')}] {title} ({year})"
