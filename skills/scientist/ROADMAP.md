# Scientist roadmap — deepening the provenance pipeline

The pipeline is built: `raw → data → analysis → claims` is one auditable DAG over a single
`experiment.yml` provenance ledger, and `sci trace` walks any claim back to the original
measurements, flagging breaks. What remains is to make the chain **deeper** (finer provenance),
**executable** (analyses that actually re-run and reproduce), and **enforced** (no prose claim
without a grounded backing).

## Already shipped (for context)

- **Extraction** `raw → data` — faithful, deterministic, audited (`sci extract` / `audit` / `cellcov`).
- **Analysis** `data → analysis` — re-derivable `derive.py`; analysis-provenance recorded into `experiment.yml`.
- **Claims** `analysis → claims` — each a pytest spec; grounding report; indexed into libkit as
  `kind=claim` carrying honest outcome + strength.
- **End-to-end traceability** (`sci trace`) — a static walk of the DAG plus drift detection.

## 1. Analysis reproduction audit — do the analyses actually re-run?

`sci trace` checks the chain *statically* (inputs exist, recorded shas still match); it executes
nothing. Add an audit that **re-runs each `analysis/derive.py`** in a pinned environment and
checks the regenerated `analysis/tables|fig/*` reproduce the recorded artifacts (within fit
tolerance), and that the derivation read only from `data/`. The claim-time bypass guard already
flags out-of-`data/` reads — extend it to derivations executed under audit. Flag analyses that
don't run, don't reproduce, or pull inputs from outside `data/`. This turns "the recipe sha still
matches" into "the recipe still produces the numbers."

## 2. Finer-grained provenance — beyond file + sha

Provenance `inputs` are file-level today (`path` + `sha256`). Extend toward **sheet / cell-range /
slide / table** granularity where the readers can supply it, so an extracted value (or a quoted
figure) traces to the exact source cell or slide, not just the file. This sharpens `sci trace` —
a drifted input would point at *which* number moved.

## 3. Claims ⟷ prose enforcement — no ungrounded narrative ✅ shipped

`sci enforce-prose` enforces this, with control **inverted to the semantic-pass agent**: deciding
whether a sentence asserts a quantitative result is a language task, so the agent extracts the
assertions (`sci audit --json` just lists the `prose_docs` to read) and pipes them in; the deterministic
gate does the part worth pinning down — parse the `[claim:<id>]` citation, resolve the claim, check it's
grounded. An assertion clears only when its citation resolves to a `passed`/`xpass`, strong/moderate
claim, else it's flagged `unbacked`, `weak-backing` (cited only to a contradicted/weak claim — surfaced
*with* its outcome+strength), or `unknown-claim`. Store-free, backed by `grounding_report.json` like
`sci trace`; exit 1 if anything is flagged. The core `scientist.store._prose.enforce_prose(assertions,
claims)` is pure and reusable — the planned report phase (`sci report`) runs the same gate, its
generating agent emitting the assertions it wrote. See [references/review-audit.md](references/review-audit.md)
and [references/auditing.md](references/auditing.md).

## 4. Program-level traceability

`scripts/rollup.py` aggregates claims program-wide (the cross-experiment claim graph, drift). Add
a program-level **traceability status** — the per-experiment `sci trace` verdict rolled up — so
"is the program's stated evidence fully grounded?" is a single report.

## Resolved

- *One skill or several?* → one (`scientist`); the stages are internal capabilities sharing one
  provenance core.
- *Reader fidelity for pzfx / prism / docx / pdf / pptx?* → built (tabular + table readers and
  prose `doc()` text, all in `labfiles`). The remaining reader work is the cell-range granularity
  in §2, not new formats.
