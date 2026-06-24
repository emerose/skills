# FDA Guidance documents (`reg guidance`)

The entire guidance corpus (~2,800 documents) is one JSON file that the
DataTables grid on the search page loads client-side:

- corpus: `https://www.fda.gov/files/api/datatables/static/search-for-guidance.json`
- page: `https://www.fda.gov/regulatory-information/search-fda-guidance-documents`

## Endpoints (which one, and the bot wall)

`reg guidance sync` uses the **static** corpus file above, which is **not**
bot-gated — it pulls all ~2,800 records directly, no browser needed. (The legacy
`/datatables-json/search-for-guidance.json` AJAX path *is* Akamai-gated → HTTP
503; don't use it.) The corpus is a **bare JSON list of FDA Drupal-field objects**
(`title`, `field_associated_media_2`, `field_issue_datetime`, …) — `parse_rows`
maps those field names, splitting the `title` cell into title + landing page and
`field_associated_media_2` into the `/media/<id>` PDF link.

If a future FDA change breaks the static path, the escape hatch still works:
open the corpus URL in a real browser, save the JSON, and run `reg guidance sync
--from-file <saved.json>`. The per-document PDFs (`/media/<id>/download`) are
ungated regardless.

## Sync → search → add

```bash
reg guidance sync                       # fetch + cache the whole corpus (or --from-file)
reg guidance search "rare disease natural history"
reg guidance search "accelerated approval" --json
reg guidance add "rare disease natural history"        # ingest the single match
reg guidance add "expedited programs" --index 2        # disambiguate by index
reg guidance add "antisense oligonucleotide" --all     # ingest ALL matches (bulk)
reg guidance add https://www.fda.gov/media/85393/download --title "Expedited Programs"
```

- `sync` parses the feed into records and caches them at
  `<home>/guidance_index.json` (so search/add work offline afterward). The parser
  (`parse_rows`) handles both keyed-object and positional-array DataTables rows
  and splits the HTML `Document` cell into `title` + `source_url` (+ `pdf_url`
  when it's a `/media/<id>/download`).
- `search` is a simple AND-of-terms match over title/topic/org/docket of the
  cached corpus; it prints an index you can feed back to `add`.
- `add` resolves the target (a corpus match by string/`--index`, or a direct
  URL), downloads the PDF (or ingests a stub if no PDF URL is discoverable), and
  files it under `docs/guidance/<FDA org>/`.

## Fields captured

`title`, `status` (Draft/Final), `issue_date`, `fda_org`/`center`, `topic`,
`docket_number`, `guidance_type`, `pdf_url`. See [schema](schema.md).

## When FDA changes the feed

If `parse_rows` starts producing empty titles or wrong columns, the DataTables
column order changed. Capture a sample row as a fixture in
`tests/test_guidance.py` and adjust `DEFAULT_COLUMNS` / the keyed-object mapping.
