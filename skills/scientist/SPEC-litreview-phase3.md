# SPEC — Phase 3: the review-node tree (storage, audit, render)

Status: **proposed** (design contract, pre-implementation). Detail for **Phase 3** of
[SPEC-litreview-redesign.md](SPEC-litreview-redesign.md) §5. Builds on Phase 1 (PRISMA protocol +
`must_confront` removal, landed) and Phase 2 (the paper-claims layer,
[SPEC-litreview-phase2.md](SPEC-litreview-phase2.md)).

## 0. What Phase 3 delivers

A review stored as a **tree of claim-citing nodes** (parents reference children, never contain them),
so a large field is covered without saturating the reader: each node is ~one screenful (`B`),
richness is absorbed by **more nodes**, and any fact is reached in `depth × B`. The root rollup **is**
"the review." This is the **depth / compression** axis; Phase 1's PRISMA artifacts remain the
**breadth / coverage** axis, review-level at the root.

## 1. Resolved decisions (from design discussion)

- **`kind=litreview`** for every node; the node-tree machinery is shared with `kind=report`, `kind`
  encoding role (neutral/external vs concluding/internal). No new kind, no rename.
- **No new citation token** — `[litreview:<id>]` targets node ids: a rollup cites a child via
  `[litreview:<child-id>]`, a thesis report cites the root via `[litreview:<root-id>]`.
- **Per-node files, tree inferred** — one markdown file per node; the tree is the `[litreview:]`
  edge graph over those files (no separate manifest = one source of truth, concurrency-clean writes,
  per-node regeneration = per-file rewrite).
- **Flat review = the degenerate one-node tree** — a small field is a single root node, which *is*
  today's `review.md`. Phase 1's flat review is the `levels=0` case; **no migration**.
- **Conflict-survival is a critic obligation, not a `sci` tripwire** (Decision 1, resolved): the
  completeness critic at each rollup level enforces that every child-level unresolved conflict is named
  in the parent's synthesis. `sci` does **not** mechanically check conflict propagation — consistent
  with the redesign moving omission-checking from mechanical (`must_confront`) to fresh-context
  critics. `must_confront` was a heavyweight fix for a problem the critic already covers.

## 2. The node model

A review lives in `<scope>/litreviews/<slug>/`:

```
<slug>/
  protocol.md          # Phase 1, review-level (the frozen pre-registration)
  screening.jsonl      # Phase 1, review-level, append-only (the PRISMA funnel)
  review.md            # the ROOT node (id: "root")
  nodes/<node-id>.md   # every non-root node
```

Each node file is `[lit:]`/`[claim:]`/`[litreview:]`-citing markdown — the **same artifact shape the
audit already parses** (no parallel structured skeleton invented):

```markdown
---
id: it-aso-cns-distribution
parent: root
summary: >
  IT ASO reaches cortex and spinal cord with a rostro-caudal gradient; cord >> cortex
  at low dose. Two labs converge; one outlier (Lee 2019) reports the inverse.
---

<synthesis prose about the relationships among the references — agree / outlier / conflict / gap —
citing [lit:silvasantos2015::...], [lit:lee2019::...], and (at a rollup) [litreview:<child-id>].>
```

- **Skeleton = the parsed ordered citation list** (derived, not separately stored). Leaves cite
  primary claims (`[lit:]`/`[claim:]`); rollups cite children (`[litreview:<child-id>]`) **plus a thin
  cross-cutting layer**, and **never re-cite primary claims** that belong to descendants. The skeleton
  is the reuse substrate and the staleness tripwire.
- **Synthesis = the prose body.** Stored, never regenerated — irrecoverable judgment about how the
  references relate.
- **`summary`** (frontmatter) = the node's **rollup-facing abstract** (≈ `B/k` sized): what its parent
  summarizes and pins against. A node exposes its `summary` upward; its internals stay below.
- **Tree edges = the `[litreview:]` graph** over the node files. Children of a node = the node ids it
  cites via `[litreview:]`. The **root** is the node nothing cites (`review.md`). `parent` in
  frontmatter is a convenience/validation mirror of that inverse.

## 3. Staleness — per-node, reusing the existing pin machinery

- A **rollup pins to its children's summary shas**: frontmatter `rolled_against: {<child-id>: <sha>}`,
  the same pin pattern a report already uses for `[litreview:]`. `sci` recomputes each child's current
  `sha(summary)`; a mismatch → **`stale-rollup`** (re-roll the parent + re-pin). An untouched subtree
  stays valid.
- **Per-node regeneration** falls out: a leaf updates without touching siblings; a parent re-rolls
  **iff** a child's `summary` changed (not its internals). This composes with Phase 1's breadth pin —
  a new included paper changes a leaf, which (if it moves the leaf's summary) re-rolls upward.

## 4. `sci` surface (Phase 3)

Mechanical, offline, store-local — extends the `litreview` command group:

- `sci litreview add-node <slug> <parent-id> <new-id>` — scaffold a child node file (frontmatter +
  empty synthesis) under `nodes/`. **Splitting/merging judgment** (where to cut the seam) is the
  agent's, guided by `references/reviews-tree.md`; `sci` only lays out the file.
- `sci litreview audit <slug>` — the tree audit (see §5), on top of Phase 1's per-node `[lit:]`
  grounding + screening-coverage checks applied to **every** node.
- `sci litreview render <slug>` — tree → linear doc/PDF (see §6).

## 5. The tree audit — what `sci` enforces mechanically (vs the critic)

**Mechanical (`sci`, deterministic, blocking unless noted):**
- **Well-formed tree** — the `[litreview:]` node-edge graph is a single rooted tree: exactly one root
  (in-degree 0), acyclic, every non-root cited by **exactly one** parent (`malformed-tree`,
  `multiple-parents`, `cycle`, `orphan-node`). (`[lit:]`/`[claim:]` leaf citations may be shared
  freely across nodes — only *node* edges must form a tree.)
- **Node load ≤ `B`** — cheap proxy (rendered word/char count of the node's synthesis with facts
  resolved). Over ceiling → **`node-over-B`** (blocking: the core invariant is *never compress past B,
  add nodes*; the remedy — where to split — is the critic's).
- **Reference-don't-contain** — a rollup cites children + cross-cutting only; it must **not** re-cite a
  primary `[lit:]`/`[claim:]` that belongs to a descendant subtree (`rollup-recites-primary`,
  blocking).
- **`stale-rollup`** — child `summary` sha drift (§3).

**Critic (fresh-context subagent, guide-enforced — NOT `sci`):**
- **Conflict-survival** — every child-level unresolved conflict is named in the parent's synthesis, at
  **every** rollup level (the Decision-1 obligation).
- **Split/merge seam** — is the split at a real subtopic boundary; was `ε`-saturation honest; did `L*`
  justify the depth.
- **Synthesis quality** — does the prose actually engage the references (the existing completeness
  critic, now per node).

This is the redesign's KEEP-as-code-vs-guide line: `sci` checks the objective graph/size/structure;
judgment stays with the critic.

## 6. Render — tree → linear doc (facts resolved fresh)

`sci litreview render <slug>` linearizes the tree for a human sit-down read, **reusing the existing
`sci report` markdown→pandoc→PDF path**:

- **Depth-first traversal:** the root node's synthesis is the overview; each child becomes a nested
  section (heading depth = tree depth), recursing in citation order.
- **Facts resolved at render** — every `[lit:]`/`[claim:]` number/citation is pulled fresh from the
  claim store (Phase 2 paper-claims JSONL + internal claims) at render time; the stored synthesis is
  the stable connective argument, the facts are always current (redesign §5 rule 6).
- **Storage ≠ presentation** — nodes are the store; the rendered doc is a disposable view. "Overview"
  is the correct read of a rollup; "I need the detail" is answered by descent (deeper sections).

## 7. Knobs & calibration

`B` (load ceiling/node), `k` (branching ~5–6, floats smaller for contested/dense subtopics), `ε,m`
(breadth saturation, recorded in `screening.jsonl`), `θ` (marginal-coverage floor) are **calibrated
defaults / guidance**, never machine-enforced thresholds beyond the objective `node-over-B` proxy.
Pin them once via the redesign §5 calibration eval (question set → reviews at varied length/breadth →
LLM reader answers using only the review → score; accuracy-vs-length knee → `θ`, accuracy-vs-breadth →
`ε`, fidelity-across-one-rollup → safe `k`). Reuse as defaults.

## 8. Blast radius

- New `references/reviews-tree.md` — the guide (the bulk of the value, prose): node authoring,
  split/merge discipline, the per-node completeness critic + the conflict-survival obligation, the
  `B/k/ε/θ` knobs, the render/descent reading model.
- `provenance/litreview.py` (+ reuse `report.py` citation parsing & pin/sha helpers) — the tree audit:
  graph validation, node-load proxy, reference-don't-contain, `stale-rollup`.
- `provenance/report.py` — extend `[litreview:]` resolution to target **node ids** (intra-review child
  edges + external report→root), reusing the existing `[litreview:]` pin/staleness path.
- `scripts/sci.py` — `litreview add-node` / `audit` / `render`.
- Render — reuse the `sci report` pandoc path; depth-first linearizer.
- `tests/` — tree well-formedness (root/cycle/multiple-parents/orphan), `node-over-B`,
  reference-don't-contain, `stale-rollup` on summary drift, `[litreview:]` node-id resolution,
  render linearization, and the flat (one-node) degenerate case unchanged from Phase 1.

## 9. Open (Phase 3 detail)

1. **`add-node` mechanical scope** — does it ever move citations between nodes on a split, or is a
   split purely guided authoring (agent moves the `[lit:]` citations, `sci` only scaffolds the new
   file)? Proposed: minimal — scaffold only; the agent re-homes citations per the guide.
2. **`node-over-B` blocking vs advisory** — proposed blocking (it's the core invariant), but if the
   word/char proxy proves noisy in practice, demote to advisory and let the critic own it.
3. **Calibration values** (`B/k/ε/θ`) — pinned by the one-time eval (§7), not guessed here.
