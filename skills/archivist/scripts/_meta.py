"""Pure model helpers for archivist: the experiment / file / entity record model,
deterministic Markdown *card* generators, dependency blocks, and the mapping
between an archivist *record* and a libkit document's ``metadata`` dict.

Dependency-free (stdlib only) so it imports and unit-tests without libkit or a
network.

## The record model

archivist manages a tree of scientific *experiments* (e.g. ``05 - Scientific
Data``). The logical unit is an **experiment** — a folder of heterogeneous files
— not a single document. Everything is stored in one libkit library as documents
of three *kinds*, distinguished by the ``kind`` metadata key:

* ``kind="experiment"`` — a generated Markdown **experiment card** summarising the
  folder (IDs, CRO, design, assays, ASOs, status, links). Embedded, so an
  experiment is searchable as a unit. Keyed by ``exp_id`` (e.g. ``K1-230901``).
* ``kind="file"`` — one per real file in an experiment. *Narrative* files
  (README/protocol/report/analysis) are ingested directly so their text is
  embedded; *tabular* files (csv/xlsx/pzfx) are represented by a generated
  schema/preview card; *binary* files (instrument output, genomics) by a
  metadata-only descriptor card. Either way the metadata records the real file's
  ``path`` and ``sha256`` so an agent can open it to pull exact numbers.
* ``kind="entity"`` — only for *curated* notes about an ASO/CRO/assay/model that
  a query can't reconstruct. Purely-derivable entity facts are NOT stored here;
  they come from a live filter query over experiment records (see the skill doc).

libkit owns byte-level identity (``document_id`` = SHA-256 of the ingested bytes).
archivist layers logical identity on top: an experiment is keyed by ``exp_id``, a
file by its ``path``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# libkit promotes these to real columns; everything else in the dict passed to
# ``ingest(metadata=...)`` is free-form JSON. Never reuse these names for our data.
LIBKIT_TOP_LEVEL = frozenset({"title", "date", "source_url", "content_type"})

KINDS = ("experiment", "file", "entity")

# How a file is represented in libkit's index.
INDEXED_CONTENT = "content"      # the real file was ingested + embedded
INDEXED_SCHEMA = "schema"        # a generated schema/preview card was ingested
INDEXED_DESCRIPTOR = "descriptor"  # a metadata-only descriptor card was ingested

# File classification by extension (lowercase, with dot).
NARRATIVE_EXTS = {".md", ".markdown", ".txt", ".rtf",
                  ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".odt"}
TABULAR_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm", ".pzfx", ".numbers"}
# Everything else (.eds, .spk, .cram, .vcf, .sav, .dta, .sas7bdat, images, zips,
# Google-Drive stubs, …) is catalogued as a descriptor only.

# Map an experiment subfolder (per the folder's LAYOUT.md) to a file role.
ROLE_BY_SUBDIR = {
    "raw": "raw",
    "data": "data",
    "protocol": "protocol",
    "reports": "report",
    "analysis": "analysis",
}

STOPWORDS = {"the", "a", "an", "of", "on", "in", "and", "or", "for", "to",
             "with", "via", "using", "from", "by", "is", "are", "at", "as"}


# --------------------------------------------------------------------------- #
# slugs / ids
# --------------------------------------------------------------------------- #
def ascii_slug(text: str) -> str:
    """Lowercase ASCII fold, alphanumerics only."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def entity_slug(text: str) -> str:
    """Slug that keeps word boundaries as single hyphens (for entity ids)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# Experiment folders are named ``K1-YYMMXX - Short Name``. The id prefix is the
# stable handle; the rest is a human-facing name. Accept a couple of separators.
_EXP_DIR_RE = re.compile(r"^\s*(?P<id>K1-[A-Za-z0-9]+)\s*[-–—:]\s*(?P<name>.+?)\s*$")
# A CRO study id often trails in parentheses, e.g. "… (C0790222)".
_CRO_ID_RE = re.compile(r"\(([^)]+)\)\s*$")


def parse_experiment_dirname(dirname: str) -> dict[str, Any] | None:
    """Parse ``K1-230901 - Rat IT Dose-Response (C0790222)`` into parts.

    Returns ``{exp_id, name, cro_study_id_guess}`` or ``None`` if the directory
    name doesn't match the experiment convention.
    """
    m = _EXP_DIR_RE.match(dirname)
    if not m:
        return None
    name = m.group("name").strip()
    cro_guess = None
    cm = _CRO_ID_RE.search(name)
    if cm:
        cand = cm.group(1).strip()
        # Heuristic: study ids look like codes (have a digit, no spaces-only words).
        if any(ch.isdigit() for ch in cand) and len(cand) <= 24:
            cro_guess = cand
    return {"exp_id": m.group("id"), "name": name, "cro_study_id_guess": cro_guess}


def classify_ext(suffix: str) -> str:
    """Return ``narrative`` | ``tabular`` | ``binary`` for a file extension."""
    s = suffix.lower()
    if s in NARRATIVE_EXTS:
        return "narrative"
    if s in TABULAR_EXTS:
        return "tabular"
    return "binary"


def role_for_path_parts(parts: tuple[str, ...], filename: str) -> str:
    """Infer a file's role from its location within an experiment folder.

    ``parts`` are the path components *below* the experiment root. A top-level
    ``README.md`` is a ``readme``; otherwise the first component that names a
    standard subfolder decides; unknown locations fall back to ``other``.
    """
    if filename.lower() == "readme.md" and not parts:
        return "readme"
    for p in parts:
        role = ROLE_BY_SUBDIR.get(p.lower())
        if role:
            return role
    return "other"


# --------------------------------------------------------------------------- #
# record  <->  libkit metadata
# --------------------------------------------------------------------------- #
def record_to_metadata(rec: dict[str, Any]) -> dict[str, Any]:
    """Flatten a record into the ``metadata`` mapping for ``ingest(metadata=…)``.

    Drops ``None``/empty values so a merge never clobbers existing data, and
    strips runtime-only fields (prefixed ``_`` or libkit-derived) that must not
    be written back.
    """
    meta: dict[str, Any] = {}
    for key, value in rec.items():
        if key.startswith("_") or key in ("document_id", "content_hash"):
            continue
        if value is None or value == "" or value == [] or value == {}:
            continue
        meta[key] = value
    return meta


def document_to_record(doc: Any) -> dict[str, Any]:
    """Build a record dict from a libkit ``Document``.

    The free-form ``metadata`` JSON carries everything; libkit's authoritative
    top-level columns and the immutable byte identity are overlaid.
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
# deterministic Markdown cards (ingested into libkit)
# --------------------------------------------------------------------------- #
def _facts_block(pairs: list[tuple[str, Any]]) -> list[str]:
    return [f"- **{label}:** {value}" for label, value in pairs if value]


def experiment_card_markdown(rec: dict[str, Any]) -> str:
    """Deterministic Markdown for an experiment card (``kind=experiment``).

    This is the text libkit embeds for the experiment-as-a-unit. Determinism
    (sorted lists, no timestamps in the body) keeps re-ingest stable: the same
    metadata yields the same bytes, hence the same ``document_id``.
    """
    title = rec.get("title") or rec.get("name") or rec.get("exp_id") or "(experiment)"
    lines = [f"# {rec.get('exp_id', '')}: {title}".strip(), ""]
    facts = _facts_block([
        ("Internal ID", rec.get("exp_id")),
        ("External / CRO study ID", ", ".join(rec.get("cro_study_ids") or [])),
        ("CRO", rec.get("cro")),
        ("Status", rec.get("status")),
        ("Species / model", rec.get("model") or rec.get("species")),
        ("Assays", ", ".join(sorted(rec.get("assays") or []))),
        ("ASOs", ", ".join(sorted(rec.get("asos") or []))),
        ("Dates", rec.get("dates")),
    ])
    if facts:
        lines += facts + [""]
    if rec.get("synopsis"):
        lines += ["## Synopsis", "", rec["synopsis"].strip(), ""]
    if rec.get("key_findings"):
        lines += ["## Key findings", "", rec["key_findings"].strip(), ""]
    related = sorted(rec.get("related") or [])
    if related:
        lines += ["## Related experiments", ""] + [f"- {r}" for r in related] + [""]
    return "\n".join(lines).rstrip() + "\n"


def _fmt_bytes(n: Any) -> str:
    try:
        size = float(n)
    except (TypeError, ValueError):
        return str(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def file_card_markdown(rec: dict[str, Any], *, schema: dict[str, Any] | None = None,
                       preview: str | None = None) -> str:
    """Deterministic Markdown for a tabular schema/preview or binary descriptor
    card (``kind=file``, ``indexed_as`` schema|descriptor).

    For tabular files ``schema`` carries ``{columns, n_rows, ...}`` and
    ``preview`` a small text sample; for binary files both are omitted and the
    card is a pure descriptor. The metadata still records the real file's path +
    sha256 so the file can be opened directly to read exact values.
    """
    name = rec.get("filename") or (rec.get("path") or "").rsplit("/", 1)[-1] or "(file)"
    lines = [f"# {name}", ""]
    facts = _facts_block([
        ("Experiment", rec.get("exp_id")),
        ("Role", rec.get("role")),
        ("Type", rec.get("file_type")),
        ("Path", rec.get("path")),
        ("Size", _fmt_bytes(rec.get("size")) if rec.get("size") is not None else None),
    ])
    if facts:
        lines += facts + [""]
    if schema and schema.get("columns"):
        lines += ["## Columns", ""]
        for col in schema["columns"]:
            if isinstance(col, dict):
                bits = [f"`{col.get('name', '?')}`"]
                if col.get("dtype"):
                    bits.append(f"({col['dtype']})")
                if col.get("unit"):
                    bits.append(f"[{col['unit']}]")
                lines.append("- " + " ".join(bits))
            else:
                lines.append(f"- `{col}`")
        lines.append("")
        if schema.get("n_rows") is not None:
            lines += [f"_{schema['n_rows']} rows × {len(schema['columns'])} columns_", ""]
    if preview:
        lines += ["## Sample", "", "```", preview.rstrip(), "```", ""]
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# dependency blocks (explicit provenance for generated docs)
# --------------------------------------------------------------------------- #
_DEPS_BEGIN = "<!-- archivist:deps"
_DEPS_END = "-->"


def render_deps_block(deps: list[dict[str, str]]) -> str:
    """Render an explicit dependency block to embed in a generated README/summary.

    ``deps`` is a list of ``{"path": ..., "sha256": ...}`` captured at generation
    time. The block is a single HTML comment so it's invisible in rendered
    Markdown but trivially machine-parseable by ``audit`` to detect staleness.
    """
    import json

    ordered = sorted(({"path": d["path"], "sha256": d.get("sha256", "")} for d in deps),
                     key=lambda d: d["path"])
    payload = json.dumps(ordered, ensure_ascii=False, sort_keys=True)
    return f"{_DEPS_BEGIN} {payload} {_DEPS_END}"


def readme_template(rec: dict[str, Any]) -> str:
    """A README.md skeleton for a newly scaffolded experiment, following the
    convention these folders use (study IDs, synopsis, findings, files, related).
    Interpretive sections are left as prompts for the author/agent to fill."""
    exp_id = rec.get("exp_id", "")
    name = rec.get("name") or rec.get("title") or ""
    lines = [
        f"# {exp_id}: {name}".strip(),
        "",
        "## Study IDs & Dates",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Internal ID** | {exp_id} |",
        f"| **External ID** | {', '.join(rec.get('cro_study_ids') or []) or 'TBD'} |",
        f"| **CRO** | {rec.get('cro') or 'TBD'} |",
        f"| **Species / model** | {rec.get('model') or 'TBD'} |",
        f"| **Status** | {rec.get('status') or 'active'} |",
        "",
        "## Synopsis",
        "",
        "_One paragraph: goal, design, and what this experiment was for._",
        "",
        "## Key findings",
        "",
        "_The main results and any caveats. Be specific; preserve hard-won caveats._",
        "",
        "## Related experiments",
        "",
        "_Predecessors, follow-ons, repeats (by K1- id)._",
        "",
    ]
    return "\n".join(lines)


def catalog_markdown(experiments: list[dict[str, Any]]) -> str:
    """A deterministic Markdown index of all experiments (the human-readable half
    of the catalog export). Sorted by exp_id; no timestamps, so re-export is a
    no-op diff unless the data changed."""
    rows = ["# Experiment catalog", "",
            f"_{len(experiments)} experiments._", "",
            "| ID | Name | CRO | Study IDs | Assays | ASOs | Status |",
            "|----|------|-----|-----------|--------|------|--------|"]
    for e in sorted(experiments, key=lambda r: r.get("exp_id") or ""):
        rows.append("| {id} | {name} | {cro} | {ids} | {assays} | {asos} | {status} |".format(
            id=e.get("exp_id", ""),
            name=(e.get("name") or e.get("title") or "").replace("|", "/"),
            cro=e.get("cro") or "",
            ids=", ".join(e.get("cro_study_ids") or []),
            assays=", ".join(sorted(e.get("assays") or [])),
            asos=", ".join(sorted(e.get("asos") or [])),
            status=e.get("status") or "",
        ))
    return "\n".join(rows) + "\n"


def parse_deps_block(text: str) -> list[dict[str, str]] | None:
    """Extract the dependency list from a generated doc, or ``None`` if absent."""
    import json

    start = text.find(_DEPS_BEGIN)
    if start == -1:
        return None
    end = text.find(_DEPS_END, start)
    if end == -1:
        return None
    payload = text[start + len(_DEPS_BEGIN):end].strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def set_deps_block(text: str, deps: list[dict[str, str]]) -> str:
    """Replace the doc's dependency block (or append one) with ``deps``."""
    block = render_deps_block(deps)
    start = text.find(_DEPS_BEGIN)
    if start != -1:
        end = text.find(_DEPS_END, start)
        if end != -1:
            return text[:start] + block + text[end + len(_DEPS_END):]
    return text.rstrip() + "\n\n" + block + "\n"


# --------------------------------------------------------------------------- #
# managed blocks: archivist-owned regions inside an otherwise human-authored doc
# --------------------------------------------------------------------------- #
def _managed_markers(name: str) -> tuple[str, str]:
    return f"<!-- archivist:begin:{name} -->", f"<!-- archivist:end:{name} -->"


def set_managed_block(text: str, name: str, body: str) -> str:
    """Insert or replace the named archivist-managed region in ``text``.

    The region is delimited by ``<!-- archivist:begin:NAME -->`` / ``end`` comments;
    everything outside every managed region is human/agent-authored narrative and is
    preserved verbatim. A new region is appended at the end of the document.
    """
    begin, end = _managed_markers(name)
    block = f"{begin}\n{body.rstrip()}\n{end}"
    si = text.find(begin)
    if si != -1:
        ei = text.find(end, si)
        if ei != -1:
            return text[:si] + block + text[ei + len(end):]
    return text.rstrip() + "\n\n" + block + "\n"


def get_managed_block(text: str, name: str) -> str | None:
    """Return the body of the named managed region, or ``None`` if absent."""
    begin, end = _managed_markers(name)
    si = text.find(begin)
    if si == -1:
        return None
    ei = text.find(end, si)
    if ei == -1:
        return None
    return text[si + len(begin):ei].strip("\n")
