# Literature reviews — `kind=litreview` (a neutral evidence map)

> Load this when authoring, auditing, consuming, or updating a **litreview**. It builds on the
> report machinery: the `[lit:]` grounding rubric and the authoring/voice discipline in
> [report-authoring.md](report-authoring.md), and the citation/audit/render mechanics in
> [report.md](report.md). This file is only the part that is *specific to a litreview*; it does
> not restate the `[lit:]` rules or the sweep method.

A **litreview** is a thesis-independent survey of the **third-party literature** on one
sub-question — an organized, assessed map of what the field reports, who reports it, how strong
each piece is, and where it disagrees or is silent. It draws **no program conclusions**. A *report*
argues toward a recommendation; a litreview only lays out the evidence a report would argue *from*.

It exists for three reasons a report cannot serve on its own:

1. **It decouples gathering from arguing.** A report's literature sweep is performed *in service of*
   its thesis, so it is structurally prone to surfacing what supports the argument. A litreview's
   success criterion is *coverage of the question and fair characterization of each piece*, not
   support for a thesis — a cleaner, more checkable bar.
2. **It makes selective citation auditable.** A neutral, reasonably complete map of a topic is the
   set a downstream report's citations are checked *against*. Because the survey commits its **search
   protocol and full screening log**, an omission is visible as "found and excluded for reason R," not
   as silence — a report that leans on the convenient half of a contested literature is doing so
   against an auditable record (see *The PROSPERO/PRISMA discipline* and *Consumption* below).
3. **It centralizes reuse and staleness.** Many reports lean on the same biology. Surveyed once,
   re-swept once: the reports that cite a litreview inherit its re-derivation instead of each
   re-sweeping (see *Staleness*).

A litreview is held to a **high completeness/fairness bar**: the test is whether it neutrally and
completely characterizes the published evidence, not whether it argues well. It should *read* like a
review article — a synthesis, not a catalogue of per-paper summaries (see *Structure*) — but it
carries none of a report's persuasive intent. Where a report works to *convince*, a litreview only
works to *represent the literature faithfully* and point the reader to the primary sources.

## What a litreview is NOT

- **Not a conclusion.** No recommendation, target, dose, lead-molecule call, or program decision.
  The line: judgments *about the evidence* — one lab, contested, direct vs. inferential, replicated
  or not — are the litreview's whole job; judgments that *synthesize toward a program decision* are
  out of scope and belong in the report that cites it. If a sentence tells the program what to do,
  it does not belong here.
- **Not internal data.** A litreview surveys only external literature: its claims are `[lit:]` only.
  It never grounds on a Kicho `[claim:]`. Kicho data meets the literature in the *report*, where the
  feasibility-vs-validation line is drawn (see [report-authoring.md](report-authoring.md) →
  *Derive; don't presuppose*). Keeping Kicho data out keeps the litreview the genuinely independent
  picture.
- **Not the library.** The bibliographer library is the corpus (papers, full text, search). A
  litreview is the program's *assessed slice* of it. Gathering — the broad sweep, `bib discover`,
  selective banking — is bibliographer's job and is unchanged (see *Gathering* below); the litreview
  owns only the assessment and the survey.

## Where it lives

Parallel to reports, under the program layer:

```
program/litreviews/<slug>/
  review.md        ← the survey (this artifact)
  protocol.md      ← the PROSPERO-style pre-registration (question/scope/queries/criteria)
  screening.jsonl  ← the PRISMA screening log (every candidate → included|excluded-with-reason)
  prompt.md        ← the conclusion-free generation brief (re-derivable; see report-authoring.md)
program/claims/test_litreview_<slug>.py   ← its [lit:] claim module
```

The claim-module name is **`test_litreview_<slug>.py` with the slug's hyphens mapped to
underscores** (a Python module name can't carry a hyphen — litreview `it-aso-biodistribution` →
`program/claims/test_litreview_it_aso_biodistribution.py`), so its `[lit:]` claims belong to the
litreview. The `protocol.md` and `screening.jsonl` are **committed artifacts** that make the
survey's *method and screening* auditable — the integrity anchor that replaced the old hand-tagged
must-confront set (see *The PROSPERO/PRISMA discipline* below). `sci new-litreview <slug>` scaffolds
all five files.

`slug` is the litreview-folder name. One litreview per **major sub-question**, not one giant program
review — match the supporting-report decomposition [report-authoring.md](report-authoring.md) already
encourages (e.g. `it-aso-biodistribution`, `ube3a-dosage-biology`). Per-question scope also *bounds*
the completeness problem: "complete on IT biodistribution" is tractable; "complete on everything" is
not. A report cites the 1–3 litreviews its argument spans.

## Structure of `review.md` — a synthesis, not a form

> **When a review outgrows one document**, store it as a **node tree** — `review.md` becomes the
> root rollup and subtopics move into `nodes/<id>.md`, wired by `[litreview:<child>]` edges; the
> root rollup *is* the review. A flat `review.md` is just the degenerate one-node tree (no
> migration). The whole of this file still applies at the **root / review level**; the tree adds
> split/merge seams, rollups, and the conflict-survival obligation. See
> [reviews-tree.md](reviews-tree.md). `sci litreview` audit/render are tree-aware automatically.

A litreview reads like a review article: a **synthesis organized by the themes of the field**,
engaging specific studies and their numbers, showing where the literature agrees, conflicts, and is
silent — but carrying **no novel argument or conclusion of its own**. Its two jobs are to (1) fairly
characterize what the published literature reports and (2) be a **roadmap** to the primary sources, so
a reader (or a downstream report-author) can see who found what, in what system, and drill straight to
the original paper.

The external standard is the narrative-review literature — Pautasso's *Ten Simple Rules for Writing a
Literature Review* [`pautasso2013ten`] and the **SANRA** quality scale [`baethge2019sanra`], both in
the bibliographer library (`bib query "<the one thing you want>" pautasso2013ten` for a granular read).
**You do not need to load either to write a litreview** — the discipline below is self-contained; the
references are for calibration only. We follow that standard with **one deliberate departure**: a
normal review rewards the author's *own* critical thesis, and ours **forbids** it.

**Synthesize, don't summarize — but stay attributable.** A litreview is not a string of per-paper
summaries, and not a fixed template of buckets. Organize by *theme*; in each paragraph lead with a
point and weave the relevant studies together — who found it, in what system, how directly, agreeing
or conflicting. The connective tissue must itself be *observable from the sources* ("X and Y report
opposite effects under condition C"), **never** the author's inference ("the field is therefore
converging on Z"). If you find yourself drawing a conclusion the papers don't state, stop — that is a
report's job, not a litreview's.

- **Summary (wrong):** "Lin et al. report tolerance to 50% loss. Okafor et al. report dose-dependent
  deficits. Mehta et al. report variable phenotypes."
- **Synthesis (right):** "Whether partial loss is tolerated appears to turn on *timing* rather than
  *depth*: Lin et al. saw no deficit at 50% reduction induced prenatally [lit:…], whereas Okafor et
  al. found graded deficits from postnatal reduction [lit:…] — though both rest on one lab's inducible
  system, and Mehta et al. report the same reduction yielding divergent phenotypes across animals
  [lit:…], so a single tolerance threshold is not established."

The right version grounds each clause in a `[lit:]` claim *and reads off the relationships from the
studies themselves* — it invents no thesis; it reports that the field's own results point at timing.

**Appraise strength descriptively, take no position.** Note the weight of each line of evidence as you
go — single lab, indirect, small *n*, unreplicated, contested — because a reader needs it to judge the
source. This is description, not a verdict: surface the limitation, don't rule on it. (This is the
half of "be critical" we keep; the evaluative half — *your* judgment of who is right — we drop.)

**Be a roadmap to the primary literature.** Name the studies and their systems *in the prose*, not
only in footnotes, so the review doubles as a finding-aid. Every load-bearing statement resolves to a
`[lit:]` claim pinned to a specific paper — and always the **primary** source (see
[report-authoring.md](report-authoring.md) → *Always cite the primary source*).

**The shape — adapt to the evidence, never template:**

- **Front matter** as for a report (`title`, `date`, `classification`).
- **Question & scope** — why the question matters, and what the survey covers and does *not*
  (SANRA's *importance* + *aims*). One short paragraph.
- **Body, organized thematically** — sections that follow the natural joints of the field (by
  mechanism, model system, developmental axis — whatever the topic's structure is), in an order you
  could defend, each synthesizing per the rules above. For the *shape* of a real review, a
  gene-biology review typically runs regulation → dosage role → model systems → distribution →
  mechanism → function → open questions — a logical thematic flow, not a grid.
- **Contested status** — an honest read of whether the field genuinely splits. Where it does, lay the
  competing accounts side by side, each its own `[lit:]` claim, cross-referencing; do not flatten a
  disagreement into whichever side you list first. But the honest finding is often that there is *no*
  two-camp controversy — the evidence converges, or the real fault line is something else (e.g.
  single-lab dependence). **An explicit "no genuine controversy here; the contested axis is X"
  discussion satisfies this expectation just as competing accounts do** — and a `## Controversies`
  heading with nothing contested under it does not. The audit reports this as a **content-based
  advisory, never a blocking check, and never satisfiable by a heading title alone**: never retitle a
  section to trip it. The real bar is that the contested evidence is *engaged and screened-in* (the
  completeness critic checks coverage against `screening.jsonl`, below), not that a particular
  heading exists.
- **Gaps / open questions** (mandatory) — what the literature does *not* settle: unmeasured regimes,
  questions answered only by analogy, single-lab results never replicated. The litreview's analog of a
  report's assumptions section, and the first place incompleteness shows up *by its absence*. A
  litreview with no gaps section is not done.
- **No conclusion that recommends** — close with a state-of-knowledge summary, never a program
  decision.

**`supporting / contradicting / equivocal / absent` is a coverage *checklist*, not a prose form.** The
author and the completeness critic apply it *per theme* — "have I represented the contradicting and
the absent evidence here?" — to test coverage. It never dictates the shape of the prose. A theme
covered only by supporting claims is a red flag for a steered survey, but the fix is to find and weave
in the missing evidence, not to add an "Absent:" paragraph.

## The claim backbone — `[lit:]` as usual

Every load-bearing assertion is a grounded `[lit:]` claim authored exactly as in
[report.md](report.md) → *Authoring and reviewing a literature claim*: `source(quote=, paraphrase=)`,
the locator ladder → strength ceiling, the caller-records `sci judge` support loop, independence via
`group=`, primary/secondary. None of that changes. The litreview's claim module is just where those
claims now live (per-litreview, so parallel authors don't collide), instead of a per-report
`test_literature_<slug>.py`.

```python
@kind("literature")
@strength("moderate")
def test_floor_developmentally_gated():
    """A tolerated ~50% prenatal loss bounds the safety ceiling."""
    source(citekey="silvasantos2015", quote="…", paraphrase="…")
```

**Bibliometric claims belong here too.** A claim *about the literature* — "the most-cited result on
this question is X," "Y is rarely replicated" — is third-party and litreview-legal, but it cannot be
quote-grounded (no paper states its own citation count). Author it as a `@kind("bibliometric")`
claim grounded on a stored OpenAlex metric via `cited_by()`/`metric()` (see [report.md](report.md) →
*Bibliometric claims*) and cite it `[lit:]` like any other. These are precisely the meta-claims a
litreview is tempted to assert as an ungrounded flourish; ground them or cut them.

## The PROSPERO/PRISMA discipline — show your work, don't tag what matters

A litreview no longer hand-tags a "contested core" for a citing report to confront. Instead it
**makes its method and screening auditable**, the way systematic reviews actually guard against a
steered survey: pre-register what you'll search and how you'll include, then account for the *full*
retrieved set. An omission becomes visible as "found and excluded for reason R," not as silence. Two
committed artifacts live beside `review.md`:

### `protocol.md` — pre-registration (the PROSPERO kernel)

Pin the question / sources / queries / inclusion + exclusion criteria **before** screening, so scope
can't be tuned to the desired answer. Front matter `slug` / `as_of` / `sources` plus four required
non-empty headings:

```markdown
---
slug: it-aso-biodistribution
as_of: 2026-06-19
sources: [openalex, semantic-scholar, europepmc, pubmed, crossref, arxiv]
---
## Question & scope
How a lumbar-route ASO distributes across the CNS, and what bounds region-to-region exposure.
## Search queries
"intrathecal ASO biodistribution", "antisense oligonucleotide CNS distribution lumbar", …
## Inclusion criteria
Primary biodistribution measurements in a mammalian model, any route stated.
## Exclusion criteria
Reviews with no primary data; modeling-only papers; non-CNS distribution.
```

The audit checks **presence + non-emptiness only**: a missing file is a blocking `missing-protocol`;
a missing/empty front-matter key or heading is a blocking `missing-protocol-field`. Whether the
criteria are *good* — narrow enough, honest about scope — is the completeness critic's call, not the
tool's.

### `screening.jsonl` — the PRISMA flow (account for the full set)

One JSON object per candidate the search surfaced, each tracked to *included* (with a `citekey`) or
*excluded* (with a `reason`). Append-only; one auditable funnel for the whole review:

```jsonl
{"id":"doi:10.1234/abc","title":"…","year":2015,"source":["openalex","pubmed"],"query":"…","decision":"included","citekey":"silvasantos2015"}
{"id":"arxiv:2401.00001","title":"…","year":2024,"source":["semantic-scholar"],"query":"…","decision":"excluded","reason":"review only — no primary biodistribution data"}
```

Fields: `id` (required; `doi:`/`arxiv:`/`pmid:` form), `title`, `year`, `source` (the engines that
surfaced it), `query`, `decision` (`included|excluded`), `reason` (required iff excluded), `citekey`
(required iff included). Rank signals carried through from discover (`citation_percentile`, `fwci`,
`cited_by_count`) are advisory. A row whose `decision` is still unset is a *pending* candidate (e.g.
just ingested — see below), not an error. The audit derives the **PRISMA funnel** — `identified` =
all rows, `excluded` grouped by reason, `included` — and flags `malformed-screening-row` (bad JSON,
no `id`, an `included` row with no `citekey`) and `excluded-without-reason`, both blocking.

> **Two-state, by design.** Phase 1 records `included|excluded` only; any phase nuance ("title/abstract
> screen vs full-text screen") goes in the `reason` text, not a 3-phase funnel. Screening is **hand-edited**
> — there is no `--screen` helper yet.

### Seeding the log — `--ingest-discover`

`sci` never calls a search API (it stays offline). Run `bib discover` (bibliographer owns the
network), then pipe its JSON into the screening log:

```bash
bib discover "intrathecal ASO biodistribution" --json > discover.json
sci litreview program/litreviews/it-aso-biodistribution/review.md --ingest-discover discover.json
```

Each result becomes a row with `decision` **unset** — `id` from doi/arxiv_id/pmid, `source` from
`found_in`, the rank signals copied through, `citekey` from `library_citekey` when the paper is
already in the library — de-duped by `id` against the existing file. You then screen each candidate
to `included|excluded(+reason)` **by hand**. A re-discover later (to widen recall as a dense theme
grows) is just another `--ingest-discover` of fresh output.

### The coverage cross-check (the integrity core)

The artifacts only bite when they constrain the prose: **every `[lit:]`-cited paper must appear as an
`included` screening row** (matched by citekey). A citation to a paper that was never screened in is a
blocking `cited-paper-unscreened` — you cited evidence that never passed (or was never recorded in)
the funnel. The mirror case — an `included` paper that **no** claim cites — is the advisory
`included-but-uncited`: either it is screened-in-but-not-yet-written (a completeness worklist item) or
a candidate to drop. This is what makes selective citation visible: the report's citations are checked
against a *committed* screened set, not against the author's memory of the field.

## Consumption — `[litreview:<id>]` and the staleness pin

A report grounds on a litreview with `[litreview:<id>]`, where `<id>` is `<exp-or-program>::<slug>`
(almost always `program::<slug>`) or a bare `<slug>`. It is **not** `[report:]`: a litreview has no
conclusion to rest on. `[litreview:program::it-aso-biodistribution]` means *"the landscape on IT ASO
biodistribution is as surveyed here."*

A report that carries `[litreview:X]` stops authoring its own `[lit:]` claims for any topic X covers
— it cites X's claims directly (`[lit:<id>]`, resolved across all claim modules as today). A report
that needs a literature fact *no* litreview covers is itself a signal that a litreview has a gap to
backfill.

**Importing a single edge-claim from an adjacent scope — flag-and-delegate, don't re-survey.** A
survey (or report) sometimes needs *one* claim that belongs to a neighbouring scope: a loss-side
litreview citing the over-side "headroom" datum as the upper edge of its band, say. Cite that one
`[lit:]` claim directly (bare node names resolve across all claim modules) **and name the
report/litreview that owns the adjacent scope, noting the boundary** — that is the whole obligation.
Importing an edge-claim does **not** oblige you to survey its home literature: the adjacent
overexpression corpus stays out of *this* survey's scope. The completeness critic is scoped to the
report/survey's own subject and **must not pull the adjacent literature into scope for a single
imported edge-claim** (see *Required: a completeness critic*) — flag-and-delegate is complete; a
re-survey of the neighbour is out of scope.

**There is no mechanical omissions gate.** Coverage — did the report lean on the convenient half of a
contested literature — is no longer a `sci`-enforced check against a hand-tagged set. It is carried by
two fresh-context critics, both reading the **screening log** rather than the author's memory: the
survey-side completeness critic (did the survey screen in and confront the disconfirmers?) and the
report-side completeness critic already mandated for the report phase (does the report's argument
engage the survey's screened-in evidence, or quietly skip it?). See [report.md](report.md) →
`[litreview:]` and [review-audit.md](review-audit.md). The report's only **mechanical** obligation to
the survey is the staleness pin below.

**Prerequisite — regenerate the full transitive grounding tree first.** Auditing a consuming report's
`[lit:]`/`[litreview:]` citations reads the **grounding reports of everything the report transitively
rests on** — every cited experiment's `grounding_report.json` *and* any `[report:]`/`[litreview:]`
dependency's grounding. Those `grounding_report.json` files are **gitignored / regenerable**, so a
fresh checkout has none: if *any* upstream is missing or stale, the report shows `BROKEN` on that
upstream and **downstream results are masked**. So before auditing the consumption: regenerate the
whole transitive tree (`pytest … --grounding-out …` for each cited experiment and each
`[report:]`/`[litreview:]` dep), then run `sci report`. (See [report.md](report.md) → *Running* and
[review-audit.md](review-audit.md) → the grounding-report regeneration note — a missing gitignored
grounding is a regenerate step, not a defect to backfill.)

## Staleness — the search protocol is the invalidation boundary

A report Y that cites `[litreview:X]` pins to **X's registered search method** — a sha over X's
`protocol.md` *Search queries* body plus its front-matter `as_of` and `sources`, recorded the way
`@reviewed(sha=)` pins a paper's text (see [report.md](report.md)). When X changes:

- **Blocking** (`stale-litreview`): X's registered search changed — a new/edited query, a refreshed
  `as_of` snapshot, or an added/dropped source. That is the one event that can invalidate Y's claim to
  rest on X's *coverage*: the field was re-searched and the map may have moved. Re-examine X, then
  re-pin.
- **Advisory completeness drift** (non-blocking, for the §3 / completeness pass): re-running the
  registered search surfaces new included-eligible papers — "the landscape grew; decide whether Y (and
  X) should now draw on any." This is surfaced for the review pass, never auto-BROKEN. **`sci` stays
  offline** — it never re-runs the search; a fresh `bib discover` re-enters via `--ingest-discover`,
  and a genuinely changed search bumps the protocol, which trips the blocking pin above.

Edits elsewhere in X — a reworded paragraph, a new non-pivotal claim, a screening decision — do not
touch the protocol pin, so a litreview can be updated often without a BROKEN cascade. This is a
genuinely new *kind* of staleness — **breadth drift** ("the search itself changed") — that the
recursive-GROUNDED `[report:]` check cannot express.

**Recording the pin.** Y records the pin it last re-examined X against in its YAML **front matter**,
one entry per cited litreview:

```yaml
---
title: "…"
litreview_pins:
  program::it-aso-biodistribution: "a1b2c3d4e5f6"
---
```

`sci report` computes the *current* pin for each `[litreview:]` edge and surfaces it: an unrecorded
pin is a non-blocking nudge (it prints the value to paste in, or `sci report --write-pins` writes it);
a recorded pin that no longer matches is the blocking `stale-litreview`. So the loop is: re-examine X
→ run `sci report` → copy (or `--write-pins`) the surfaced pin into `litreview_pins` → re-runs stay
green until X's search actually drifts. The pin is a sha over X's protocol `Search queries` + `as_of` +
`sources` — exactly the registered method whose change means the coverage may have moved.

## Keeping a citing report current — the cheap path

Steady-state maintenance of a report when its litreview moves is the **scoped, incremental update**
in [report-authoring.md](report-authoring.md) → *Keeping a report current* — **not** a from-scratch
regeneration (which is explicit-only, a debugging step). The cheap mechanism:

1. **Mechanical filter (free).** `sci litreview <slug> --delta <baseline>` emits the claim-set delta
   (added/removed claims, strength changes, retractions). If the delta intersects none of Y's surface
   — no claim Y cites, no paper Y references — and the protocol pin still matches, re-pin Y silently.
2. **Scoped delta-judge (cheap).** For a delta that *does* intersect Y, hand a **fresh-context**
   subagent the *claim-set* delta (not a prose diff — the prose diff is noise) plus Y's claim set and
   the paragraphs the tool flags as likely-affected, and ask the narrow question per delta item:
   *does this force a change to Y, or can Y stand (perhaps with a new citation)?* → `no-impact` /
   `add-citation` / `needs-rewrite` / `needs-rederivation`. Record the verdict pinned to the delta
   sha (like `sci judge`), so a green re-pin is *inspectable* ("judged immaterial by judge J on date
   D"), not "nobody looked".
3. **Escalate** only on a `needs-rewrite`/`needs-rederivation`, or when the **cumulative-drift cap**
   trips (too many cheap re-pins since Y's last from-scratch derivation, or too much of X's screened
   set has turned over) — the cheap path *defers* a from-scratch pass, it does not abolish it, or
   drift-by-a-thousand-cuts creeps back in.

The judge is adversarial and fresh-context (never Y's author): `no-impact` requires high confidence;
escalation is the safe default. It sees Y's *full* claim set so it can catch a contradiction in a
paragraph the topic-match missed.

## Gathering stays in bibliographer

Authoring a litreview *starts* with the same broad parallel sweep a report does — fan out research
subagents, each beginning with `bib discover` per the bibliographer
[literature-search protocol](../../bibliographer/references/literature-search.md), bank selectively
(responsive, or germane-and-highly-ranked — see [report-authoring.md](report-authoring.md) →
*Bank selectively*), require disconfirming evidence. The litreview adds only the **assessment** layer
on top of the gathered corpus: pre-register the protocol, **screen the full retrieved set** into
`screening.jsonl` (seed it with `--ingest-discover`, then decide each), organize by question, ground
the load-bearing assertions as `[lit:]` claims, write the controversies and gaps. Retrieval is
bibliographer; judgment is here.

After the sweep, run `sci coverage --query "<this survey's topic>"` — banked-but-unclaimed papers
**relevant to the topic** are the worklist of assertions the survey still owes. Use the **topic-scoped**
form: unscoped `sci coverage` is a coarse library-wide tally that returns *every* uncited paper, unranked
and polluted with off-topic noise — useless for a single sub-question (`--since` narrows by date but not
by topic). `--query` intersects the uncited set with a `bib query` and ranks it by relevance, which is
the actual per-survey worklist; a topic-scoped `bib query` does the same by hand. Treat it as one
mechanical *input* to completeness, not the whole leg — judging which uncited papers are load-bearing
stays the completeness critic's job.

## Restructuring vs. authoring from scratch

A litreview is often built not by a cold sweep but by **restructuring evidence already grounded
elsewhere** — lifting `[lit:]` claims a report previously authored into a dedicated litreview module,
so the survey formalizes knowledge the program already holds. This is the cheaper and more common
path; the broad sweep above is for genuinely new ground. Two things make it safe and honest:

- **Moving a claim is resolution-safe.** A bare `[lit:<node>]` resolves across *all* claim modules by
  node name, so moving a claim from `test_literature_<report>.py` into `test_litreview_<slug>.py`
  leaves every existing citation resolving — **provided the node name stays unique tree-wide**. Check
  for a node-name collision before moving (two `test_floor`s would become ambiguous) and rename if so;
  then re-run grounding so the moved claims land in the new module's grounding report.
- **Surface deferred coverage hits, don't bury them.** A restructure is deliberately *not* a fresh
  sweep — but `sci coverage` will still surface on-topic library papers no claim cites. Don't silently
  ignore them, and don't cold-sweep them either: list them in the **Gaps** section as candidates for a
  later assessment pass, so the deferral is *visible*. A restructured litreview that lists zero such
  candidates on a well-studied topic is suspiciously tidy.
- **Defer breadth, never the disconfirmer.** What a restructure may defer to Gaps is *coverage
  breadth* — the long tail of on-topic papers that add depth but not a new direction. It may **not**
  defer the **load-bearing on-topic disconfirmer**: a result that contradicts a surveyed claim is
  exactly what the completeness critic blocks on (below), so even a restructure owes enough searching
  to *find* the disconfirmers and confront them in the prose. The line is sharp: defer breadth, never
  the disconfirmer. A disconfirmer parked in Gaps as "coverage to revisit" is a blocking miss, not a
  deferral.

## Required: a completeness critic (fresh-context), distinct from the §3 pass

A litreview's whole value is *complete and fair*, and that is not the §3 prose↔claims check (which
verifies citations map to claims). Because the rigid section template is gone, **this critic — not the
tool — is the primary guardian of the per-theme supporting / contradicting / absent coverage**; the
audit's only hard structural check is that a gaps section exists. Before a litreview is final,
**delegate a completeness/fairness review to a fresh-context subagent** — the litreview author is
blind to the question they didn't ask.
Hand it `review.md`, the cited claims' statements/strengths/sources, the `prompt.md` (for sub-topic
coverage), and the topic-scoped `sci coverage --query "<this survey's topic>"` worklist (the
unscoped tally is too noisy to hand a critic — see *Gathering*). Prompt it adversarially:

> *What sub-question relevant to the scope got no coverage? What claim is characterized as settled
> that the field actually contests, or as fringe that is mainstream? What contradicting evidence to
> the surveyed claims is absent? Which banked papers state a load-bearing fact no claim captures?
> **Is the screening honest** — does `screening.jsonl` exclude any disconfirmer or binding bound on a
> thin pretext, and does every contested paper that survives appear as an `included` row the prose
> actually engages? **Is any claim about the literature itself** — "most-cited," "rarely replicated,"
> "understudied," "the consensus is" — **asserted as prose without a grounded `@kind("bibliometric")`
> claim behind it?** Such meta-claims cannot be quote-grounded, so they slip past the §3 check; flag
> any unbacked one (it must be measured via `cited_by()`/`metric()` or cut).*

It returns, per finding: the gap, why it matters, and what to add. **Blocking**: a missing
sub-question, a mischaracterized claim, a disconfirmer excluded-on-a-pretext (or screened-in but never
engaged), an ungrounded meta-claim about the literature. The objective stop condition is an empty
blocking list, judged by the reviewer. Run the §3 prose↔claims pass and a light voice check too (the
report-authoring rules apply, at the lower polish bar). The committed `protocol.md` + `screening.jsonl`
are this critic's primary evidence — it checks coverage against the screening log, not against the
author's memory.

**The disconfirmer is the hard floor; breadth is not.** The critic distinguishes a *coverage-breadth*
gap (an on-topic paper that would add depth — defer it to Gaps, non-blocking) from a *load-bearing
on-topic disconfirmer* (a result that contradicts a surveyed claim — **blocking**, even for a
restructure; see *Restructuring vs. authoring from scratch*). Defer breadth, never the disconfirmer.

**Scope the critic to the survey's own subject.** A litreview may *import* a single edge-claim from
an adjacent scope (e.g. a loss-side survey citing the over-side "headroom" datum as its upper band
edge) by **flag-and-delegate** — citing the report/litreview that owns the adjacent scope and noting
the boundary (see *Consumption*). That single imported edge-claim does **not** pull the adjacent
home-literature into this critic's sights: do not flag the survey for "incomplete coverage" of a
neighbouring topic it deliberately delegated. The critic's coverage bar is this survey's own
sub-question, not the union of every scope it touches at the edges.

## `sci litreview` — build / audit / ingest / index / render / delta

```bash
sci new-litreview <slug>                            # SCAFFOLD: folder + review.md + protocol.md + screening.jsonl + prompt.md + module
sci litreview <review.md> [--home H] [--json]       # AUDIT: [lit:] backed; gaps present; protocol + screening committed; cited papers screened-in
sci litreview <review.md> --ingest-discover d.json  # seed screening.jsonl from `bib discover --json` (decision unset, de-duped by id)
sci litreview <review.md> --render out.pdf [--to pdf|html|docx]
sci litreview <review.md> --trace                   # litreview -> each [lit:] claim -> paper
sci litreview <review.md> --delta base.json [--json]  # claim-set delta vs a baseline (the cheap-update filter)
sci litreview <review.md> --index                   # upsert into the store as kind=litreview
```

**Start with `sci new-litreview <slug>`** — it stubs the folder, `review.md`, `protocol.md`,
`screening.jsonl`, `prompt.md`, and the **correctly-named** claim module
(`test_litreview_<slug-underscored>.py`), removing the highest-risk manual steps. Then pre-register
the search in `protocol.md`, `--ingest-discover` your `bib discover` output and screen each candidate,
and author the survey. On the report side, `sci report --write-pins` writes the surfaced
`litreview_pins` into the report's front matter automatically (no manual paste).

The `--delta` baseline is just an older copy of the grounding report — the git part stays yours:
`git show <ref>:program/analysis/grounding_report.json > base.json`, then
`sci litreview <review.md> --delta base.json`. It reports the claims that were added/removed or
drifted, the worklist the cheap-update delta-judge weighs (see *Keeping a citing report current*).

The **audit** validates mechanically (a failure → `BROKEN`, exit 1): every `[lit:]` claim resolves
and is `backed` by the `lit_verdict` rule in [report.md](report.md); a **gaps** section is present;
the **protocol** is committed and complete (`missing-protocol` / `missing-protocol-field`); the
**screening log** parses and accounts for every candidate (`malformed-screening-row`,
`excluded-without-reason`); and the **coverage cross-check** holds — every `[lit:]`-cited paper is an
`included` screening row (`cited-paper-unscreened`), with `included-but-uncited` as an advisory. It
derives the PRISMA funnel, warns when the grounding report is older than the claim module (re-run with
`--grounding-out`), and **suppresses the report-tuned `weak-load-bearing` advisory** (noise for a
conclusion-free survey). It does **not** re-run the claims suite or call any search API — it reads the
recorded grounding report and the committed artifacts only. **Whether the screening is *honest* and
coverage is *fair* — beyond the mechanical cross-check — is the completeness critic, not the tool**
(see above).

The `stale-litreview` protocol pin lives in the **report** audit (`sci report`), since it is a
property of the *consuming* report — see *Consumption* / *Staleness*.

## Maintaining (for agents working ON scientist)

The litreview parse/audit/index lives in `scientist/provenance/litreview.py`, store-free at its core
like `report.py`/`trace.py` (PyYAML + stdlib). It **reuses** `report.py`'s `parse_report`,
`index_claims`, `lit_verdict`, and `render_markdown` — a litreview is `[lit:]`-only report-shaped
Markdown, so do not re-implement citation parsing or the `[lit:]` verdict. The protocol parsing
(`parse_protocol`, `litreview_protocol_pin_sha`) lives in `report.py` (the store-free base) so both
the survey audit and the consuming report's pin can read it; protocol/screening **validation**, the
funnel, the coverage cross-check, and `--ingest-discover` are in `litreview.py`. The `[litreview:]`
citation regex and the protocol-keyed `stale-litreview` pin are in `report.py`'s report audit. The
`kind=litreview` store card is `_meta.litreview_card_markdown` + `_store.upsert_litreview`, mirroring
the report card. Keep the `[lit:]` rule and `claim_id` format identical across all of this and §3 — a
drift in one is a drift in all.
