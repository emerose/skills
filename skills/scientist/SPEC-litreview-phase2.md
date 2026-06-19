# SPEC — Phase 2: the paper-claims layer (storage, extraction, retrieval)

Status: **proposed** (design contract, pre-implementation). Detail for **Phase 2** of
[SPEC-litreview-redesign.md](SPEC-litreview-redesign.md) §3. Phase 1 (Change A — PRISMA protocol +
`must_confront` removal) is independent and proceeds separately. Phase 3 (the review-node tree) builds
on this layer.

## 0. What Phase 2 delivers

The **reuse substrate** the tree (Phase 3) needs: a paper's claim set extracted **once** and reused
across every review that touches the paper, instead of authored lazily per citation. An external
`[lit:]` citation resolves to a **pre-extracted paper-claim in scientist's store**.

This phase is shippable on its own (eager external claims are useful even before the tree exists).

## 1. Resolved architecture (carried from the discussion)

- **Scientist-side extraction.** Scientist *reads* the PDF from bibliographer's library (via the
  pure-Python readers it already has — pdfplumber / python-docx / etc.) and *writes* paper-claims into
  **scientist's own store**. **Scientist never writes bibliographer's DB**; bib is a read-only source
  of PDFs and `discover --json`.
- **No new libkit capability, no shared schema.** Because bib never touches claims, the attributed
  claim schema is purely scientist's. Paper-claims reuse scientist's existing claim shape, flagged
  `attributed` and keyed to a *paper identifier* rather than an experiment.
- **Grep-able per-paper JSONL is the source of truth.** One `paper-claims.jsonl` per source paper,
  loaded into memory on demand (glob + parse). **No DB on the critical path.** A libkit semantic index
  is optional, derived, and deferred — added only if measured recall (the §5 calibration eval doubles
  as the test) shows grep under-recalls.

## 2. The attributed-claim record

A paper-claim is **ATTRIBUTED, not grounded**: pinned to what the paper *says* (its text), never to
reality. It must stay structurally and visually distinct from an internal grounded claim ("Smith et
al. report" vs "we measured"); the paper's own hedging is carried into `strength`; assertion is never
laundered into fact.

One JSON object per line. Proposed schema:

```jsonc
{
  "id": "silvasantos2015::prenatal-loss-50pct",  // stable; <paper-key>::<claim-slug>
  "paper": "doi:10.1234/abc",                    // source identifier; cross-walks to the bib library
  "citekey": "silvasantos2015",
  "kind": "attributed",                          // vs "grounded" for internal claims
  "paraphrase": "≈50% prenatal loss is tolerated in the model",  // OUR normalized vocabulary (grep target)
  "quote": "we observed loss of roughly half of …",             // verbatim span from the paper
  "evidence_sha": "<sha256 of the located quote span>",          // integrity pin / locator
  "locator": { "page": 5, "section": "Results" },
  "strength": "moderate",                        // normalized, reflecting the paper's hedging
  "hedge": "the authors write 'appears to be tolerated'",        // verbatim hedge snippet
  "n": 12,                                        // stated stats, if any
  "p": "<0.05",
  "caveats": ["single cohort", "no replication"],
  "methods_qualifier": "in vitro, HEK293",        // travels with the claim; never read context-free
  "conditioned_on": ["silvasantos2015::dosing-window"],  // "B given A" links (claim ids)
  "precis": false,                                // true → the per-paper précis claim (paper's own rollup)
  "borrowed": false,                              // true → background borrowed from elsewhere (don't double-count)
  "null_result": false                            // true → from the explicit null/negative pass
}
```

Notes:
- **`paraphrase` is the grep/search target** and is written in *our* consistent vocabulary, which is
  what makes plain `rg` adequate recall without a semantic index (the searcher and the store share a
  vocabulary). `quote`/`hedge` retain the paper's own words for fidelity.
- **`evidence_sha`** pins the located quote span so a cheap integrity check (`sci paper-claims verify`)
  can flag drift if the source text is re-OCR'd / replaced. This is the *only* runnable check an
  attributed claim supports (it cannot be re-derived against data we own).
- **`borrowed: true`** marks a claim whose true source is another paper (extract the paper's *own*
  contributions; don't double-count when the real source is also in the library).

## 3. Storage layout

```
<home>/paper-claims/<citekey>.jsonl     # one file per source paper; the canonical store
```

- **Per-paper file** is the Goldilocks shard (see redesign SPEC discussion): clean concurrent writes
  under fan-out (each paper its own file → no git HEAD races / merge conflicts), localized diffs, and
  per-node regeneration maps to a single-file rewrite. Not one-file-total (concurrency/locality), not
  one-file-per-claim (clutter).
- **JSONL** over a JSON array — line-grep, line-level diffs, idempotent whole-file rewrite on
  re-extraction, consistent with `screening.jsonl`.
- **Load semantics:** glob `paper-claims/*.jsonl`, parse each line into the in-memory claim set on
  demand. The whole corpus is single-digit MB at realistic scale, so "load all, filter in memory" is
  fine; nothing is paged.
- **Additive:** the PDF in the bib library is never modified; the JSONL *indexes* it.

## 4. Extraction (judgment, guided — not code-wrapped)

Per the skill's KEEP-as-code-vs-guide line, extraction is **agent judgment guided by a prose
reference** (`references/paper-claims.md`), not an LLM call wrapped in code. The agent reads the PDF
and authors the JSONL; `sci` provides scaffolding + validation, not the judgment.

Discipline the guide enforces (carried from redesign SPEC §3):
- **Comprehensive finding-grain:** findings, key secondary results, stated limitations, **plus an
  explicit null/negative pass** (`null_result: true`). NOT every sentence (noise); NOT lazy per-review
  (re-reads). Paper-claim sets are **not size-capped** — a dense paper yields a *larger* set.
- **Per-paper précis claim** (`precis: true`) at the head: the paper's arc + headline (its own
  rollup; the cheapest place to preserve narrative across atomization).
- **`conditioned_on` links** so "B given A" survives atomization.
- **Verbatim `hedge` + normalized `strength`** so "suggests" vs "demonstrates" is preserved.
- **`methods_qualifier`** on every claim.
- **Don't over-attribute** — mark `borrowed` background; extract the paper's own contributions.
- **Idempotent re-extraction:** re-running rewrites `<citekey>.jsonl` from scratch; cheap and
  repeatable. Irreducible residual = extractor recall (connections only visible in full text);
  mitigated by re-extraction, not eliminated. **Never discard the source.**

## 5. `sci` surface (Phase 2)

All offline, store-local:

- `sci paper-claims scaffold <citekey>` — resolve the PDF path from the bib library (read-only),
  create/open `paper-claims/<citekey>.jsonl`, emit the extraction brief (points at
  `references/paper-claims.md`). The agent authors the claims.
- `sci paper-claims validate <citekey>` — schema check: required fields, `kind=="attributed"`,
  `id`/`paper` well-formed, `conditioned_on` ids resolve, exactly one `precis`. Blocking findings on
  violation.
- `sci paper-claims verify <citekey>` — the quote-integrity check: each `evidence_sha` still matches
  the located span in the (retained) PDF. Flags drift.
- `sci paper-claims --json [--paper <citekey>] [--query <substr>]` — load + emit the claim set(s) for
  the `--json | python3 -c` consumption pattern; `--query` is a substring/regex filter over
  `paraphrase` (the grep path). No semantic ranking (deferred).
- **`[lit:]` resolution:** a `[lit:<claim-id>]` in a review/report resolves against
  `paper-claims/*.jsonl` (load → look up by `id`), exactly as `[claim:]` resolves against internal
  claims. The audit checks the cited paper-claim exists, is `attributed`, and (optionally) that its
  `evidence_sha` verifies.

## 6. Blast radius (Phase 2)

- New `references/paper-claims.md` (the extraction guide — the bulk of the value, prose).
- `scientist/` new module for the paper-claim model + per-paper JSONL load/validate/verify + the
  substring/`--json` access (reuses the existing claim shape where possible).
- `scripts/sci.py` — the `paper-claims` subcommand group.
- `provenance/report.py` — extend `[lit:]` resolution to look up pre-extracted paper-claims in the
  store (alongside today's path), and the attributed-vs-grounded distinctness in rendering/audit.
- PDF-reading reuse: the pure-Python readers scientist/analyst already have (no new dependency).
- `tests/` — schema validation, quote-integrity drift, `[lit:]` resolution to a stored paper-claim,
  per-paper file load/concurrency shape.
- **No** libkit change. **No** bibliographer change (read-only consumer of its PDFs + `discover`).

## 7. Open (Phase 2 detail)

1. **Data record vs pytest spec.** This SPEC models a paper-claim as a **JSONL data record** with a
   `verify` quote-integrity check, *not* a pytest spec — because an attributed claim has nothing to
   re-derive against owned data (its only runnable check is quote-matches-locator). Internal grounded
   claims stay pytest specs. Confirm this asymmetry is acceptable (the alternative — a generated
   `test_paperclaims_<citekey>.py` whose only assertion is the sha check — adds machinery for little
   gain).
2. **`paper-claims/` location** — at the scientist `home` root (program-wide, since paper-claims are
   shared across all the program's reviews) vs nested under a program dir. Proposed: `<home>/
   paper-claims/`, library-wide.
3. **Cross-paper `conditioned_on`** resolution + the précis-claim render contract — finalize when
   Phase 3's tree consumes them.
