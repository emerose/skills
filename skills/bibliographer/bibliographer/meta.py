"""Pure metadata helpers for bibliographer: citekeys, BibTeX, and the mapping
between a bibliographer *record* and a libkit document's ``metadata`` dict.

This module is dependency-free (stdlib only) so it can be imported and unit
tested without libkit or a network.

## The record model

A bibliographer *record* is a plain ``dict`` describing one paper. It is what
resolvers produce and what the CLI prints. When a paper is stored, the record
is flattened into a single libkit ``metadata`` mapping (see
:func:`record_to_metadata`); libkit promotes four keys to real columns
(``title``, ``date``, ``source_url``, ``content_type``) and keeps everything
else as free-form JSON. Reading a libkit ``Document`` back into a record is
:func:`document_to_record`.

libkit owns the byte-level identity (``document_id`` = SHA-256 of the file).
Paper-level identity (citekey, dedup by DOI/arXiv/PMCID) is bibliographer's job
and lives entirely in these metadata keys.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# libkit promotes these to top-level columns; everything else in the dict we
# pass to ``ingest(metadata=...)`` is stored as free-form JSON. We deliberately
# never use these names for bibliographer-specific data.
LIBKIT_TOP_LEVEL = frozenset({"title", "date", "source_url", "content_type"})

# Identifier keys, in the priority order used for paper-level dedup.
IDENTIFIER_KEYS = ("doi", "arxiv_id", "pmcid", "pmid", "s2_id")

STOPWORDS = {
    "the", "a", "an", "of", "on", "in", "and", "or", "for", "to", "with",
    "via", "using", "from", "by", "is", "are", "at", "as", "into",
}


# --------------------------------------------------------------------------- #
# slugs / normalisation
# --------------------------------------------------------------------------- #
def ascii_slug(text: str) -> str:
    """Lowercase ASCII fold, keep alphanumerics only."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def norm_title(title: str | None) -> str:
    """Normalised title for fuzzy duplicate matching."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", "", t.lower()).strip()


# --------------------------------------------------------------------------- #
# citekey + author rendering
# --------------------------------------------------------------------------- #
def make_citekey(rec: dict[str, Any]) -> str:
    """``<firstauthorfamily><year><firsttitleword>`` e.g. ``vaswani2017attention``."""
    authors = rec.get("authors") or []
    if authors and authors[0].get("family"):
        name = ascii_slug(authors[0]["family"])
    elif rec.get("authors_text"):
        name = ascii_slug(rec["authors_text"].split(",")[0].split(";")[0])
    else:
        name = "anon"
    year = str(rec.get("year") or "nd")
    word = ""
    for tok in re.findall(r"[A-Za-z]+", rec.get("title") or ""):
        if tok.lower() not in STOPWORDS:
            word = ascii_slug(tok)
            break
    return f"{name}{year}{word}" or "untitled"


def authors_text(authors: list[dict[str, Any]]) -> str:
    """Render structured authors as ``Family, Given; Family, Given``."""
    parts = []
    for a in authors:
        fam, giv = a.get("family", ""), a.get("given", "")
        parts.append(f"{fam}, {giv}".strip().rstrip(",") if fam else giv)
    return "; ".join(p for p in parts if p)


def short_authors(rec: dict[str, Any]) -> str:
    """A compact ``First et al.`` rendering for list views and filenames."""
    txt = rec.get("authors_text") or ""
    names = [p.split(",")[0].strip() for p in txt.split(";") if p.strip()]
    if not names:
        return "?"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{names[0]} et al."


# --------------------------------------------------------------------------- #
# record  <->  libkit metadata
# --------------------------------------------------------------------------- #
def record_to_metadata(rec: dict[str, Any]) -> dict[str, Any]:
    """Flatten a record into the single ``metadata`` mapping passed to
    ``Library.ingest(metadata=...)``.

    Includes the libkit top-level keys (``title``/``date``/``source_url``/
    ``content_type``) when present — libkit splits them into columns — and
    every other non-empty record key as free-form JSON. ``None``/empty values
    are dropped so they don't clobber existing data on a merge.
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
    # libkit's columns win for the keys it owns.
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
# BibTeX
# --------------------------------------------------------------------------- #
def to_bibtex(rec: dict[str, Any]) -> str:
    """Render a record as a single BibTeX entry."""
    fields: list[tuple[str, str]] = []
    authors = rec.get("authors") or []
    if authors:
        bib_authors = " and ".join(
            f"{a.get('family', '')}, {a.get('given', '')}".rstrip(", ") for a in authors
        )
        fields.append(("author", bib_authors))
    elif rec.get("authors_text"):
        fields.append(("author", rec["authors_text"].replace("; ", " and ")))
    for key, col in (("title", "title"), ("year", "year"), ("doi", "doi"), ("url", "source_url")):
        if rec.get(col):
            fields.append((key, str(rec[col])))
    venue = rec.get("venue")
    btype = rec.get("bibtex_type") or "article"
    if venue:
        fields.append(("journal" if btype == "article" else "booktitle", venue))
    for key in ("volume", "issue", "pages", "publisher"):
        if rec.get(key):
            fields.append(("number" if key == "issue" else key, str(rec[key])))
    if rec.get("arxiv_id"):
        fields.append(("eprint", str(rec["arxiv_id"])))
        fields.append(("archivePrefix", "arXiv"))
    citekey = rec.get("citekey") or "untitled"
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
    return f"@{btype}{{{citekey},\n{body}\n}}"


def stub_markdown(rec: dict[str, Any]) -> str:
    """A deterministic Markdown rendering of a citation-only record.

    Ingested into libkit when a paper has no file yet: the abstract becomes
    real searchable text and the document carries full metadata. Determinism
    (sorted fields, no timestamps) makes re-ingest idempotent — the same record
    yields the same bytes, hence the same ``document_id``.
    """
    lines = [f"# {rec.get('title') or '(untitled)'}", ""]
    if rec.get("authors_text"):
        lines += [f"**Authors:** {rec['authors_text']}", ""]
    facts = []
    for label, key in (
        ("Year", "year"), ("Venue", "venue"), ("DOI", "doi"),
        ("arXiv", "arxiv_id"), ("PMCID", "pmcid"), ("PMID", "pmid"),
        ("URL", "source_url"),
    ):
        if rec.get(key):
            facts.append(f"- **{label}:** {rec[key]}")
    if facts:
        lines += sorted(facts) + [""]
    if rec.get("abstract"):
        lines += ["## Abstract", "", rec["abstract"].strip(), ""]
    return "\n".join(lines) + "\n"
