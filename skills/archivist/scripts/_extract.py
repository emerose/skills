"""Best-effort extraction of experiment metadata + entities from README content
and filenames. Dependency-free (stdlib only) so it unit-tests without libkit.

The aim is *precision over recall* for the structured fields that drive experiment
cards and the (derivable) entity registry: CRO, external study IDs, assays, ASOs,
species/model, status, and related experiments. Everything is conservative —
controlled vocabularies with explicit aliases, plus a few tight regexes — because
these values feed cross-referencing (`entity show "ASO-7"`) where a false match
is worse than a miss. Free-text the extractor isn't sure about is left for the
human/agent author of a README, not invented here.

Real CRO/vendor names and vendor-specific study-id formats are program-specific
and are **not** baked into this public repo. They live in a private vocabulary
file in your data folder (`vocab.yml`, or `$ARCHIVIST_VOCAB`); `load_vocab()`
merges it over the generic placeholder defaults below. See that loader.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# controlled vocabularies (canonical -> alias regex fragments, case-insensitive)
# --------------------------------------------------------------------------- #
# Public defaults are GENERIC placeholders only. Configure your real CROs in a
# private `vocab.yml` (see load_vocab) so vendor identities stay out of this repo.
CRO_VOCAB: dict[str, list[str]] = {
    "Vendor A": [r"vendor a", r"\bVA\b"],
    "Vendor B": [r"vendor b", r"\bVB\b"],
    "Vendor C": [r"vendor c"],
}

ASSAY_VOCAB: dict[str, list[str]] = {
    "QuantiGene": [r"quantigene", r"\bQG\b"],
    "Luminex": [r"luminex"],
    "qPCR": [r"\bqPCR\b", r"rt-?qpcr", r"\bRT-qPCR\b"],
    "MEA": [r"\bMEA\b", r"multi-?electrode array", r"microelectrode array"],
    "LC-MS/MS": [r"lc-?ms/?ms", r"\bLC-MS\b", r"mass spec"],
    "FOB/Irwin": [r"\bFOB\b", r"irwin", r"functional observational battery"],
    "Histopathology": [r"histopath"],
    "Hematology": [r"hematolog"],
    "Clinical chemistry": [r"clinical chemistr"],
    "Biodistribution": [r"biodistribution"],
    "Cytokine/Immunotox": [r"cytokine", r"immunotox", r"cytokine release"],
    "ELISA": [r"\bELISA\b"],
    "Jess/Simple Western": [r"\bJess\b", r"simple western"],
    "Transfection": [r"transfection", r"lipofectamine", r"endoporter", r"\bPEI\b"],
    "Body weight": [r"body weight"],
    "Clinical observations": [r"clinical observations"],
}

MODEL_VOCAB: dict[str, list[str]] = {
    "Sprague-Dawley rat": [r"sprague-?dawley"],
    "rat": [r"\brats?\b"],
    "mouse": [r"\bmouse\b", r"\bmice\b"],
    "NHP/cynomolgus": [r"\bNHP\b", r"cynomolgus", r"non-?human primate"],
    "iPSC neurons": [r"ipsc[- ]?neuron", r"\bneurons?\b"],
    "iPSC": [r"\biPSC\b"],
    "rat fibroblast": [r"rat fibroblast", r"fibroblast"],
    "PBMC": [r"\bPBMC\b"],
    "astrocyte": [r"astrocyte"],
    "organoid": [r"organoid"],
}

STATUS_HINTS: dict[str, list[str]] = {
    "terminated": [r"\(terminated\)", r"\bterminated\b"],
    "failed": [r"\(failed\)", r"\bfailed\b"],
    "draft": [r"\bDRAFT\b"],
}

# Generic study-id shapes (kept tight to avoid grabbing random codes). These are
# vendor-neutral; vendor-specific id formats are added from the private vocab file.
DEFAULT_STUDY_ID_PATTERNS = [
    r"\b[A-Z]\d{7}\b",            # letter + 7 digits, e.g. V1234567
    r"\b\d{4}-\d{4}\b",          # numeric study code, e.g. 1124-8851
    r"\bSOW\d+\b",               # statements of work
]
_ASO_RE = re.compile(r"\bASO[\s\-]?(\d{1,4})\b", re.IGNORECASE)
_EXP_ID_RE = re.compile(r"\bK1-[A-Za-z0-9]+\b")


# --------------------------------------------------------------------------- #
# private vocabulary (keeps real vendor names out of this public repo)
# --------------------------------------------------------------------------- #
def _vocab_path(home: str | Path | None) -> Path | None:
    """Locate the private vocabulary file: ``$ARCHIVIST_VOCAB`` if set, else
    ``vocab.{yml,yaml,json}`` in the data folder. None if there isn't one."""
    env = os.environ.get("ARCHIVIST_VOCAB")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    if home:
        for name in ("vocab.yml", "vocab.yaml", "vocab.json"):
            p = Path(home) / name
            if p.is_file():
                return p
    return None


def load_vocab(home: str | Path | None = None) -> tuple[dict[str, list[str]], list[str]]:
    """Return ``(cro_vocab, study_id_patterns)`` — the generic public defaults
    extended by a private config, if present.

    The public skills repo ships only generic placeholder vendors and vendor-neutral
    id shapes. Real CRO names and vendor-specific study-id formats are program-specific
    and live in a private file in the data folder (``vocab.yml``) or at
    ``$ARCHIVIST_VOCAB``, never here. That file is merged OVER the defaults::

        cros:
          "Real CRO Inc.": ["real cro", "\\\\bRCI\\\\b"]
        study_id_patterns:
          - "\\\\bRCI-\\\\d{6}\\\\b"
    """
    cro = dict(CRO_VOCAB)
    pats = list(DEFAULT_STUDY_ID_PATTERNS)
    path = _vocab_path(home)
    if not path:
        return cro, pats
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(raw)
    else:
        import yaml  # lazy: keeps this module importable without PyYAML
        data = yaml.safe_load(raw)
    if isinstance(data, dict):
        for canon, aliases in (data.get("cros") or {}).items():
            cro[canon] = list(aliases)
        for pat in (data.get("study_id_patterns") or []):
            if pat not in pats:
                pats.append(pat)
    return cro, pats


def _match_vocab(text: str, vocab: dict[str, list[str]]) -> list[str]:
    found = []
    for canon, patterns in vocab.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            found.append(canon)
    return found


def find_asos(text: str) -> list[str]:
    """Normalise ASO mentions to ``ASO-<n>`` with leading zeros stripped, so
    'ASO 7', 'ASO-7', and 'ASO007' all canonicalise consistently."""
    return sorted({f"ASO-{int(m.group(1))}" for m in _ASO_RE.finditer(text)},
                  key=lambda s: int(s.split("-")[1]))


def find_study_ids(text: str, patterns: list[str] | None = None) -> list[str]:
    ids: list[str] = []
    for pat in (patterns if patterns is not None else DEFAULT_STUDY_ID_PATTERNS):
        for m in re.finditer(pat, text, re.IGNORECASE):
            v = re.sub(r"\s+", " ", m.group(0)).strip()
            if v not in ids:
                ids.append(v)
    return ids


def find_related(text: str, *, exclude: str | None = None) -> list[str]:
    rel = sorted({m.group(0) for m in _EXP_ID_RE.finditer(text)})
    return [r for r in rel if r != exclude]


# --------------------------------------------------------------------------- #
# README parsing
# --------------------------------------------------------------------------- #
# Only genuine 2-column rows: the value cell must not itself contain a `|`. This
# deliberately ignores 3+-column tables (Related-Studies, Files-on-disk, etc.), whose
# rows would otherwise be mis-parsed as label/value pairs with pipe-laden values.
_TABLE_ROW_RE = re.compile(r"^\|\s*\*{0,2}([^|*]+?)\*{0,2}\s*\|\s*([^|]+?)\s*\|\s*$", re.MULTILINE)


def parse_md_table_fields(text: str) -> dict[str, str]:
    """Pull ``| **Label** | value |`` rows from a two-column Markdown table into a
    dict keyed by lowercased label. Skips separator rows, empty values, and any row
    that isn't a clean 2-column property row (so a wider table's headers/cells don't
    pollute the field map)."""
    out: dict[str, str] = {}
    for label, value in _TABLE_ROW_RE.findall(text):
        label = label.strip().lower()
        value = value.strip().strip("*").strip()
        if not label or not value or set(value) <= {"-", " ", ":"}:
            continue
        if label in ("field", "parameter", "property", "value", "details"):
            continue
        out.setdefault(label, value)
    return out


def _external_study_id(fields: dict[str, str]) -> str | None:
    """Pick the experiment's *external* study id from parsed table fields.

    Matches varied labels by meaning (external/CRO study id), never the internal
    id, and rejects values that are clearly parse garbage (containing ``|`` from a
    mis-read multi-column row, or newlines). Returns the highest-priority clean
    value, or ``None``."""
    best: tuple[int, str] | None = None
    for label, value in fields.items():
        if "internal" in label or "|" in value or "\n" in value:
            continue
        if re.search(r"external", label) and "id" in label:
            rank = 0
        elif label in ("cro study id", "cro id", "external / cro study id"):
            rank = 1
        elif label in ("study id", "external study id"):
            rank = 2
        else:
            continue
        if best is None or rank < best[0]:
            best = (rank, value.strip())
    return best[1] if best else None


def _section(text: str, *titles: str) -> str | None:
    """Return the body of the first matching ``## Title`` section, if present."""
    for title in titles:
        m = re.search(rf"^#{{1,4}}\s*{re.escape(title)}\s*$(.*?)(?=^#{{1,4}}\s|\Z)",
                      text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
        if m:
            body = m.group(1).strip()
            if body:
                return body
    return None


def _first_paragraph(body: str | None) -> str | None:
    if not body:
        return None
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if para and not para.startswith(("|", "#")):
            return re.sub(r"\s+", " ", para)
    return None


def extract_from_readme(
    text: str,
    *,
    exp_id: str | None = None,
    home: str | Path | None = None,
    cro_vocab: dict[str, list[str]] | None = None,
    study_id_patterns: list[str] | None = None,
) -> dict[str, Any]:
    """Extract structured experiment metadata from a README's Markdown.

    Returns only the keys it can fill with reasonable confidence; the caller
    merges these over the folder-derived skeleton. Recognises both the table-
    style headers used in these READMEs ("External ID", "CRO", "Species/Strain",
    "Report Status") and free vocabulary in the prose.

    CRO names and study-id formats come from ``load_vocab(home)`` (generic defaults
    plus any private ``vocab.yml``); pass ``cro_vocab``/``study_id_patterns`` to
    override directly (e.g. in tests).
    """
    if cro_vocab is None or study_id_patterns is None:
        loaded_cro, loaded_pats = load_vocab(home)
        if cro_vocab is None:
            cro_vocab = loaded_cro
        if study_id_patterns is None:
            study_id_patterns = loaded_pats

    fields = parse_md_table_fields(text)
    out: dict[str, Any] = {}

    # Title: explicit label, else the "# K1-xxx: Title" heading.
    title_m = re.search(r"\*\*Study Title:\*\*\s*(.+)", text)
    if title_m:
        out["title"] = title_m.group(1).strip()
    else:
        h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if h1:
            t = h1.group(1).strip()
            t = re.sub(r"^K1-[A-Za-z0-9]+\s*[:\-–—]\s*", "", t)  # drop id prefix
            out["title"] = t

    # Own study ID comes ONLY from the IDs table — authoritative for THIS
    # experiment. We deliberately do NOT scan the prose (a planning doc references
    # other studies' ids). Labels vary ("External Study ID", "Study ID (External)",
    # "External ID", "CRO Study ID"), so match by meaning; never the internal id.
    ext_val = _external_study_id(fields)
    if ext_val:
        out["cro_study_ids"] = find_study_ids(ext_val, study_id_patterns) or [ext_val]
    # Secondary (still authoritative for THIS experiment): a study-id-shaped token
    # in the title, e.g. "Rat IT PK/PD Screening Study (V1234567)".
    if "cro_study_ids" not in out and out.get("title"):
        tids = find_study_ids(out["title"], study_id_patterns)
        if tids:
            out["cro_study_ids"] = tids

    cro = fields.get("cro")
    if cro:
        canon = _match_vocab(cro, cro_vocab)
        if canon:
            out["cro"] = canon[0]            # canonicalise a full name to the vocab
        elif not re.search(r"\bTBD\b|to be|bids|n/?a\b|none|\|", cro, re.IGNORECASE):
            out["cro"] = re.split(r"\(", cro)[0].strip()   # else a clean literal value
    else:
        cros = _match_vocab(text, cro_vocab)
        if cros:
            out["cro"] = cros[0]

    model = fields.get("species/strain") or fields.get("species") or fields.get("model")
    out["model"] = model.strip() if model else None
    # Assays/ASOs from vocabulary are safe to read from prose (they describe what
    # the experiment did); cross-references are K1- ids, self excluded.
    out["assays"] = _match_vocab(text, ASSAY_VOCAB)
    out["asos"] = find_asos(text)
    out["related"] = find_related(text, exclude=exp_id)

    # Status ONLY from an explicit lifecycle field — never scraped from prose
    # ("failed to deliver" must not mark a study failed). Folder-name suffixes
    # like "(Terminated)"/"(Failed)"/DRAFT are handled by the caller.
    status_val = fields.get("status") or fields.get("study status")
    if status_val:
        for status, pats in STATUS_HINTS.items():
            if any(re.search(p, status_val, re.IGNORECASE) for p in pats):
                out["status"] = status
                break
        else:
            out["status"] = status_val.strip().lower()

    # Narrative sections (kept short; the README itself remains the full text).
    out["synopsis"] = _first_paragraph(
        _section(text, "Study Overview", "Synopsis", "Overview", "Summary"))
    kf = _section(text, "Key Findings", "Main Findings", "Key Conclusions")
    if kf:
        out["key_findings"] = re.sub(r"\n{3,}", "\n\n", kf).strip()[:2000]

    return {k: v for k, v in out.items() if v not in (None, "", [], {})}
