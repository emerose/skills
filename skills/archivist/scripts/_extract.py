"""Best-effort extraction of experiment metadata + entities from README content
and filenames. Dependency-free (stdlib only) so it unit-tests without libkit.

The aim is *precision over recall* for the structured fields that drive experiment
cards and the (derivable) entity registry: CRO, external study IDs, assays, ASOs,
species/model, status, and related experiments. Everything is conservative —
controlled vocabularies with explicit aliases, plus a few tight regexes — because
these values feed cross-referencing (`entity show "ASO-154"`) where a false match
is worse than a miss. Free-text the extractor isn't sure about is left for the
human/agent author of a README, not invented here.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# controlled vocabularies (canonical -> alias regex fragments, case-insensitive)
# --------------------------------------------------------------------------- #
CRO_VOCAB: dict[str, list[str]] = {
    "Charles River": [r"charles river", r"\bCRL\b", r"\bCRDS\b"],
    "Attentive Science": [r"attentive"],
    "BioLegacy": [r"biolegacy"],
    "Dash Bio": [r"dash bio"],
    "UNC": [r"\bUNC\b", r"university of north carolina"],
    "iXCells": [r"ixcells"],
    "NeuCyte": [r"neucyte"],
    "bit.bio": [r"bit\.bio"],
    "Fios Genomics": [r"fios"],
    "Nitto Denko Avecia": [r"avecia", r"nitto denko"],
    "Synoligo": [r"synoligo"],
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

# Study-id shapes seen across CROs (kept tight to avoid grabbing random codes).
_STUDY_ID_PATTERNS = [
    r"\bC\d{7}\b",                 # CRL, e.g. C0790222
    r"\b\d{4}-\d{4}\b",            # Attentive, e.g. 1124-8851
    r"\b25[PW]-KSO-\d{3}\b",       # BioLegacy, e.g. 25P-KSO-001
    r"\bKey\s?\d{3,4}[A-Z]?\b",    # mouse studies, e.g. Key 2738 / 2830B
    r"\bSOW\d+\b",                 # statements of work
    r"\bCRP\s?Exp\d+\b",           # MEA CRP experiments
]
_ASO_RE = re.compile(r"\bASO[\s\-]?(\d{1,4})\b", re.IGNORECASE)
_EXP_ID_RE = re.compile(r"\bK1-[A-Za-z0-9]+\b")


def _match_vocab(text: str, vocab: dict[str, list[str]]) -> list[str]:
    found = []
    for canon, patterns in vocab.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            found.append(canon)
    return found


def find_asos(text: str) -> list[str]:
    """Normalise ASO mentions to ``ASO-<n>`` with leading zeros stripped, so
    'ASO 154', 'ASO-154', and 'ASO007' all canonicalise consistently."""
    return sorted({f"ASO-{int(m.group(1))}" for m in _ASO_RE.finditer(text)},
                  key=lambda s: int(s.split("-")[1]))


def find_study_ids(text: str) -> list[str]:
    ids: list[str] = []
    for pat in _STUDY_ID_PATTERNS:
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
_TABLE_ROW_RE = re.compile(r"^\|\s*\*{0,2}([^|*]+?)\*{0,2}\s*\|\s*(.+?)\s*\|\s*$", re.MULTILINE)


def parse_md_table_fields(text: str) -> dict[str, str]:
    """Pull ``| **Label** | value |`` rows from a Markdown table into a dict
    keyed by lowercased label. Skips separator rows and empty values."""
    out: dict[str, str] = {}
    for label, value in _TABLE_ROW_RE.findall(text):
        label = label.strip().lower()
        value = value.strip().strip("*").strip()
        if not label or not value or set(value) <= {"-", " ", ":"}:
            continue
        if label in ("field", "parameter", "value", "details"):
            continue
        out.setdefault(label, value)
    return out


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


def extract_from_readme(text: str, *, exp_id: str | None = None) -> dict[str, Any]:
    """Extract structured experiment metadata from a README's Markdown.

    Returns only the keys it can fill with reasonable confidence; the caller
    merges these over the folder-derived skeleton. Recognises both the table-
    style headers used in these READMEs ("External ID", "CRO", "Species/Strain",
    "Report Status") and free vocabulary in the prose.
    """
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

    # Own study IDs come ONLY from the IDs table — authoritative for THIS
    # experiment. We deliberately do NOT scan the prose: a planning/design doc
    # references other studies' ids, and those are not this experiment's.
    for key in ("external id", "external / cro study id", "cro study id", "study id"):
        if key in fields:
            out["cro_study_ids"] = find_study_ids(fields[key]) or [fields[key].strip()]
            break
    # Secondary (still authoritative for THIS experiment): a study-id-shaped token
    # in the title, e.g. "Rat IT PK/PD Screening Study (25P-KSO-001)".
    if "cro_study_ids" not in out and out.get("title"):
        tids = find_study_ids(out["title"])
        if tids:
            out["cro_study_ids"] = tids

    cro = fields.get("cro")
    if cro:
        canon = _match_vocab(cro, CRO_VOCAB)
        if canon:
            out["cro"] = canon[0]            # canonicalise a full name to the vocab
        elif not re.search(r"\bTBD\b|to be|bids|n/?a\b|none|\|", cro, re.IGNORECASE):
            out["cro"] = re.split(r"\(", cro)[0].strip()   # else a clean literal value
    else:
        cros = _match_vocab(text, CRO_VOCAB)
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
