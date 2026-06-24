"""File organizer for regulator: the human-readable document tree on disk.

libkit stores bytes, not files — it neither keeps the PDF nor decides where it
lives. regulator organizes the downloaded originals into a browsable tree, keyed
by ``doc_type`` so the library folder is navigable in Finder without the tool:

    <home>/docs/guidance/<FDA org>/<Title> (<Year>).pdf
    <home>/docs/drugsfda/<APPNO> <Brand or Sponsor>/<appno> <sub> <reviewtype>.pdf
    <home>/docs/adcomm/<Committee>/<meeting-date> <material> - <Title>.pdf
    <home>/docs/personnel/<Name>.md

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

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TITLE_MAX = 120


def sanitize(text: str, maxlen: int = _TITLE_MAX) -> str:
    """Make a string safe and tidy for one path component."""
    text = unicodedata.normalize("NFC", text or "")
    text = _ILLEGAL.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(". ")
    if len(text) > maxlen:
        text = text[:maxlen].rsplit(" ", 1)[0].strip(". ")
    return text or "untitled"


def _year(rec: dict[str, Any]) -> str:
    for key in ("year", "approval_date", "issue_date", "meeting_date", "date"):
        v = rec.get(key)
        if v:
            m = re.search(r"(19|20)\d{2}", str(v))
            if m:
                return m.group(0)
    return "n.d."


def subtree(rec: dict[str, Any]) -> tuple[str, str]:
    """Return ``(<doc_type-folder>, <grouping-folder>)`` for a record."""
    dt = rec.get("doc_type") or "misc"
    if dt == "guidance":
        return "guidance", sanitize(rec.get("fda_org") or rec.get("center") or "FDA", maxlen=80)
    if dt == "drugsfda":
        appno = rec.get("application_number") or "app"
        who = rec.get("brand_name") or rec.get("sponsor_name") or ""
        return "drugsfda", sanitize(f"{appno} {who}".strip(), maxlen=80)
    if dt == "adcomm":
        return "adcomm", sanitize(rec.get("committee_abbr") or rec.get("committee") or "committee", maxlen=80)
    if dt == "personnel":
        return "personnel", ""
    return "misc", ""


def filename(rec: dict[str, Any], ext: str) -> str:
    """Human-readable, sanitized leaf filename, keyed by doc_type."""
    dt = rec.get("doc_type")
    if dt == "drugsfda":
        appno = rec.get("application_number") or "app"
        sub = rec.get("submission") or rec.get("submission_number") or ""
        rtype = rec.get("review_type") or rec.get("doc_subtype") or "doc"
        stem = sanitize(f"{appno} {sub} {rtype}".strip(), maxlen=180)
    elif dt == "adcomm":
        date = rec.get("meeting_date") or _year(rec)
        mat = rec.get("material_type") or "material"
        title = rec.get("title") or ""
        stem = sanitize(f"{date} {mat} - {title}".strip(" -"), maxlen=180)
    elif dt == "personnel":
        stem = sanitize(rec.get("name") or rec.get("title") or "person", maxlen=120)
    else:  # guidance / misc
        stem = sanitize(f"{rec.get('title') or 'untitled'} ({_year(rec)})", maxlen=180)
    return stem + (ext if ext.startswith(".") else f".{ext}")


def plan_path(home: Path, rec: dict[str, Any], ext: str) -> Path:
    """Where this document's file should live (collision-safe), without moving it."""
    top, group = subtree(rec)
    folder = home / "docs" / top
    if group:
        folder = folder / group
    dest = folder / filename(rec, ext)
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
    """Copy (or move) ``src`` into the document tree; return the final path."""
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
