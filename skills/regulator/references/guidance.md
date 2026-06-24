# FDA Guidance documents (`reg guidance`)

The entire guidance corpus (~4,000 documents) is one JSON feed backing the
DataTables grid on the guidance search page:

- feed: `https://www.fda.gov/datatables-json/search-for-guidance.json`
- page: `https://www.fda.gov/regulatory-information/search-fda-guidance-documents`

## The bot wall (important)

That feed sits behind **Akamai bot protection** — a request from a flagged
egress gets an HTTP 503 challenge. `reg guidance sync` detects this and exits
with an escape-hatch message rather than caching a challenge page. Two ways
through:

1. **`--from-file`** — open the feed URL once in a real browser (or pull it from
   an un-flagged IP), save the JSON, and run
   `reg guidance sync --from-file <saved.json>`.
2. Run `reg guidance sync` from a network/egress Akamai doesn't challenge.

The **per-document PDFs** (`https://www.fda.gov/media/<id>/download`) are *not*
gated, so once the corpus is cached, `guidance add` downloads work normally.

## Sync → search → add

```bash
reg guidance sync                       # fetch + cache the whole corpus (or --from-file)
reg guidance search "rare disease natural history"
reg guidance search "accelerated approval" --json
reg guidance add "rare disease natural history"        # ingest the single match
reg guidance add "expedited programs" --index 2        # disambiguate by index
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
