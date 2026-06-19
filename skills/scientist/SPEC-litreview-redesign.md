# SPEC — litreview redesign: claims pipeline + PRISMA protocol (replaces `must_confront`)

Status: **proposed** (design contract, pre-implementation). The unification decisions — the §7 crux
plus the four structural/implementation calls — are **resolved** as recorded below. The remaining
open questions (§10) are Phase 2/3 only and do not block Phase 1.

This SPEC unifies **two** redesigns of the litreview system that must land coherently because they
touch the same surface and one references machinery the other deletes:

- **Change A — PROSPERO/PRISMA protocol (replaces `must_confront`).** Drop the `@must_confront`
  obligation set + consumption-time omissions audit + must-confront-keyed staleness pin. Replace with
  committed, auditable *method + screening* artifacts. Governs the **breadth / coverage** axis.
- **Change B — hierarchical reviews as a claims pipeline.** A review is lossy compression of a claim
  corpus; make the *claim* the unit of reuse and store a review as a **tree of nodes** (parents
  reference children, never contain them). Adds a `paper → paper-claims` extraction layer. Governs the
  **depth / compression** axis.

They are largely **orthogonal axes** and compose into one pipeline. The critical reconciliation:
Change B's text still names `must_confront` and "the omissions audit" (`U = unique must-confront
claims`; "Papers confronted drives the omissions audit") — those are **re-expressed in Change A's
terms** below (§7). Nothing in Change B requires the `must_confront` tag to survive.

---

## 1. Motivation

**Change A.** `@must_confront` tagged a "contested core" of `[lit:]` claims and blocked a citing
report that didn't cite-or-waive each. It *duplicated* a judgment the litreview completeness critic
already makes (a left-out disconfirmer is already its hard floor) and only *froze and forwarded* it,
at the cost of a decorator, an omissions audit, a staleness pin, store cards, and a fail-closed
module-name finding. The integrity goal is better served the way systematic reviews actually do it —
**show your work**: don't tag which findings matter, make the survey's *method and screening*
auditable, so an omission is visible as "found and excluded for reason R," not as silence.

- *PROSPERO kernel* (no external registry — a committed file): pin question / sources / queries /
  inclusion criteria **before** screening. Guards against tuning scope to the desired answer.
- *PRISMA kernel*: account for the **full retrieved set** — every candidate tracked to
  *included* or *excluded-with-reason*. Guards against silent dropping.

**Change B.** A review must satisfy two budgets: a **reader budget** (optimum faithful length grows
only ~log in corpus richness, so flat reviews saturate the reader before covering a large field) and
a **writer budget** (don't re-research to re-express the same literature at a new altitude). Fix:
make the **claim** the unit of reuse; store the review as a tree addressed by node, parents
referencing children. Node *grain* is bounded by a reader ceiling **B**; node *content* is set by the
fidelity the topic demands. Richness is absorbed by **more nodes** (bushier/deeper tree), never by
lossier nodes.

---

## 2. The unified pipeline

```
internal:  raw   → data        → analysis → claims ─┐
external:  paper → paper-claims → [PRISMA screen] ──┤→ review-node tree → root rollup ("the review")
                                                    │
thesis report: authored synthesis OVER claims  ←────┘  (may mix internal [claim:] + external [lit:])
```

Both arms terminate in **claims**; reports and reviews are both authored synthesis *over* claims. **A
review is a report whose claims are all external.** A review node and a thesis report are the same
artifact kind (see §3 *kinds* open question); they differ only in whether their claims are external.

Layered concretely for one subtopic:

```
discover (bib) → screen (PRISMA: include/exclude+reason) → for each INCLUDED paper: extract paper-claims
              → leaf review-node cites primary paper-claims → rollup nodes cite [review:child] → root
```

---

## 3. Layer 1 — `paper → paper-claims` (new; eager generalization of the lit-support judge)

Extract a paper's claim set **once** into the library and reuse it across every review that touches
the paper. Today `[lit:]` claims are authored lazily per citation (re-reading wastes the writer
budget); this front-loads the same artifact.

- **A paper-claim** = `{ paraphrase, evidence_sha (locator in the paper), stated strength/caveats/n/p,
  verbatim hedge snippet }`.
- **ATTRIBUTED, not grounded.** Pinned to what the paper *says* (its text), **not** to reality.
  Internal claims are grounded (re-runnable specs vs evidence we own); paper-claims can only be
  faithful-to-text. Keep them structurally + visually **distinct** ("we measured" vs "Smith et al.
  report"). Carry the paper's own hedging into the strength. **Never launder assertion into fact.**
- **Granularity:** finding-grain **comprehensive** — findings, key secondary results, stated
  limitations, **plus an explicit null/negative pass**. NOT every sentence (noise); NOT lazy
  per-review (re-reads). Paper-claim sets are **not size-capped**: a long/dense paper yields a
  *larger* set, not a more compressed one.
- **Don't over-attribute:** extract the paper's *own* contributions; mark/skip borrowed background so
  a claim isn't double-counted when its true source is also in the library. (Mirrors the existing
  derivation per-artifact-context discipline.)
- **Additive:** the PDF stays; claims **index** it, never supersede it.
- **Inclusion ≡ extraction (resolved).** A paper marked `included` in screening **is** extracted to
  paper-claims — there is no second "extract or not" gate (it would smuggle back the lazy re-read this
  layer exists to kill). A paper too marginal to be worth comprehensive extraction is **excluded**
  with reason `"marginal — not extracted"` (still recorded as confronted). The exclusion bar can be
  generous, since extraction has real cost.
- **`[lit:]` reframed:** the `[lit:]` citation token stays, but now resolves to a **pre-extracted
  library paper-claim** (eager) instead of a lazily authored per-report one. This subsumes the
  caller-records `sci judge` support loop for external claims.

**Narrative-nuance preservation** (atomizing a paper loses arc, conditionality, emphasis, methods
qualification, negative space, confidence register). Mitigations — claims are *reversible pins into a
retained source*:
- a **per-paper précis claim** at the head of each set (the paper's own rollup; cheapest narrative);
- **`conditioned-on` links** between claims so "B given A" survives atomization;
- the **verbatim hedge snippet** beside the normalized strength;
- the **null/negative pass** (counters positive bias);
- the **methods qualifier** travels with each claim (never read context-free).
Irreducible residual = extractor recall; mitigated by cheap idempotent re-extraction, not eliminated.
**Rule: never discard the source.**

**Home (open question, §10):** paper-claims are attributed claims about a *library paper*, so they
plausibly live on the bibliographer side (it owns the PDFs + libkit store) and are consumed by
scientist reviews — deepening the `bib ↔ sci` coupling already introduced by `--ingest-discover`.

---

## 4. Layer 2 — PROSPERO/PRISMA protocol + screening (Change A), attached at leaves

**Resolved: one review-level protocol + append-only search passes** (not per-leaf). Pre-registration
(question / scope / inclusion + exclusion criteria) is genuinely review-level and is **one frozen
block** — the integrity anchor. Searches are **append-only passes**: an initial broad sweep, then
targeted deepenings as the tree grows and a dense leaf demands more recall. All candidates land in a
**single** `screening.jsonl` keyed by `query`, so there is one auditable funnel for the whole review.
This respects the chicken-and-egg (search → see claim density → split → maybe search the dense leaf
deeper) without forcing the tree to exist before the search. Inclusion criteria stay global; queries
accrete. Rollups never search — they roll up children. (In Phase 1, pre-tree, this attaches to the
single flat review unchanged.)

**Resolved: PRISMA discipline attaches to reviews only.** A thesis report does **not** carry its own
protocol/screening; it consumes a review and inherits the provenance. A report's external `[lit:]`
must either (i) resolve to a paper-claim in a cited review's included set, or (ii) be a flagged
**edge-import** with a one-line justification (reusing the existing flag-and-delegate edge-claim
rule). This keeps systematic screening where the literature actually lives — in reviews, the reuse
substrate — instead of turning every report into a mini systematic review.

### `protocol.md` (review-level) — pre-registration

```markdown
---
slug: it-aso-biodistribution
as_of: 2026-06-19
sources: [openalex, semantic-scholar, europepmc, pubmed, crossref, arxiv]
---
## Question & scope
## Search queries
## Inclusion criteria
## Exclusion criteria
```
Required: front-matter `slug`/`as_of`/`sources` + the four headings with non-empty bodies. Missing →
blocking `missing-protocol-field`. Content quality is the completeness critic's job.

### `screening.jsonl` (review-level, append-only) — the PRISMA flow

One JSON object per candidate the search surfaced:
```jsonl
{"id":"doi:10.1234/abc","title":"…","year":2015,"source":["openalex","pubmed"],"query":"…","decision":"included","citekey":"silvasantos2015"}
{"id":"arxiv:2401.00001","title":"…","year":2024,"source":["semantic-scholar"],"query":"…","decision":"excluded","reason":"review only — no primary biodistribution data"}
```
Fields: `id` (required), `title`, `year`, `source` (from `found_in`), `query`, `decision`
(`included|excluded`, required), `reason` (required iff excluded), `citekey` (required iff included).
Rank signals (`citation_percentile`, `fwci`, `cited_by_count`) carried through from discover are
advisory. Funnel derived: `identified = len(rows)`, `excluded` grouped by reason, `included`.

**Breadth saturation cutoff (ties Change B §5 to the screening log).** The `ε, m` saturation stop
(stop admitting papers when the last `m` add `< ε` novel claims) is **recorded in the screening log /
protocol** so coverage stays auditable — "breadth ≠ length": papers *confronted* (the screened set) is
deliberately broader than claims *promoted to prose*.

### `sci litreview … --ingest-discover <discover.json>`

Ingests `bib discover --json` (verified shape in §9) into `screening.jsonl` candidate rows with
`decision` unset, de-duped by `id`; author fills `decision`/`reason`. Carries `found_in`→`source`,
the query, and rank signals; sets `citekey` from `library_citekey` when `in_library`.

---

## 5. Layer 3 — the review-node tree (Change B)

- **A review node** = a `kind=report` (literature-scoped) artifact with two layers:
  - **Skeleton** — ordered references: `[claim:]`/`[lit:]` at leaves, `[review:child_id]` at rollups.
    Structured; the reuse substrate **and** the staleness tripwire.
  - **Synthesis** — authored prose *about the relationships* among the references (agree / outlier /
    conflict / gap). **Stored, not regenerated** — it is irrecoverable judgment.
- **Leaves** cite primary paper-claims. **Rollups** cite child nodes **+ a thin cross-cutting layer**
  — never re-cite primary claims.
- **Reference, don't contain.** Rollups point to children by id (containment reloads the corpus per
  parent and re-duplicates shared claims). Reader cost to any fact = `depth × B`. A claim shared
  across siblings lives **once**, referenced. **The root rollup IS "the review."**

### Knobs — uniform ceiling, adaptive content

- **B** — reader **load ceiling** per node (~one screenful). A *max* on a single retrieval unit, not a
  target every node is compressed to.
- **k** — default branching factor (~5–6), bounded by safe per-level compression. **Floats locally**:
  contested/high-density subtopics get smaller `k` / more rollup budget — compress less where
  disagreement *is* the content.
- **ε, m** — breadth saturation (above).
- **θ** — marginal-coverage floor (value of a token).

Node *content* is set by local fidelity need **L\***; **B** only caps grain. Depth/fan-out are
**derived**: `levels ≈ log_k(U / claims-per-leaf)`, where **U = unique admitted paper-claims** (the
breadth set fixed by `ε/m` saturation and recorded in screening — see §7 for why this is *not*
"must-confront"). Small field → one flat node; rich field → bushier/deeper tree.

### Sizing rules (two-sided, L\*-driven)

1. **Split when `L* > B`.** `L* ≈ λ·ln(1/λθ)`, `λ ≈ U·c` (`c` = tokens/claim). Over-dense node →
   fragment into `⌈L*/B⌉` subtopic leaves + a rollup parent. **Never compress harder than B; add
   nodes.** Computed, not by taste.
2. **Merge when siblings are collectively well under B.** Counters over-fragmentation; node size
   floats between the merge floor and the B ceiling.
3. **Rollups are lossy at the TOP, non-destructive overall.** A macro node compresses `k` children to
   B, but full complexity lives in the leaves, recovered by **descent**, not by fattening the rollup.
4. **Breadth ≠ length** (see §4 saturation cutoff).
5. **Per-node regeneration.** A leaf updates without touching siblings; a parent re-rolls only if a
   child's **summary** changed.
6. **Facts resolved at render; synthesis stored.** Numbers/citations pulled from claims at render
   (always fresh); the connective argument is authored and stable.
7. **Storage ≠ presentation.** Store as nodes; render to a single linear doc / PDF on demand.

### Calibration (one-time)

Pin `θ, ε, safe k` with one downstream eval: a question set the review must answer; generate reviews
at varied length/breadth; an LLM reader answers using **only** the review; score. Accuracy-vs-length
knee → `θ`; accuracy-vs-breadth → `ε`; fidelity-across-one-rollup → safe `k`. Reuse as defaults.

---

## 6. `sci` surface changes

- **Remove:** `--must-confront`, the `must_confront` field, `empty-must-confront` advisory, the
  fail-closed `missing-claims-module` finding, `litreview_must_confront`, `unaddressed-must-confront`,
  `[litreview-waive:]`, the must-confront-keyed pin.
- **Add (Change A):** protocol parse/validate; screening parse + `excluded-without-reason`,
  `malformed-screening-row`; **coverage cross-check** — every `[lit:]`-cited paper must be an
  `included` row (`cited-paper-unscreened`, blocking); `included-but-uncited` (advisory); funnel
  counts; `--ingest-discover`.
- **Add (Change B):** `[review:<node_id>]` citation type; per-node audit + render; `sci review render`
  (tree → linear doc/PDF); per-node staleness (skeleton sha; parent re-rolls iff child summary sha
  changed); paper-claim extraction/index commands (home TBD, §10).

**Enforcement split (resolved — mechanical vs critic).** `sci` enforces only the *objective* tree
rules, deterministically and offline: node load ≤ **B** (cheap proxy — rendered word/char count, not a
real tokenizer), skeleton well-formedness, reference-don't-contain (a rollup cites children, never
re-cites primary claims), and per-node staleness shas. The *fuzzy* calls stay with the completeness
critic: is the split at the right seam, was `ε`-saturation honest, did `L*` justify the depth. The
`B/k/ε/θ` knobs are calibrated **defaults / guidance**, never machine-enforced thresholds (`L*/λ/θ`
are model-estimated — enforcing them would make the audit nondeterministic). This matches the skill's
existing KEEP-as-code-vs-guide line.

### Staleness — composed

- **Breadth drift (Change A):** a consuming report pins to a leaf's **protocol queries + `as_of` +
  sources** sha. Re-running the registered search returning new included-eligible papers = completeness
  drift → **advisory** for the §3/completeness pass. `sci` stays offline; `bib` owns the network; fresh
  discover output re-enters via `--ingest-discover`.
- **Compression drift (Change B):** the **skeleton** is the tripwire — a leaf's claim set changes →
  leaf re-rolls; a child **summary** changes → parent re-rolls; untouched subtrees stay valid.
- These compose: breadth drift at a leaf *triggers* node regeneration that propagates up by summary
  change. The old must-confront-membership pin is gone.

---

## 7. Reconciling Change B's `must_confront` references (the collision)

Change B's text predates the `must_confront` removal and names it twice. Re-expressed:

| Change B as written | Re-expressed (this SPEC) |
|---|---|
| `U = unique must-confront claims` (depth derivation) | `U = unique **admitted** paper-claims` — the breadth set fixed by `ε/m` saturation and recorded in `screening.jsonl`. Depth scales with how much *survives screening*, which is exactly what the funnel measures. No hand-tagged subset needed. |
| "Papers confronted (drives **the omissions audit**)" | "Papers confronted" = the **screened set** (`screening.jsonl`). There is no mechanical omissions audit; coverage is checked by the **completeness critic against the screening log** (survey side) and the report's completeness critic (consumption side). |

Net: Change B's depth math is *better served* by the PRISMA funnel than by `must_confront` — `U` was
always meant to be "the claims the review must cover," and the screened-and-included set defines that
objectively, where the hand-tagged core only approximated it.

**Consumption model (replaces the omissions audit).** A report citing a review gets **no** mechanical
`unaddressed-*` gate. The guarantee is carried by: (a) survey-side completeness critic + auditable
screening; (b) the report-side completeness critic (already mandated), now checking against the
screening log instead of hand-tags. Both fresh-context, never the author.

**The conflict-survival invariant (resolved crux).** Lossy rollups would otherwise re-introduce the
cherry-pick failure `must_confront` guarded against: a consumer reading only the root (the intended
overview read) could miss a disconfirmer buried at a leaf. The replacement is a **compression
invariant**, not a tag — a disconfirmer or unresolved conflict must **survive to the root**. Mechanism:
rollups are **lossy on detail, lossless on the existence and direction of conflict** — a rollup must
name each child-level unresolved conflict as at least a one-line synthesis statement ("X and Y
disagree on Z; unresolved"), while the evidentiary detail compresses away and is recovered by descent.
This is the operational reading of the addendum's "compress less where disagreement *is* the content"
and of `k` floating smaller for contested subtopics. The completeness critic enforces it at **every
rollup level**: every child's unresolved conflict must be named in its parent's synthesis. This is the
keystone that makes dropping `must_confront` safe under compression — the disconfirmer guarantee
migrates from a hand-tag into a structural property of the tree.

---

## 8. Blast radius

Change A (Phase 1): `grounding/{__init__,plugin}.py` (drop decorator+marker); `provenance/
litreview.py` (drop must-confront branches; add protocol+screening parse/validate, cross-check,
funnel; rework `scaffold`); `provenance/report.py` (drop omissions/waiver/pin; add protocol-keyed
pin); `scripts/sci.py` (drop `--must-confront`; add `--ingest-discover`); `store/{_meta,_cli_browse,
_store}.py` (must-confront card → funnel counts); `tests/test_litreview.py`; `references/{litreview,
report,review-audit}.md`.

Change B (Phases 2–3, larger): a **paper-claims** model + extraction + index (home TBD); the
**`[review:]`** citation type + per-node tree model in `provenance/report.py`; `kind` reconciliation
(§10); a `sci review render` tree→doc renderer; node sizing/regeneration logic; tree-aware store
records; substantial new docs (a `references/reviews-tree.md`); the calibration eval harness.

---

## 9. `bib discover --json` contract (consumed by `--ingest-discover`)

Verified (`bib.py` `cmd_discover`):
```
{ "sources": {"<name>": <count>, …},
  "results": [ {"title","year","authors","venue","doi"|"arxiv_id"|"pmid",
                "found_in":[…],"citation_percentile","fwci","cited_by_count",
                "in_library","library_citekey"}, … ] }
```
Row mapping: `id` from doi/arxiv_id/pmid (prefixed); `source`=`found_in`; rank signals copied;
`citekey`=`library_citekey` when `in_library`; `decision` unset.

---

## 10. Phasing & open questions

**Recommended phasing** (A is a self-contained shippable increment; B is larger and depends on the
paper-claims layer):

- **Phase 1 — Change A** (must_confront removal + protocol/screening + `--ingest-discover` + breadth
  pin). Ships the breadth/coverage integrity story on the *current* flat review.
- **Phase 2 — paper-claims layer** (§3). The reuse substrate B needs; valuable on its own (eager
  external claims).
- **Phase 3 — review-node tree** (§5) + `[review:]` + render + calibration.

**Resolved (folded into the design above):**
- *Crux* — disconfirmer survival = the conflict-survival invariant (§7): rollups lossy on detail,
  lossless on the existence/direction of conflict; critic enforces per rollup level.
- *Search scope* — one review-level frozen protocol + append-only search passes, single funnel (§4).
- *Inclusion* — `included` ≡ extract paper-claims; marginal → exclude-with-reason (§3).
- *PRISMA scope* — reviews only; reports inherit or flag an edge-import (§4).
- *Knob enforcement* — hybrid: `sci` enforces the objective rules, the critic owns the fuzzy calls (§6).
- *Phase-1 defaults* — two-state `included|excluded` (phase nuance in `reason`); hand-edit
  `screening.jsonl` for v1 (no `--screen` helper); `sci` never calls search APIs (re-discover is
  manual, re-fed via `--ingest-discover`).

**Still open (Phase 2/3 only — do not block Phase 1):**
1. **`kind` reconciliation.** Current code has `kind=litreview`; an agreed-but-unbuilt `kind=review`
   exists; Change B says a review node is `kind=report` (literature-scoped). Pick one: review-node =
   `kind=report` + `literature-scoped` flag (retiring `kind=litreview`), or keep `kind=litreview` as
   the literature-scoped report kind carrying a node tree. **Load-bearing — decide before Phase 3.**
2. **paper-claims home** — bibliographer side (owns PDFs/libkit) vs scientist side. Affects which
   skill grows the extractor. **Decide before Phase 2.**
3. **`[litreview:]` → `[review:]`** — does the tree's `[review:<node>]` subsume the old `[litreview:]`
   consumption token (rename + back-compat), or do both coexist? Tied to (1).
