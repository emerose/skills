# Advisory Committee materials (`reg adcomm`)

There is no JSON listing API. Materials are linked from HTML pages and served
through the generic media service (`/media/<id>/download`, ungated). Two page
levels matter:

- **Per-meeting page** — carries the actual document links (briefing docs,
  roster, agenda, transcript, presentations). Example:
  `…/advisory-committee-calendar/september-26-2024-meeting-oncologic-drugs-advisory-committee-meeting-announcement-09262024`
- **Per-committee year hub** — has *no* document links; it indexes the meeting
  pages. Example:
  `…/advisory-committees/oncologic-drugs-advisory-committee/2024-meeting-materials-oncologic-drugs-advisory-committee`

`reg adcomm sync` handles both: given a meeting page it extracts that page's
materials; given a year hub (no materials of its own) it **auto-recurses one
level** into each indexed meeting and aggregates — that can be 100+ materials, so
review before ingesting.

## Usage

```bash
reg adcomm sync "<meeting-or-hub-url>"                  # list only
reg adcomm sync "<meeting-url>" --add                   # download + ingest
reg adcomm sync "<url>" --committee "Oncologic Drugs AC" --abbr ODAC --date 2024-09-26
reg adcomm sync "<url>" --json
```

- Committee, abbreviation, and meeting date are inferred from the page/URL when
  not supplied (`guess_committee`, `guess_meeting_date`); pass them to override or
  when inference fails for a committee not in `COMMITTEE_ABBR`.
- Each material is classified from its anchor text (`classify_material`):
  `briefing` / `roster` / `transcript` / `agenda` / `presentation` / `minutes` /
  `questions` / `errata` / `material`.
- `--add` downloads each `/media/<id>/download` (dedup by `media_id`/`doc_url`),
  files under `docs/adcomm/<committee>/`, and ingests. Without `--add` it just
  lists (no store/embedder touched).

Verbose anchor titles like
`"06. September 26, 2024 Meeting of the Oncologic Drugs Advisory Committee- AM- FDA Briefing Document"`
are tidied to the distinctive tail (`AM- FDA Briefing Document`).

## When a sync returns 0 materials

Fetch the page and check: a 503 challenge (gated — try a browser), a different
link pattern (the page restructured — update `_MEDIA_RE` / `extract_materials`),
or it's a hub with only meeting links (should already auto-recurse; if the
meeting-link pattern changed, update `extract_meeting_links`). Capture the new
shape as a fixture in `tests/test_adcomm.py`.
