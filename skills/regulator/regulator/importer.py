"""Index an existing folder of regulatory documents *in place*.

Unlike the source ingesters (which fetch from FDA into the managed ``docs/``
tree), ``reg import`` walks a directory of files the user already curated — a
regulatory archive — and ingests each into the libkit store **without moving
it**, so the human-organised folder structure is preserved and only the
searchable index is added alongside.

Classification is best-effort from the filename + path:

* Files whose names match the accessdata Drugs@FDA convention
  (``206488Orig1s000MedR.pdf``) become ``drugsfda`` records with the application
  number, submission, and review type parsed out.
* Everything else becomes an ``other`` record titled from the filename, tagged
  with its program folder.

Pure stdlib (+ the drugsfda filename classifier); unit-tests offline.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .sources import drugsfda

INGESTIBLE_EXTS = {".pdf", ".md", ".markdown", ".docx", ".doc", ".pptx", ".ppt", ".txt"}

# accessdata Drugs@FDA filename: 6-digit appno, OrigN, submission sNNN, type stem.
# e.g. 206488Orig1s000MedR.pdf ; 205834Orig1s017lbl.pdf ; 761178Orig1s000ltr.pdf
_ACCESSDATA_RE = re.compile(r"(\d{6})Orig\d+s(\d{3})", re.I)


def _brand_and_drug(program: str | None) -> tuple[str | None, str | None]:
    """From a program folder like ``03_Eteplirsen_Exondys51`` → (drug, brand)."""
    if not program:
        return None, None
    bits = [b for b in re.split(r"[_\s]+", program) if b and not b.isdigit()]
    if not bits:
        return None, None
    drug = bits[0]
    brand = bits[1] if len(bits) > 1 else None
    return drug, brand


def classify_path(path: Path, home: Path) -> dict[str, Any]:
    """Build a regulator record for one existing file, classified by name/path."""
    try:
        rel = path.resolve().relative_to(home.resolve())
    except ValueError:
        rel = Path(path.name)
    parts = rel.parts
    program = parts[-2] if len(parts) >= 2 else None
    title = path.stem
    tags = ["imported"]
    if program:
        tags.append(program)

    rec: dict[str, Any] = {
        "title": title,
        "file_path": str(rel),
        "imported": True,
        "tags": tags,
    }
    if program:
        rec["program"] = program

    m = _ACCESSDATA_RE.search(path.name)
    if m and path.suffix.lower() == ".pdf":
        review_type, label = drugsfda.classify_doc(path.name)
        drug, brand = _brand_and_drug(program)
        rec.update({
            "doc_type": "drugsfda",
            "application_number": m.group(1),  # bare digits; NDA/BLA prefix unknown from filename
            "submission": "s" + m.group(2),
            "review_type": review_type,
            "doc_subtype": label,
        })
        if drug:
            rec["active_ingredient"] = drug
        if brand:
            rec["brand_name"] = brand
        rec["tags"] = tags + [t for t in ("drugsfda", review_type) if t]
    else:
        rec["doc_type"] = "other"
    return rec


def walk(root: Path, *, skip_dirs: tuple[str, ...] = (".download", ".stubs", "docs")) -> list[Path]:
    """List ingestible files under ``root``, skipping dotfiles and store dirs.

    ``docs/`` (the skill's managed fetch tree) is skipped by default — those files
    are indexed when fetched; pass ``skip_dirs=()`` to re-walk everything.
    """
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in INGESTIBLE_EXTS:
            continue
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if rel_parts and rel_parts[0] in skip_dirs:
            continue
        out.append(p)
    return out
