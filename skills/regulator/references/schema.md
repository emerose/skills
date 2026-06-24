# The document model

Every record carries a **`doc_type`** discriminator; the type-specific fields
hang off it. When stored, a record is flattened into one libkit `metadata` dict
(`record_to_metadata`); libkit promotes `title`/`date`/`source_url`/
`content_type` to columns and keeps the rest as free-form JSON. Reading a libkit
`Document` back is `document_to_record`.

## Fields common to all types

| Field | Meaning |
|---|---|
| `doc_type` | `guidance` \| `drugsfda` \| `adcomm` \| `personnel` |
| `title` | human title (libkit column) |
| `source_url` | canonical URL on fda.gov / accessdata (libkit column) |
| `citekey` | the stable handle (see below) |
| `content_state` | `full` (PDF ingested) or `stub` (known, not downloaded) |
| `tags` | list of free tags |
| `added_at` / `updated_at` | ISO timestamps |
| `file_path` | path under `<home>/docs/…` (full records only) |

## Per-type fields

**guidance** — `status` (Draft/Final), `issue_date`, `fda_org`/`center`, `topic`,
`docket_number`, `guidance_type`, `pdf_url`, `guidance_id` (natural key:
`media-<id>` or docket).

**drugsfda** — `application_number` (e.g. `NDA205834`), `application_kind`
(NDA/BLA), `sponsor_name`, `brand_name`, `generic_name`, `active_ingredient`,
`submission` (`s000`, `s017`), `submission_type`/`submission_number`,
`submission_class`, `review_type` (normalized code — see
[drugs-at-fda](drugs-at-fda.md)), `doc_subtype` (human label), `approval_date`,
`doc_url` (natural key — the accessdata PDF URL).

**adcomm** — `committee`, `committee_abbr` (ODAC, …), `meeting_date`,
`material_type` (briefing/roster/transcript/agenda/presentation/minutes/
questions/errata), `media_id`, `doc_url`/`page_url` (natural key: `media_id`).

**personnel** — `name`, `person_id` (slug, natural key), `role`, `division`,
`office`, `center`, `review_disciplines`, `signed_reviews` (list of
{date, application_number, review_type, brand_name, signed_by?}), `bio`,
`sources`. Stored as a Markdown document.

## Citekeys

Readable, type-specific (uniquified by a trailing letter on collision):

- guidance → `guidance-<year>-<title-slug>`
- drugsfda → `<APPNO>-<submission>-<reviewtype>` (`NDA205834-s000-medical`)
- adcomm → `<committee>-<date>-<material>` (`odac-2024-09-26-briefing`)
- personnel → `person-<name-slug>` (`person-edward-m-cox`)

## Dedup (document-level identity)

Layered over libkit's byte identity. `find_duplicate` checks the doc_type's
**natural key** first (`NATURAL_KEYS` in `meta.py`: e.g. `doc_url` for drugsfda,
`guidance_id` for guidance), then the citekey, then a normalized-title +
doc_type fallback. Re-running an ingest is therefore idempotent — the same PDF
URL won't be added twice.
