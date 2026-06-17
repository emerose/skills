# Auditing a library (periodic hygiene)

A library drifts: imports leave unverified records, enrichment can mis-match, files
can end up misfiled, and the occasional pile mislabel slips through. Run a hygiene
pass periodically (after any big import, and otherwise occasionally) to keep every
record **fully enriched, matching its actual file content, and correctly filed**.

There are two layers: a fast deterministic pass, and a semantic pass that only an
agent (reading the document) can do.

## 1. Deterministic pass — `bib audit`

```bash
bib audit            # human summary of flagged records
bib audit --json     # structured worklist (use this to drive fixes)
bib audit --fast     # skip the content-overlap check (no chunk reads)
```

Per-record flags:

| flag | meaning | typical fix |
|------|---------|-------------|
| `missing:title/authors_text/year` | thin metadata | `bib enrich <ck>` or `--doi` |
| `unverified` | metadata came from the file only (`source` = pdf/file) | `bib enrich <ck>` |
| `stub` | citation-only, no real file yet | attach a PDF (`add … --pdf`) |
| `no-identifier` | verified but no DOI/arXiv | often fine (pre-DOI papers); leave |
| `misfiled` | on-disk folder ≠ what current metadata implies | re-derive + re-file (re-run `enrich`, or fix metadata then re-file) |
| `file-missing` | `file_path` points nowhere | often transient cloud-sync; re-run. Else re-add |
| `low-content-overlap:N` | title words barely appear in the doc's leading text | **soft** — verify by reading (see §2) |

**`low-content-overlap` is a heuristic, not a verdict — and it fails both ways.**
It *false-positives* when a PDF's first pages are boilerplate (ethics statements,
cover sheets) rather than the title/abstract. It also *false-negatives*: if a paper
**cites** the title it's mislabeled as, those words sit in its reference list and
the overlap looks fine even though the document is a different paper. So a low
score means "an agent should read this," and a *normal* score is **not** proof of
correctness. Only the semantic pass (§2) is authoritative.

**Watch for shared journal pages.** In print-layout PDFs the first page often
carries the **tail of the *preceding* article** (its ethics statement, references)
above the actual paper's title — so a document's leading text, and even its parsed
"first chunk," can belong to a *different* paper, and stray topic words (a real
case: a nusinersen paper whose page 1 ended a myasthenia article — 19 "myasthenia"
mentions) can look alarming. When verifying, find the **title + author block**
(which may be partway down page 1) and judge from the article body, not the top of
the first page.

Expected residue (don't chase): non-article documents tagged `type:genereview` /
`type:statpearls` / `type:techdoc` / `type:supplement` will always show
`unverified`/`missing:*`, and genuinely pre-DOI papers show `no-identifier`.

## 2. Semantic pass — parallel agents

Confirming that a document's **content** matches **all** of its stored metadata
(right paper, authors, year, venue) is a reading task, not a string match. At scale
this parallelizes cleanly — fan out agents, each owning a slice.

Procedure:

1. **Partition.** `bib list --json` (or `bib audit --json` to target only flagged
   records) → split the citekeys into N batches.
2. **Fan out** N agents (Agent tool / a Workflow). Give each batch its citekeys and
   this instruction: for each citekey, read a content excerpt
   (`bib show <ck> --json` for the metadata; `bib query "<title>" --json` or the
   document's first chunks for the content) and judge whether the stored title,
   first author, and year actually match the document. Return a structured list of
   discrepancies only — `{citekey, problem, suggested_fix}` — not prose.
3. **Adversarial check (optional, for high stakes).** Have a second agent re-verify
   each *claimed* discrepancy before acting, so one agent's misread doesn't cause a
   bad edit.
4. **Apply fixes** with the tool, never by hand: `bib enrich <ck> --doi <id>` for a
   corrected identifier, `bib tag` for type tags, `bib rm` for true duplicates.
   Re-running `enrich` re-files as a side effect.
5. **Re-audit** (`bib audit`) to confirm the flag set shrank and nothing regressed.

Keep slices modest (cost scales with reads) and always `log`/report what was
skipped — a silent cap reads as "all clean" when it isn't.

## Why this exists

The deterministic checks catch structure; only a reader catches a file whose
*contents* are a different paper than its name/metadata claim. Two real cases this
audit found:

- a file named `…punt_2022…` that actually contained the Milazzo 2021 paper; and
- a record resolved (during import) to a paper it merely **cited** — the importer
  picked a DOI out of the file's reference list, so the metadata described one
  paper while the PDF was another. A deterministic title-overlap check *passed* it
  (the cited title's words were in the references); a parallel agent reading the
  document caught it immediately.

`import` was since hardened to distrust reference-list DOIs, but the general
lesson stands: structural checks and string overlap can be fooled; periodic
semantic auditing is the backstop that actually reads the papers.
