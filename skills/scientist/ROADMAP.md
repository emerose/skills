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
- **Analysis reproduction audit** (`sci reproduce`) — the *executable* complement to `trace`:
  re-runs each `analysis/derive.py` in the pinned environment and checks the regenerated
  `analysis/tables|fig/*` reproduce the recorded artifacts (within tolerance) and that the
  derivation read only from `data/` (the bypass guard, extended to derivations). See §1.

## 1. Analysis reproduction audit — do the analyses actually re-run? *(shipped)*

**Shipped** as `sci reproduce <exp>` (`provenance/reproduce.py`; docs in
[references/review-audit.md](references/review-audit.md)). `sci trace` checks the chain
*statically* (inputs exist, recorded shas still match); it executes nothing. `sci reproduce`
**re-runs each `analysis/derive.py main()`** in the pinned environment under a derivation-audit
context (writes to scratch, never over the recorded artifacts; no provenance written), and checks
the regenerated `analysis/tables|fig/*` reproduce the recorded artifacts (tables: exact sha then a
numeric/log tolerance; figures: regenerated + tolerant PNG-dimension check, not byte-compared), and
that the derivation read only from `data/`. The claim-time bypass guard was extended to wrap a
derivation execution, so an out-of-`data/` read is flagged for a derivation exactly as for a claim.
Emits three independent verdicts per experiment — **runs / reproduces / reads_only_data** — and an
overall REPRODUCES / BROKEN status.

## 2. Finer-grained provenance — beyond file + sha

Provenance `inputs` are file-level today (`path` + `sha256`). Extend toward **sheet / cell-range /
slide / table** granularity where the readers can supply it, so an extracted value (or a quoted
figure) traces to the exact source cell or slide, not just the file. This sharpens `sci trace` —
a drifted input would point at *which* number moved.

## 3. Claims ⟷ prose enforcement — no ungrounded narrative ✅ shipped

Done as part of `sci audit`'s semantic pass: for each `README.md` / `reports/*.md` the agent picks out
every result asserted in prose (quantitative *or* qualitative — both grounded and audited the same way),
maps it to its `kind=claim` (cite inline with `[claim:<id>]`; pull claims via `sci query --kind claim` or
the `grounding_report.json`), and confirms the claim is grounded — `passed`/`xpass` **and** strong/moderate.
Else it reports `unbacked`, `weak-backing` (contradicted/weak — *with* its outcome+strength), or
`off-topic` (grounded claim cited but not about this sentence). Severity is tiered: an unbacked
*qualitative* conclusion is advisory; an unbacked number, or any bad/contradicted citation, is blocking.
The grounded rule + `claim_id` format match `index-claims` / `sci query --kind claim` / `sci trace`; the
planned report phase (`sci report`) runs the identical procedure over generated report Markdown. See
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
