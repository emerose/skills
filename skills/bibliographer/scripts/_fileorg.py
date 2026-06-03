"""File organizer for bibliographer: the human-readable author tree on disk.

libkit stores bytes, not files — it neither keeps the PDF nor decides where it
lives. bibliographer organizes the originals into a browsable tree so the
library folder is navigable in Finder without the tool:

    <home>/papers/<First Author Family, Given>/
        <Authors> (<Year>) - <Title>.<ext>

The citekey remains the stable handle in the catalog; the on-disk name is a
human-facing convenience and may change without breaking anything. Collisions
get the citekey appended, then a counter.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any

import _meta

# Characters illegal or troublesome in file/dir names across macOS/Windows.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TITLE_MAX = 120


def sanitize(text: str, maxlen: int = _TITLE_MAX) -> str:
    """Make a string safe and tidy for one path component."""
    text = unicodedata.normalize("NFC", text or "")
    text = _ILLEGAL.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(". ")  # no leading/trailing dots or spaces (Windows/macOS)
    if len(text) > maxlen:
        text = text[:maxlen].rsplit(" ", 1)[0].strip(". ")
    return text or "untitled"


def author_dir(rec: dict[str, Any]) -> str:
    """Folder name for a paper's first author, e.g. ``Vaswani, Ashish``."""
    authors = rec.get("authors") or []
    if authors and authors[0].get("family"):
        a = authors[0]
        name = f"{a['family']}, {a.get('given', '')}".strip().rstrip(",")
    elif rec.get("authors_text"):
        name = rec["authors_text"].split(";")[0].strip()
    else:
        name = "Unknown"
    return sanitize(name, maxlen=80)


def filename(rec: dict[str, Any], ext: str) -> str:
    """``<Authors> (<Year>) - <Title>.<ext>`` — human-readable, sanitized."""
    year = rec.get("year") or "n.d."
    title = sanitize(rec.get("title") or "untitled")
    stem = sanitize(f"{_meta.short_authors(rec)} ({year}) - {title}", maxlen=180)
    return stem + (ext if ext.startswith(".") else f".{ext}")


def plan_path(home: Path, rec: dict[str, Any], ext: str) -> Path:
    """Where this paper's file should live (collision-safe), without moving it."""
    folder = home / "papers" / author_dir(rec)
    base = filename(rec, ext)
    dest = folder / base
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    ck = rec.get("citekey")
    if ck:
        cand = folder / f"{stem} ({ck}){suffix}"
        if not cand.exists():
            return cand
    i = 2
    while (folder / f"{stem} ({i}){suffix}").exists():
        i += 1
    return folder / f"{stem} ({i}){suffix}"


def place(home: Path, rec: dict[str, Any], src: Path, *, move: bool) -> Path:
    """Copy (or move) ``src`` into the author tree; return the final path."""
    src = src.resolve()
    ext = src.suffix.lower() or ".pdf"
    dest = plan_path(home, rec, ext)
    if dest.resolve() == src:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))
    return dest
