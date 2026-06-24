# Drugs@FDA (`reg drugsfda`)

The cleanest FDA source, and the one to start from for approval precedent.

## How it works

openFDA's `drug/drugsfda` endpoint returns, for each application, a
`submissions[].application_docs[]` array that already lists **every approval-
package PDF** on accessdata.fda.gov — typed and dated. So the corpus is
API-enumerable end-to-end with no HTML scraping. The PDFs live on
`accessdata.fda.gov`, which is not bot-gated.

- metadata + doc URLs: `https://api.fda.gov/drug/drugsfda.json`
- the PDFs: `https://www.accessdata.fda.gov/drugsatfda_docs/...`

Limits: 1,000 requests/day with no key, 120,000 with `OPENFDA_API_KEY`. Set
`REGULATOR_MAILTO` to be polite.

## Search

```bash
reg drugsfda search sofosbuvir --field ingredient   # active ingredient
reg drugsfda search "vertex pharmaceuticals" --field sponsor
reg drugsfda search HARVONI --field brand
reg drugsfda search NDA205834 --field appno
reg drugsfda search '<raw openFDA expression>'      # no --field: passthrough
```

Each result is one application: `application_number`, `sponsor_name`,
`brand_names`, `active_ingredients`, `first_approval`/`latest_approval`. Add
`--json` to parse.

## Add (download + ingest the approval package)

```bash
reg drugsfda add NDA205834 --dry-run                       # list the docs first
reg drugsfda add NDA205834 --submission s000 --type medical clinpharm summary letter
```

- `--dry-run` enumerates `application_docs` (one openFDA call, no store/embedder)
  so you can pick before downloading.
- `--submission` limits to submission tags (`s000` = original approval; `s017` =
  the 17th supplement). Omit to take all submissions.
- `--type` limits to normalized review types (below). Omit to take all.
- Each PDF is downloaded, filed under
  `docs/drugsfda/<APPNO> <Brand>/`, and ingested (dedup by `doc_url`, so re-runs
  are idempotent). Failures are reported per-doc and don't abort the batch.

**Be selective.** Applications can have dozens of PDFs and review packages are
large/scanned — ingesting "everything" is slow and costs Datalab + embeddings.
For "how was X approved," the original `s000` Summary Review + Medical Review +
approval letter usually answer it.

## Review-type taxonomy

`review_type` is normalized from the accessdata filename stem (precise), falling
back to openFDA's `type`. The map (`_REVIEW_STEMS` in `sources/drugsfda.py`):

| code | document |
|---|---|
| `medical` | Medical Review (`…MedR.pdf`) |
| `clinpharm` | Clinical Pharmacology / Biopharmaceutics Review |
| `statistical` | Statistical Review |
| `chemistry` | Chemistry (CMC) Review |
| `pharmtox` | Pharmacology/Toxicology Review |
| `microbiology` | Microbiology Review |
| `multidiscipline` | Integrated/Multidisciplinary Review (modern combined review) |
| `summary` | Summary Review |
| `risk` | Risk Assessment / REMS |
| `letter` | Approval Letter |
| `label` | Label / Printed Labeling |
| `admin` | Administrative & Correspondence |

Newer approvals replace the separate MedR/ClinPharmR/StatR with one
`MultidisciplineR` (`multidiscipline`); both generations are handled.

## Then ask questions

```bash
reg query "what was the basis for accelerated approval" --limit 5
reg query "recommended phase 3 dose and exposure-response"
reg text NDA205834-s000-medical | head -50     # confirm OCR landed
```
