# Auditing the data folder

Archivist's store drifts as experiments are added, re-analyzed, and re-summarized.
Two complementary passes keep it honest — a fast deterministic one and a semantic
one that actually reads the data. Both **only report**; fixes are applied through
`arx` (and land as PRs).

## 1. Structural — `arx check`

Deterministic, instant, no embeddings. Flags, per experiment:

- `missing:readme` — no `README.md`.
- `file-missing:<path>` — an indexed file is gone from disk.
- `unindexed:N` — N on-disk files aren't in the index → run `arx index <exp>`.
- `thin-metadata` — no CRO / study IDs / assays / ASOs extracted (often a stub or a
  README that needs filling).
- `layout:root-file:<name>` — a stray file at the experiment root (only `README.*`
  belongs there; everything else goes in `raw/ data/ protocol/ reports/ analysis/`).
- `redundant-archive:<zip>` — a zip whose members are already extracted in the same
  folder (the `raw.zip` pattern). Verify, then delete the zip (the originals also
  live in `Attic/`).

Run `arx check --json` to get a worklist you can drive fixes from.

## 2. Staleness — `arx audit`

Every generated doc (a README's managed sections, `SUMMARY.md`) carries an explicit
dependency block listing the evidence files it was built from with their `sha256` at
generation time. `arx audit` re-hashes those against disk:

- `up-to-date` — all dependencies unchanged.
- `STALE` — an input changed (`changed_inputs`) or went missing (`missing_inputs`).
  Regenerate the mechanical parts with `arx readme <exp>`, then review/refresh the
  narrative (next section).
- `no-deps-block` — a human README archivist hasn't managed yet; staleness can't be
  judged by hashing, so it always warrants a semantic look.

## 3. Semantic — the parallel-agent pass (authoritative for content)

Hashing tells you an input *changed*, not whether the *prose is still true*. For
that, read the data. `arx audit --json` emits, per experiment, the README path and
its `source_files`. Fan out one agent per experiment:

> Read `<readme>` and its `source_files`. Does every claim, number, and caveat in
> the README still match the data? List specific contradictions (claim vs. what the
> data shows), missing caveats, and stale numbers. Don't rewrite — report.

Collect the contradictions, then for each confirmed one regenerate/edit the README
(preserving the human caveats that still hold) and open a PR with `arx readme <exp>
--pr` or `arx pr`. This is the same technique bibliographer uses to verify a paper's
content against its metadata, and it's the **authoritative** check — a high or low
hash/overlap score is a hint, not proof.

## Cadence

Run `arx check` after every `intake` or big change, and a full `arx audit` (plus the
semantic pass on anything flagged) periodically — especially after re-analyses that
change results the summaries depend on.
