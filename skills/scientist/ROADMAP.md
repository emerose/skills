# Scientist roadmap — deepening the provenance pipeline

The pipeline is built: `raw → data → analysis → claims` is one auditable DAG over a single
`experiment.yml` provenance ledger, and `sci trace` walks any claim back to the original
measurements, flagging breaks. What remains is to make the chain **deeper** (finer provenance),
**executable** (analyses that actually re-run and reproduce), **enforced** (no prose claim
without a grounded backing), and to add a terminal **report** phase (`claims → report`) that turns
grounded claims into a human-facing narrative without loosening the grounding discipline.

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

## 2. Finer-grained provenance — beyond file + sha *(deferred — low priority)*

Provenance `inputs` are file-level today (`path` + `sha256`). Extend toward **sheet / cell-range /
slide / table** granularity where the readers can supply it, so an extracted value (or a quoted
figure) traces to the exact source cell or slide, not just the file. This sharpens `sci trace` —
a drifted input would point at *which* number moved.

## 3. Claims ⟷ prose enforcement — no ungrounded narrative ✅ shipped

Done as part of `sci audit`'s semantic pass: for each `README.md` / `reports/*.md` the agent picks out
every result asserted in prose (quantitative *or* qualitative — both grounded and audited the same way),
pulls the experiment's claim set once, and maps each result to its `kind=claim` (cite inline with
`[claim:<id>]`; pull claims via `sci list --kind claim --experiment <exp>` / the `grounding_report.json`),
confirming the claim is grounded — `passed`/`xpass` **and** strong/moderate. **A grounded `kind=claim` is
the sole accepted backing** — a raw `analysis/` cell is grounded *provenance* but not *judged* evidence (no
outcome/strength), and a claim is what cites the artifact. When no claim covers a number, artifact-tracing
is *triage*, not an alternative backing: a value that traces verbatim to a current sha-pinned `analysis/`
artifact (verified via `sci read` / `sci trace`) is reported `artifact-only` — a **finding to clear by
authoring the claim**, cheap because the evidence already exists — vs. `unbacked` (no claim *and* no
artifact: invented/untracked). Else `weak-backing` (contradicted/weak — *with* its outcome+strength) or
`off-topic` (grounded claim cited but not about this sentence). Three severity tiers: **blocking** (unbacked
number / bad / contradicted citation), **finding** (`artifact-only` — author the claim), **advisory**
(unbacked *qualitative*). The grounded rule + `claim_id` format match `index-claims` / `sci query --kind
claim` / `sci trace`; the report phase (`sci report`, §5) runs the identical procedure over report Markdown,
citing claims the same way. See [references/review-audit.md](references/review-audit.md) and
[references/auditing.md](references/auditing.md).

## 4. Program-level traceability

`scripts/rollup.py` aggregates claims program-wide (the cross-experiment claim graph, drift). Add
a program-level **traceability status** — the per-experiment `sci trace` verdict rolled up — so
"is the program's stated evidence fully grounded?" is a single report.

## 5. Reports — `claims → report`: a grounded human narrative

Where a **claim** is one machine-checkable assertion, a **report** is a human-facing narrative
built *from* claims. It collects grounded claims — potentially fanning in from across experiments —
arranges them into a coherent argument, and pulls in figures and tables to make a point. It is for
humans, not machines: readable, concise, compelling. The same provenance discipline applies —
**no quantitative prose without a backing** — and the unit of backing is an *existing* grounded
`kind=claim` (the same sole-backing rule as §3; a raw artifact cell is grounded provenance but not
judged evidence). A report never re-litigates grounding and can't drift ahead of the evidence: to
assert something new, you author the claim first. (Figures and tables are *embedded as exhibits* —
sha-pinned artifacts, below — which is distinct from backing a quantitative *sentence*; the sentence
that draws a conclusion from an exhibit still cites the claim.)

**Authoring — Markdown + a citation syntax.** A report is git-diffable Markdown (like everything
else durable here) with an inline **citation syntax** in two roles: a **claim citation** by stable
`claim_id` (`<exp>::<test-file>::<node>`) **backs a quantitative assertion**, and an **artifact
reference** by path + role **embeds an exhibit** (a figure/table). The prose, section structure, and
argument are hand-authored; the citations are the load-bearing links the audit checks. Reports live
in **both** scopes, citing the same way: cross-experiment reports under `program/reports/<slug>/`,
per-experiment summary reports under `<exp>/reports/<slug>/`.

**Report-specific figures via a grounded derivation.** A compelling cross-experiment report often
needs a *new* comparison plot or summary table that no single experiment produced. Produce these
through a (program-level) `derivation` — the same machinery as `derive.py` — so the artifact is
sha-pinned and its inputs (the `data/`/analysis tables it reads) are recorded; the report then
embeds the grounded artifact. No ad-hoc, untracked graphics.

**`sci report` — build + audit + render.** One command that (1) **validates** every citation —
each cited `claim_id` must resolve in the claim index to a *live, current* claim, every embedded
figure/table to a *current* sha-pinned artifact; (2) **enforces grounding** — flag any quantitative
sentence with no *claim* citation (reusing/extending the §3 semantic audit — an embedded exhibit
doesn't substitute for the claim a conclusion-sentence needs), and refuse to present a contradicted
(`xfail`) or drifted claim as positive support, surfacing its real outcome + strength instead;
(3) **renders** the validated Markdown to the primary deliverable, a polished **PDF** with
figures embedded (optional HTML/docx). A report that cites a claim which has since flipped or
drifted **fails the audit**, exactly as `sci trace` flags a broken chain — so a shipped report is
provably backed by currently-true claims.

**Indexed + traceable.** The finished report is indexed into libkit (`kind=report`, embedded on its
title/abstract + section summaries) so "which report makes the case for the dose-dependent effect"
is answerable, and its citations extend `sci trace`: a report node sits atop the DAG, walkable down
through each cited claim to the original measurements. Program-level traceability (§4) then includes
"are our reports fully grounded?"

## Resolved

- *One skill or several?* → one (`scientist`); the stages are internal capabilities sharing one
  provenance core.
- *Reader fidelity for pzfx / prism / docx / pdf / pptx?* → built (tabular + table readers and
  prose `doc()` text, all in `labfiles`). The remaining reader work is the cell-range granularity
  in §2, not new formats.
- *Report design (§5) — what grounds the prose / scope / figures / output?* → reports **cite
  existing claims only** (author the claim first); **both** program- and per-experiment scopes;
  report-specific figures via a **grounded derivation** (no ad-hoc graphics); Markdown source
  rendered to **PDF** as the primary deliverable.
