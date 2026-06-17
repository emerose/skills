"""Controlled-vocabulary normalizers for experiment metadata. Dependency-free
(stdlib only, PyYAML lazy) so it unit-tests without libkit.

*Reading* a README and deciding what an experiment's CRO / assays / model / status
are is reading comprehension — the calling agent's job, done directly from the prose
(see references/search-index.md, "Author experiment.yml from the README"). This module
is the deterministic glue underneath that: it canonicalizes the tokens the agent
identifies so cross-referencing stays consistent (``entity show "ASO-7"`` must match
whether the README wrote "ASO 7", "ASO-7", or "ASO007"). It does NOT decide *which*
values apply — it normalizes ones already in hand:

* :func:`find_asos` — ASO mentions → canonical ``ASO-<n>``.
* :func:`find_study_ids` — study-id-shaped tokens (vendor-neutral defaults + private
  patterns), validated against the controlled shapes.
* :func:`find_related` — cross-referenced ``K1-…`` ids (self excluded).
* :func:`load_vocab` / :func:`match_vocab` — map a CRO/assay/model name onto its
  canonical vocabulary entry (alias → canonical), so a full vendor name folds to its
  short form.

Real CRO/vendor names and vendor-specific study-id formats are program-specific and are
**not** baked into this public repo. They live in a private vocabulary file in your data
folder (``vocab.yml``, or ``$SCIENTIST_VOCAB``); :func:`load_vocab` merges it over the
generic placeholder defaults below.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

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
    """Locate the private vocabulary file: ``$SCIENTIST_VOCAB`` if set, else
    ``vocab.{yml,yaml,json}`` in the data folder. None if there isn't one."""
    env = os.environ.get("SCIENTIST_VOCAB")
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
    ``$SCIENTIST_VOCAB``, never here. That file is merged OVER the defaults::

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


def match_vocab(text: str, vocab: dict[str, list[str]]) -> list[str]:
    """Every canonical vocabulary entry whose alias patterns match ``text``, in
    vocabulary order. Use to fold a name the agent read ("RT-qPCR", "Vendor A
    Discovery Services") onto its canonical form ("qPCR", "Vendor A")."""
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
