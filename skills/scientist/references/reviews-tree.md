# The review-node tree — authoring a review that scales

A small literature review is one document. A *large* one isn't — the faithful length of a survey
grows only ~logarithmically in the richness of the field, so past some size a flat review either
drops findings or exhausts the reader. Phase 3 stores a review as a **tree of claim-citing nodes**:
parents **reference** children (they never contain them), each node stays ~one screenful (`B`),
richness is absorbed by **more nodes**, and any fact is reached in `depth × B`. **The root rollup
*is* "the review."**

This guide is the judgment half — when to split, how to roll up, what the critic must check. The
tool (`sci litreview …`) only checks the objective graph/size/structure and scaffolds files; the
seams and the synthesis are yours.

> A flat `review.md` is the **degenerate one-node tree** (`levels=0`). Everything in
> [references/litreview.md](litreview.md) — the PROSPERO/PRISMA `protocol.md` + `screening.jsonl`,
> the `[lit:]` backbone, the gaps section, consumption via `[litreview:]` — still holds, **at the
> review level (the root)**. This guide adds only the tree. There is no migration: a review grows
> into a tree when a node gets too big.

---

## 1. The node model

A review lives in `<scope>/litreviews/<slug>/`:

```
<slug>/
  protocol.md          # review-level (the frozen pre-registration)   ── breadth/coverage axis
  screening.jsonl      # review-level, append-only (the PRISMA funnel) ──┘
  review.md            # the ROOT node (frontmatter id: root)
  nodes/<node-id>.md   # every non-root node
```

Each node file is ordinary `[lit:]`/`[litreview:]`-citing Markdown with frontmatter:

```markdown
---
id: it-aso-cns-distribution
parent: root
summary: >
  IT ASO reaches cortex and spinal cord with a rostro-caudal gradient; cord >> cortex at
  low dose. Two labs converge; one outlier (Lee 2019) reports the inverse.
rolled_against: {}        # only on a rollup: {<child-id>: <summary-sha>}
---

<synthesis prose: how THIS node's references relate — agree / outlier / conflict / gap — citing
[lit:silvasantos2015::cord-gradient] at a leaf, or [litreview:<child-id>] at a rollup.>
```

Two layers, both load-bearing:

- **Skeleton** — the *parsed, ordered citation list* (derived, never separately stored). A **leaf**
  cites primary claims (`[lit:]` — internal literature claims or Phase-2 paper-claims). A **rollup**
  cites its children (`[litreview:<child-id>]`) **plus a thin cross-cutting layer**, and **never
  re-cites a primary claim that belongs to a descendant** (that's *containment*, which reloads the
  corpus per parent). The skeleton is the reuse substrate and the staleness tripwire.
- **Synthesis** — the prose body. **Stored, never regenerated** — it is the irrecoverable judgment
  about how the references relate. Facts (numbers, citations) resolve *fresh* at render; the
  connective argument is what's stored.

**`summary`** (frontmatter) is the node's **rollup-facing abstract** (≈ `B/k` sized): what its
parent summarizes and pins against. A node exposes its `summary` upward; its internals stay below,
recovered by descent.

**Tree edges = the `[litreview:]` graph** over the node files. A node's children are the node ids
it cites via `[litreview:]`. The **root** is the node nothing cites (`review.md`). `parent` in
frontmatter is a convenience mirror the audit cross-checks.

---

## 2. When to split (`L* > B`) and when to merge

Node *content* is set by the local fidelity the subtopic demands; **`B` only caps grain.**

- **Split when a node exceeds `B`.** `sci litreview audit` flags `node-over-B` (a word-count proxy,
  default ~500, `$SCIENTIST_REVIEW_B_WORDS`). The remedy is **never "compress harder"** — it is to
  **fragment into subtopic leaves + a rollup parent.** Cut at a *real* subtopic boundary (a
  mechanism, an organ, a dose regime, a contested axis), not an arbitrary midpoint. Richness ⇒ more
  nodes, never lossier nodes.
- **Merge when siblings are collectively well under `B`.** Over-fragmentation costs the reader a
  descent for little content. Node size floats between a merge floor and the `B` ceiling.
- **`k` (branching, ~5–6) floats *smaller* for contested/dense subtopics** — compress *less* where
  disagreement *is* the content. A rollup over a contested area earns more budget and fewer
  children.

Depth and fan-out are **derived**, not chosen: `levels ≈ log_k(U / claims-per-leaf)`, where `U` is
the unique admitted paper-claims (the breadth set fixed by `ε`-saturation and recorded in
`screening.jsonl` — see [references/litreview.md](litreview.md)). Small field → one flat node; rich
field → a bushier/deeper tree.

### The mechanics of a split

```
sci litreview <slug> --add-node <new-id> --parent <parent-id>   # scaffold nodes/<new-id>.md
```

`sci` **only lays out the file** (frontmatter + an empty synthesis stub). **You** then:

1. **Move** the relevant `[lit:]` citations out of the parent and into the new leaf's synthesis.
2. **Add the edge**: cite `[litreview:<new-id>]` from the parent's synthesis.
3. **Write the parent's rollup** of the new child (a sentence or two; see §3) and the child's
   `summary`.
4. `sci litreview <slug> --write-rollup-pins` to record each rollup's `rolled_against`, then
   re-audit.

Moving a citation is resolution-safe: a `[lit:]` id resolves the same wherever it lives.

---

## 3. Rolling up — lossy on detail, **lossless on conflict**

A rollup compresses `k` children to ~`B`. It is **lossy at the top** (the evidentiary detail lives
in the leaves, recovered by descent) but must be **non-destructive overall**. The one thing a
rollup may **never** compress away:

> **Conflict-survival (the keystone).** Every child-level **unresolved conflict or disconfirmer
> must survive to the root.** A rollup must *name* each child-level unresolved conflict as at least
> a one-line synthesis statement ("X and Y disagree on Z; unresolved"), even as the detail
> compresses. A reader of only the root (the intended overview read) must never miss a disconfirmer
> buried at a leaf.

This is what makes dropping the old `@must_confront` tag safe under compression: the disconfirmer
guarantee migrates from a hand-tag into a **structural property of the tree**, enforced by the
completeness critic **at every rollup level** (it is *not* a `sci` tripwire — see §5). When you roll
up, ask at each parent: *did any child surface a conflict, outlier, or negative result that my
synthesis fails to mention?* If so, name it.

A rollup cites children + a thin cross-cutting layer (a new connective `[lit:]` is fine). It must
**not** re-cite a primary claim a descendant already cites — `sci` blocks that as
`rollup-recites-primary`.

---

## 4. Staleness — per-node, compositional

- A **rollup pins its children's summary shas**: frontmatter `rolled_against: {<child-id>: <sha>}`.
  `sci` recomputes each child's current `sha(summary)`; a mismatch → **`stale-rollup`** (re-roll the
  parent, then `--write-rollup-pins`). An **untouched subtree stays valid** — editing one leaf never
  invalidates its cousins.
- **Per-node regeneration falls out:** a leaf updates without touching siblings; a parent re-rolls
  **iff** a child's *summary* changed (not its internals). This **composes with the Phase-1 breadth
  pin**: a newly-included paper changes a leaf, which — if it moves that leaf's `summary` — re-rolls
  upward by the same mechanism.
- Pin only on `summary` change, so a node's internal rewording doesn't churn its parent. Keep the
  `summary` tight and stable; let it move only when the rollup-relevant story moves.

---

## 5. What `sci` checks (mechanical) vs the critic (judgment)

**Mechanical — `sci litreview audit <slug>`, deterministic, blocking unless noted:**

- **Well-formed tree** — the `[litreview:]` node-edge graph is a single rooted tree: one root
  (`review.md`), acyclic, every non-root cited by **exactly one** parent. (`malformed`/`root-is-child`,
  `multiple-parents`, `cycle`, `orphan-node`, `unknown-node-edge`, `duplicate-node-id`.) Primary
  `[lit:]` citations may be shared freely across nodes — only *node* edges must form a tree.
- **Node load ≤ `B`** — the word-count proxy (`node-over-B`).
- **Reference-don't-contain** — a rollup must not re-cite a descendant's primary claim
  (`rollup-recites-primary`).
- **`stale-rollup`** — child-`summary` sha drift.
- Plus, per node, the Phase-1 grounding: every `[lit:]` backed, literature-only (`[claim:]`/`[report:]`
  in a node is blocking), and — review-level, over **all** nodes — protocol + screening + the
  coverage cross-check + the mandatory gaps section.

**Critic — a fresh-context subagent, guide-enforced, NOT `sci`:**

- **Conflict-survival** (§3) — every child-level unresolved conflict named in the parent's
  synthesis, at **every** rollup level. This is the keystone; the critic owns it.
- **Split/merge seam** — is the cut at a real subtopic boundary; was `ε`-saturation honest; did the
  density actually justify the depth.
- **Synthesis quality** — does the prose engage the references (agree/outlier/conflict/gap), or just
  list them.

This is the skill's KEEP-as-code-vs-guide line: `sci` checks the objective graph/size/structure;
every judgment about *seam, honesty, and conflict-propagation* stays with the critic. Run it after
`sci litreview audit` is GROUNDED — green means the tree is well-formed, not that it is *good*.

---

## 6. Reading & rendering — storage ≠ presentation

```
sci litreview <slug> --render review.pdf        # tree → one linear doc (facts resolved fresh)
```

The tree is the **store**; the rendered document is a disposable **view**. `--render` linearizes
depth-first — the root's synthesis is the overview, each child a nested section (heading depth =
tree depth), recursing in citation order — and every `[lit:]`/`[claim:]` number is pulled **fresh**
from the claim store at render time (the stored synthesis is the stable connective argument; the
facts are always current). "Overview" is the correct read of a rollup; "I need the detail" is
answered by **descent** into deeper sections, never by fattening the rollup.

---

## 7. Knobs (calibrated defaults, not hard thresholds)

`B` (load ceiling/node), `k` (branching, floats smaller for contested subtopics), `ε,m` (breadth
saturation, recorded in `screening.jsonl`), `θ` (marginal-coverage floor). Only the objective
`node-over-B` word proxy is machine-enforced; the rest are **guidance**, pinned once by the
one-time calibration eval (a question set the review must answer → reviews generated at varied
length/breadth → an LLM reader answers using **only** the review → score; accuracy-vs-length knee →
`θ`, accuracy-vs-breadth → `ε`, fidelity-across-one-rollup → safe `k`). Until calibrated, the
defaults stand; never treat `B/k/ε/θ` as a gate beyond the `node-over-B` proxy.
