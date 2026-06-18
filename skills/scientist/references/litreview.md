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
   set a downstream report's citations are checked *against*. A report that cites 12 of a litreview's
   assertions while ignoring the contested ones is now visibly doing so — see *Consumption* below.
3. **It centralizes reuse and staleness.** Many reports lean on the same biology. Surveyed once,
   re-swept once: the reports that cite a litreview inherit its re-derivation instead of each
   re-sweeping (see *Staleness*).

A litreview is held to a **high completeness/fairness bar but a lower narrative-polish bar** than a
report. It is more structured catalog than flowing essay; do not spend effort on rhetorical shape
that you would spend on a report.

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
  review.md       ← the survey (this artifact)
  prompt.md       ← the conclusion-free generation brief (re-derivable; see report-authoring.md)
program/claims/test_litreview_<slug>.py   ← its [lit:] claim module
```

The claim-module name is **`test_litreview_<slug>.py` with the slug's hyphens mapped to
underscores** (a Python module name can't carry a hyphen — litreview `it-aso-biodistribution` →
`program/claims/test_litreview_it_aso_biodistribution.py`). The omissions audit and the
must-confront set are keyed off this convention, so follow it exactly.

`slug` is the litreview-folder name. One litreview per **major sub-question**, not one giant program
review — match the supporting-report decomposition [report-authoring.md](report-authoring.md) already
encourages (e.g. `it-aso-biodistribution`, `ube3a-dosage-biology`). Per-question scope also *bounds*
the completeness problem: "complete on IT biodistribution" is tractable; "complete on everything" is
not. A report cites the 1–3 litreviews its argument spans.

## Structure of `review.md`

Front matter as for a report (`title`, `date`, `classification`), then:

- **Question & scope** — the sub-question this maps and its boundaries. One short paragraph.
- **Body, organized by sub-question** — under each, the assessed landscape: the assertions the field
  makes, each a grounded `[lit:]` claim, with strength, who, and how direct. For each sub-question,
  state the evidence that **supports**, that **contradicts**, that is **equivocal**, and — explicitly
  — where there is **no evidence**. A sub-question with only supporting claims is a red flag for a
  steered survey.
- **Controversies / unresolved** — where the field genuinely disagrees, presented side by side
  ("Lab A reports X; Lab B's model implies not-X under condition C"), each side its own `[lit:]`
  claim. This is the litreview's signature section: do **not** flatten a disagreement into whichever
  side you list first. A claim and its counter-claim should reference each other.
- **Gaps / open questions** (mandatory) — what the literature does *not* settle: unmeasured regimes,
  questions answered only by analogy, single-lab results never replicated. This is the litreview's
  analog of a report's assumptions section, and the first place incompleteness shows up *by its
  absence*. A litreview with no gaps section is not done.

No conclusion/recommendation section.

## The claim backbone — `[lit:]` as usual, plus a must-confront tag

Every load-bearing assertion is a grounded `[lit:]` claim authored exactly as in
[report.md](report.md) → *Authoring and reviewing a literature claim*: `source(quote=, paraphrase=)`,
the locator ladder → strength ceiling, the caller-records `sci judge` support loop, independence via
`group=`, primary/secondary. None of that changes. The litreview's claim module is just where those
claims now live (per-litreview, so parallel authors don't collide), instead of a per-report
`test_literature_<slug>.py`.

**The must-confront subset.** The litreview marks the claims that *any* honest report in this area
must reckon with — the **pivotal, contested, or disconfirming** ones — with a `@must_confront`
decorator on the claim:

```python
@kind("literature")
@strength("moderate")
@must_confront("a tolerated ~50% prenatal loss bounds the safety ceiling — any dosing "
               "report must address it")
def test_floor_developmentally_gated():
    source(citekey="silvasantos2015", quote="…", paraphrase="…")
```

The `reason` is one line on *why* a report must address it. This tag is the litreview's most
important neutral judgment: it is made **before and independent of any thesis**, which is exactly
what makes the obligation trustworthy. Mark a claim must-confront when ignoring it would let a report
reach a tidy conclusion the full evidence does not support — disconfirmers, the binding bound, the
genuine controversy. Do **not** mark the whole claim set; the obligation is the *contested core*, not
every on-topic fact. The set surfaces in the grounding report (`must_confront: {reason}`) and drives
both the omissions audit and staleness.

## Consumption — `[litreview:<id>]` and the omissions audit

A report grounds on a litreview with `[litreview:<id>]`, where `<id>` is `<exp-or-program>::<slug>`
(almost always `program::<slug>`) or a bare `<slug>`. It is **not** `[report:]`: a litreview has no
conclusion to rest on. `[litreview:program::it-aso-biodistribution]` means *"the landscape on IT ASO
biodistribution is as surveyed here."*

A report that carries `[litreview:X]` stops authoring its own `[lit:]` claims for any topic X covers
— it cites X's claims directly (`[lit:<id>]`, resolved across all claim modules as today). A report
that needs a literature fact *no* litreview covers is itself a signal that a litreview has a gap to
backfill.

**The omissions audit (blocking).** When a report cites `[litreview:X]`, `sci report` computes X's
**must-confront** claims and checks the report **addresses each one**: either it cites the claim
(`[lit:<id>]` anywhere in the report), or it carries an explicit waiver. An unaddressed must-confront
claim is a blocking `unaddressed-must-confront` finding. The audit is scoped to the must-confront set
*by design* — requiring a report to cite *every* claim of a broad survey would force irrelevant
breadth and train reflexive waivers; the contested core is the obligation, and it is small.

**Waiver syntax.** A one-line waiver in the report's assumptions/weak-support section, naming the
claim and the reason it does not bear on this report's argument:

```markdown
- [litreview-waive:test_cortex_critical_period_dependent] out of scope — this report addresses the
  spinal route only, where cortical critical-period dependence does not apply.
```

A waiver is *address-or-account*, not a silencer: it must state why the contested claim does not
change this report's conclusion. The §3 fresh-context pass adjudicates whether a waiver is honest
(see [report-authoring.md](report-authoring.md) → *Required: the §3 pass*).

## Staleness — the must-confront set is the invalidation boundary

A report Y that cites `[litreview:X]` pins to **X's must-confront set + the X-claims Y cites** (a sha
over those, recorded the way `@reviewed(sha=)` pins a paper's text — see [report.md](report.md)).
When X changes:

- **Blocking** (`stale-litreview`): the must-confront set gains or loses a claim, or a claim Y cites
  drifts (strength/paraphrase change, retraction). These are the only changes that can break Y's
  obligation — the contract changed, or a backing Y leaned on moved. Re-pin after addressing.
- **Advisory** (non-blocking, for the §3 / completeness pass): X gained *non*-must-confront claims
  since Y pinned it — "the landscape grew; decide whether Y should now draw on any." Never auto-BROKEN.

Cosmetic edits to X and irrelevant new claims do not touch Y, so a litreview can be updated often
without a BROKEN cascade — which is the point: frequent re-sweeping must stay cheap. This is a
genuinely new *kind* of staleness — **completeness drift** ("the landscape grew past what the report
accounted for") — that the recursive-GROUNDED `[report:]` check cannot express. The must-confront tag
does double duty: it scopes the omissions audit **and** the staleness boundary.

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
pin is a non-blocking nudge (it prints the value to paste in); a recorded pin that no longer matches
is the blocking `stale-litreview`. So the loop is: address the must-confront set → run `sci report`
→ copy the surfaced pin into `litreview_pins` → re-runs stay green until X actually drifts. The pin
is a sha over X's must-confront claim-ids plus the drift signatures (outcome/strength/quote/
paraphrase/retraction) of the X-claims Y cites — exactly the two things that can break Y's
obligation.

## Keeping a citing report current — the cheap path

Steady-state maintenance of a report when its litreview moves is the **scoped, incremental update**
in [report-authoring.md](report-authoring.md) → *Keeping a report current* — **not** a from-scratch
regeneration (which is explicit-only, a debugging step). The cheap mechanism:

1. **Mechanical filter (free).** `sci litreview <slug> --delta <since>` emits the claim-set delta
   (added/removed must-confront, strength changes, retractions, added non-must-confront). If the
   delta intersects none of Y's surface — no must-confront change, no claim Y cites, no paper Y
   references — re-pin Y silently.
2. **Scoped delta-judge (cheap).** For a delta that *does* intersect Y, hand a **fresh-context**
   subagent the *claim-set* delta (not a prose diff — the prose diff is noise) plus Y's claim set and
   the paragraphs the tool flags as likely-affected, and ask the narrow question per delta item:
   *does this force a change to Y, or can Y stand (perhaps with a new citation)?* → `no-impact` /
   `add-citation` / `needs-rewrite` / `needs-rederivation`. Record the verdict pinned to the delta
   sha (like `sci judge`), so a green re-pin is *inspectable* ("judged immaterial by judge J on date
   D"), not "nobody looked".
3. **Escalate** only on a `needs-rewrite`/`needs-rederivation`, or when the **cumulative-drift cap**
   trips (too many cheap re-pins since Y's last from-scratch derivation, or too much of X's
   must-confront set has turned over) — the cheap path *defers* a from-scratch pass, it does not
   abolish it, or drift-by-a-thousand-cuts creeps back in.

The judge is adversarial and fresh-context (never Y's author): `no-impact` requires high confidence;
escalation is the safe default. It sees Y's *full* claim set so it can catch a contradiction in a
paragraph the topic-match missed.

## Gathering stays in bibliographer

Authoring a litreview *starts* with the same broad parallel sweep a report does — fan out research
subagents, each beginning with `bib discover` per the bibliographer
[literature-search protocol](../../bibliographer/references/literature-search.md), bank selectively
(responsive, or germane-and-highly-ranked — see [report-authoring.md](report-authoring.md) →
*Bank selectively*), require disconfirming evidence. The litreview adds only the **assessment** layer
on top of the gathered corpus: organize by question, ground the load-bearing assertions as `[lit:]`
claims, mark the must-confront set, write the controversies and gaps. Retrieval is bibliographer;
judgment is here.

After the sweep, run `sci coverage --since <date>` — banked-but-unclaimed papers are the worklist of
assertions the survey still owes, and one mechanical leg of completeness.

## Required: a completeness critic (fresh-context), distinct from the §3 pass

A litreview's whole value is *complete and fair*, and that is not the §3 prose↔claims check (which
verifies citations map to claims). Before a litreview is final, **delegate a completeness/fairness
review to a fresh-context subagent** — the litreview author is blind to the question they didn't ask.
Hand it `review.md`, the cited claims' statements/strengths/sources, the `prompt.md` (for sub-topic
coverage), and the `sci coverage --since` output. Prompt it adversarially:

> *What sub-question relevant to the scope got no coverage? What claim is characterized as settled
> that the field actually contests, or as fringe that is mainstream? What contradicting evidence to
> the surveyed claims is absent? Which banked papers state a load-bearing fact no claim captures? Is
> the must-confront set complete — is any disconfirmer or binding bound left untagged?*

It returns, per finding: the gap, why it matters, and what to add. **Blocking**: a missing
sub-question, a mischaracterized claim, an untagged disconfirmer that should be must-confront. The
objective stop condition is an empty blocking list, judged by the reviewer. Run the §3 prose↔claims
pass and a light voice check too (the report-authoring rules apply, at the lower polish bar).

## `sci litreview` — build / audit / index / render / delta

```bash
sci litreview <review.md> [--home H] [--json]      # AUDIT: every [lit:] claim backed; structure present
sci litreview <review.md> --must-confront [--json] # list the must-confront obligation set
sci litreview <review.md> --render out.pdf [--to pdf|html|docx]
sci litreview <review.md> --trace                  # litreview -> each [lit:] claim -> paper
sci litreview <review.md> --delta <since> [--json] # [planned] claim-set delta (the cheap-update filter)
sci litreview <review.md> --index                  # [planned] upsert into the store as kind=litreview
```

> **Build status.** Live: the `@must_confront` marker, the `[litreview:]` omissions audit +
> `[litreview-waive:]` and the `stale-litreview` staleness pin (`litreview_pins` front matter) in
> `sci report`, and `sci litreview` audit / `--must-confront` / `--render` / `--trace`. **Planned**
> (designed here, not yet wired): the `--delta` cheap-update filter and the `kind=litreview` store
> card (`--index`). The discipline below is the target spec.

The **audit** validates mechanically (a failure → `BROKEN`, exit 1): every `[lit:]` claim resolves
and is `backed` by the `lit_verdict` rule in [report.md](report.md); the structure carries a gaps
section and (when there are competing claims) a controversies section; and the must-confront set is
non-empty (a survey that obliges a citing report to confront *nothing* is almost always
under-assessed — a warning, not a block, when the topic is genuinely uncontested). It does **not**
re-run the claims suite (that produced the grounding report); like `sci report` it reads the recorded
grounding report.

The `[litreview:]` omissions check and `stale-litreview` pin live in the **report** audit
(`sci report`), since they are properties of the *consuming* report — see *Consumption* / *Staleness*.

## Maintaining (for agents working ON scientist)

The litreview parse/audit/index lives in `scientist/provenance/litreview.py`, store-free at its core
like `report.py`/`trace.py` (PyYAML + stdlib). It **reuses** `report.py`'s `parse_report`,
`index_claims`, `lit_verdict`, and `render_markdown` — a litreview is `[lit:]`-only report-shaped
Markdown, so do not re-implement citation parsing or the `[lit:]` verdict. The `@must_confront`
decorator and its flow into `grounding_report.json` live in the grounding layer
(`scientist/grounding/literature.py` + `plugin.py`); the `[litreview:]` citation regex, the omissions
audit, and the `stale-litreview` pin are added to `report.py`'s report audit. The `kind=litreview`
store card is `_meta.litreview_card_markdown` + `_store.upsert_litreview`, mirroring the report card.
Keep the `[lit:]` rule and `claim_id` format identical across all of this and §3 — a drift in one is a
drift in all.
