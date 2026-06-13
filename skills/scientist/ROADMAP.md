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

A **semantic-pass discipline**, not a tool — the whole check is "read the claims, apply a fixed rule,"
which the reviewing agent does better than any CLI could, so there's deliberately no new code. During
`sci audit`'s semantic pass, for each `README.md` / `reports/*.md` the agent picks out every result
asserted in prose (quantitative *or* qualitative — both are grounded and audited the same way), maps it
to its `kind=claim` (cite inline with `[claim:<id>]`; pull claims via `sci query --kind claim` or the
`grounding_report.json`), and confirms the claim is grounded — `passed`/`xpass` **and** strong/moderate.
Else it reports `unbacked`, `weak-backing` (contradicted/weak — *with* its outcome+strength), or
`off-topic` (grounded claim cited but not about this sentence — the case only an agent catches). Severity
is tiered so the gate stays useful: an unbacked *qualitative* conclusion is advisory; an unbacked number,
or any bad/contradicted citation, is blocking. The grounded rule + `claim_id` format are the same ones
`index-claims` / `sci query --kind claim` / `sci trace` already use; the planned report phase
(`sci report`) runs the identical procedure over generated report Markdown. See
[references/review-audit.md](references/review-audit.md) and [references/auditing.md](references/auditing.md).

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
