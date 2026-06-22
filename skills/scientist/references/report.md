# Reports — `claims → report` (mechanics)

> Authoring/voice/research discipline lives in [report-authoring.md](report-authoring.md) — load it when drafting prose, not when running `sci report`.

The terminal phase of the pipeline `raw → data → analysis → claims → **report**`. Where a
*claim* is one machine-checkable assertion, a **report** is a human-facing narrative built
*from* claims: it collects grounded claims (often fanning in across experiments), arranges
them into a coherent argument, and embeds figures/tables to make a point. It is for humans
— readable, concise, compelling — but holds the same grounding discipline as the rest of
the pipeline:

> **No quantitative prose without a backing**, and — as in §3 — the **sole accepted backing
> is an *existing* grounded `kind=claim`** (cited `[claim:<id>]`). A sha-pinned `analysis/`
> artifact is grounded *provenance* but not *judged* evidence (no outcome/strength), so it
> never backs a prose result on its own; it is what an embedded *figure/table* points at,
> and what the claim itself cites. To assert something new, write the claim first — reports
> never re-litigate grounding.

`sci report` mechanizes the parts that are genuinely mechanical (citation + artifact
resolution, render). The *semantic* judgment — "is every quantitative sentence actually
cited / on-topic / not over-reaching" — stays the **§3 prose↔claims semantic pass** of the
authoring agent (see [review-audit.md](review-audit.md) → *Prose ↔ claims check*); it is
**not** a regex assertion-detector. The authoring-side discipline for that pass (and the
voice/tone review) lives in [report-authoring.md](report-authoring.md).

## Authoring model

Reports are **git-diffable Markdown** with inline `[claim:<id>]` citations and Markdown
image embeds, in BOTH scopes:

- **cross-experiment** reports under `program/reports/<slug>/report.md` — cite claims from
  *any* experiment (the program-wide argument);
- **per-experiment summaries** under `<exp>/reports/<slug>/report.md` — the experiment's
  own story.

### Citations — reuse the §3 `[claim:<id>]` syntax (do not invent another)

Every asserted *result* carries the same inline citation §3 defined for `README.md` /
`reports/*.md`:

```markdown
Sustained knockdown of 53% at the top dose [claim:test_knockdown].
```

`<id>` is the stable `claim_id` `<exp>::<test-file>::<node>` (e.g.
`K1-230101::test_kd.py::test_knockdown`) **or** its trailing node name (`test_knockdown`).
A bare node name that is ambiguous across experiments (two define a `test_knockdown`) must
be qualified to the full id — `sci report` flags the ambiguity.

Pull the claims to cite with `sci query "<topic>" --kind claim`, `sci list --kind claim
--experiment <exp> --json`, or `<exp>/analysis/grounding_report.json` (each claim: `{id,
statement, outcome, strength, kind}`) — the identical sources §3 / `sci trace` use.

Two more citation forms, same audit:

- **`[report:<id>]`** — ground on another (supporting) report. `<id>` is
  `<exp-or-program>::<slug>` (e.g. `program::target-dosage-window`) or a bare `<slug>`.
  `sci report` resolves it and requires the cited report to be itself `GROUNDED` (checked
  recursively) — so it is `backed` only if that report holds, `missing` if it doesn't exist,
  `weak-backing` if it is `BROKEN`. Use it to build a report on a supporting report.
  **These citations *are* the report dependency graph** — the live, audited edges. Don't keep a
  separate prose map of which report depends on which (in a README, a program brief, anywhere):
  a hand-maintained copy drifts from the citations and the audit can't catch the drift. To see
  the graph, read a report's abstract for what it establishes and run `sci trace <report.md>`
  to walk what it rests on.
- **`[lit:<id>]`** — ground a *third-party* fact on a paper in the bibliographer library. A
  literature claim is a pytest spec in `program/claims/test_literature.py` (`@kind("literature")`)
  that calls `source(citekey, quote=…, paraphrase=…, test=…, system=…, primary=…, group=…)` — and
  `converge(...)` for a multi-source fact. The spec **fails if the verbatim quote is not in the
  cited paper's stored text** (read from the LOCAL library DuckDB — keyless, offline; only the
  library's semantic *query* embeds), and **fails outright if the cited paper is marked
  retracted** (OpenAlex / Retraction Watch, as of the last `bib add`/enrich — a claim must not
  rest on retracted work; pass `allow_retracted=True` only to discuss the retraction itself).
  `[lit:program::test_literature.py::<node>]` is `backed`
  only if that quote check passed **and** the claim's *support* is confirmed — either by a
  re-runnable machine verdict (`source(paraphrase=…)`, recorded via `sci judge --record`) or a hand-stamped
  `@reviewed` (see *The machine support judge* below); a *weak* but supported claim still backs
  (single/suggestive evidence is legitimately weak). It is **second-class to `[claim:]`** — rendered as a distinct
  "Literature" footnote and reported on its own audit line — and must never read as data-grounded.
  The papers a report cites `[lit:]` are **auto-collected into a `# References` section** the
  renderer appends at the end — one entry per paper (`Authors (Year). *Title*. Venue. <DOI>`),
  built from the bibliographic fields each source snapshotted at grounding time, sorted by
  author. Nothing to hand-author; the per-page footnote is the inline pointer, the References
  list is the works-cited. (A source predating the `authors_text`/`venue` snapshot falls back to
  the citekey-derived surname; re-running the literature claims regenerates it with the full
  fields, since `source(...)` snapshots them from the library at grounding time.)
  - **`[lit:<citekey>::<slug>]` may instead resolve to a pre-extracted *paper-claim*** (a 2-part
    id, vs a literature claim's 3-part `<exp>::<file>::<node>`). When no internal literature claim
    matches, the audit looks the id up in the per-paper JSONL store (`<home>/paper-claims/`,
    `sci paper-claims`). A paper-claim is **attributed, not grounded** — pinned to what the paper
    *says* — so it backs the cite (verdict `attributed`, rendered "Author year report: …") iff it
    exists, is `kind="attributed"`, and carries an `evidence_sha`; the quote-integrity re-check is
    `sci paper-claims verify`. See [references/paper-claims.md](paper-claims.md). This front-loads
    the per-citation re-read: extract a paper's claim set once, cite it from many reports.
- **Bare references** (no `[lit:]`) remain fine for *background*: refer to the paper inline by
  author-year ("(Monine et al. 2021)"). If you need background papers *listed*, author your own
  **References** (or **Bibliography** / **Works cited**) heading — that **takes over the whole
  list** (the auto-generation defers to it, so include the `[lit:]` papers there too). Author
  the entries as an ordinary Markdown list; the `1.`/`2.` markers are fine — the renderer drops
  the numbers so it renders unnumbered (reference numbers aren't cross-referenced in the prose
  and would clash with the per-page footnote numbers). Promote a citation to `[lit:]` when the
  fact is **load-bearing** — when the argument leans on it, make it quote-pinned and auditable.

#### Authoring and reviewing a literature claim

A `[lit:]` claim grounds *attribution faithfulness* (the paper really says this), not truth —
you cite what the field reports. Two layers:

1. **Quote (tool, every audit).** Find a *verbatim* phrase in the paper that states the fact and
   pass it as `quote=`. Matching folds Unicode dashes/whitespace, but the words must be exact;
   use `bib show`/`bib query`/`sci`-side reads to copy a real phrase. A short, specific sentence
   beats a long one (less to break). One paper can back several quotes; it appears once per
   endnote.
2. **Support.** Whether the quote actually *supports the paraphrase* — read in context, no
   quote-mining — is the load-bearing judgment. Record it ONE of two ways:
   - **Machine-judged (preferred, executable).** Add `paraphrase="…"` to `source()` (alongside
     `quote=`). The support judgment becomes a *re-runnable, cache-pinned* entailment check —
     "does quote Q fairly support paraphrase P?" — over two short strings (NOT "read the whole
     paper and decide if it supports X"). The judge is **you, the orchestrating agent** (ideally a
     fresh-context judge subagent you spawn — see **The machine support judge** below); the tool
     only lists the work (`sci judge --list`) and records your verdict (`sci judge --record`). A
     cached `unsupported` verdict *fails the claim* on every subsequent run, so quote-mining no
     longer survives a re-audit.
   - **Hand-stamped (legacy).** Stamp `@reviewed(date=…, by=…, support=…, …)` after reading the
     cited span; `support=False` ⇒ the claim is broken. The original path, unchanged — the right
     choice when no judge is configured. Watch for quote-mining (a supportive sentence whose
     surrounding text qualifies it).

   Either way, also judge — and record via `@reviewed`, which co-exists with `paraphrase=`:
   - **primary** — is this the *primary* source, or a relay? (The telephone problem: if A says
     "B showed X", cite **B**; verify A isn't just repeating B.) This is not optional bookkeeping:
     a `primary=False` source is a signal to go *get* B — track down B's paper, add it to the
     library, read it, and re-ground the quote on B. Mark the relay `primary=False` only when B is
     genuinely unobtainable; the default outcome of finding a relay is to replace it with the
     primary source, not to ground on the relay. (See *Always cite the primary source* in
     [report-authoring.md](report-authoring.md) — the same rule applies whether the relay is a
     review paper or a Kicho-authored report.)
   - **independence** — set `group=` so co-lab / shared-model papers count as ONE group; the
     endnote's "N independent" comes from distinct groups.

**Strength is evidential weight, judged — not retrieval location.** `@strength`: **strong** =
independent (≥2 distinct groups) + direct + primary; **moderate** = a single group's direct,
primary, unreplicated result; **weak** = single + *suggestive* (a related experiment that only
implies the claim), or secondary, or contested. Keep author seniority / citation count as
*context* in `note`/`caveats`, **never** as a scoring input — weighting by prestige is
argument-from-authority and the machinery would launder it as rigor. `source()` records the
cited paper's **credibility markers** from the library automatically — venue legitimacy (DOAJ;
journal vs. preprint), citation impact (FWCI / percentile / journal h-index), and the retraction
flag — and they surface on the endnote as reader context. They are exactly the "context, not
scoring" signals above: shown so a reader can weigh the source, and deliberately **never** fed
into `@strength`, `support`, or any quality gate (an impact gate would also push toward the
high-profile *review* over the lower-cited *primary* paper — the opposite of the rule above).

**A weak literature claim still backs — cite the weak disconfirmer, do not drop it.** Strength
for a `[lit:]` claim is *descriptive*, not a gate: unlike a data `[claim:]` (which must be
moderate-or-strong to back), a `weak` literature claim that is reviewed, supported, and
quote-pinned **backs its citation** and renders as an appropriately weak endnote (`lit_verdict`
blocks only on a failed quote, a non-literature claim, an un-reviewed/un-judged claim, a stale
verdict, an unsupported one, or a strength that exceeds the locator ceiling — never on `weak`
itself). So single/suggestive/secondary evidence is
*citable*, not unusable. This matters most for **disconfirming** evidence, which is often
legitimately weak (one contrary case, an inferential tolerance argument): write it as a `weak`
`[lit:]` claim and cite it, rather than demoting it to a bare reference or omitting it. Dropping a
weak-but-real disconfirmer to keep the claim set "clean" is the exact failure the
disconfirming-evidence requirement exists to prevent.

**When the library has only an abstract or a title — and you need the body, pause and ask.**
Many papers are paywalled, so the bibliographer ingests only their metadata + abstract (and the
oldest/most-locked, just the title) — `bib fetch` can't get an open-access PDF. You can still
quote a real sentence from an abstract, and a title sometimes carries the headline result (mark
those `suggestive`), but you **cannot pin the specific finding** (an exact number, the actual
mechanism, the method that makes it *direct*) that lives only in the body. So: check each cited
paper's available text (a few hundred chars = title only; ~1–3k = abstract; more = full text). If
a paper is abstract-/title-only **and it is load-bearing** — the claim's strength or a specific
number genuinely depends on the body you don't have — **stop and ask the user for the full text**
(they may have institutional access: `bib fetch <key> --pdf <downloaded.pdf>`). Don't silently
ground a load-bearing claim on a title gloss, and don't silently drop it either; surface it. For
*corroborating* abstract-only sources, grounding on the abstract as `suggestive` is fine — just
say so in the `@reviewed` note. Record which cited papers are abstract-/title-only so the gap is
visible, not buried.

#### The machine support judge — an executable, re-runnable support verdict

The legacy `@reviewed(support=True)` is a trusted, hand-stamped boolean the audit *never
re-checks*: it re-verifies the verbatim quote and the paper-text sha every run, but it never
re-examines whether the paraphrase is a fair reading of the quote. That is the weak link
(quote-mining survives a green audit). `source(paraphrase=…)` closes it by making the support
judgment **executable**: the narrow, local question "does quote Q entail paraphrase P?" gets a
recorded, re-runnable verdict — and the claims suite asserts on that *cached* verdict.

**Who judges: you, the orchestrating agent — not the tool.** There is **no model inside `sci`** (or
`bib`). You are already an LLM that read the paper, so the tool re-owning a model and re-judging
would be backwards. Instead the loop is *list → judge → record*, and the judging is done **by a
fresh-context judge subagent you spawn** — independence matters: don't let the context that *wrote*
the paraphrase grade it. The tool's only jobs are deterministic: surface the work and record/verify
the verdict.

- **`sci judge --list`** emits the worklist of `[lit:]` sources whose verdict is **missing or
  stale**, each as `{claim_id, citekey, tier, span_text, paraphrase, evidence_sha}` — `span_text`
  is the verbatim quote (tier 1) or the resolved chunk text (tier 2). Spawn a fresh subagent, hand
  it `span_text` + `paraphrase`, and ask the one narrow question: *does the span fairly support the
  paraphrase?* → `{supported, rationale}`.
- **`sci judge --record <file|->`** ingests those verdicts `{citekey, paraphrase, supported,
  rationale}` (echo the worklist's `evidence_sha` back for an extra stale-span guard) and writes
  them to the cache. The pin is **recomputed by the tool** from the report's current span — a caller
  can't record a verdict against a stale or wrong span; a record whose `(citekey, paraphrase)` (or
  echoed `evidence_sha`) no longer resolves is rejected. `--judge-id` stamps *who* judged.

**The determinism discipline (non-negotiable).** The claims suite is a re-runnable, offline,
deterministic pytest suite — that is the whole system's value, and it is **unchanged**: no model is
ever called on the pytest path. `source()` and the report audit only ever *read* the verdict cache
— a plain JSON file, a pure function of bytes, no key, no network. Only `sci judge --record` writes
it. A `sci report` audit and a normal grounding run stay free and deterministic.

**The cache + its key.** Each verdict answers one entailment question, keyed by the pair
`(evidence_sha, paraphrase)` and stored in `lit_judgments.json` next to the grounding report (a
machine-owned artifact, like `grounding_report.json` — never hand-edited): `{supported, judge_id,
timestamp, rationale, …}`. `evidence_sha` is the sha of the **folded** span (the same
normalization quote-matching uses — NFKC, Unicode-dash fold, strip Markdown `*`/`_`, collapse
whitespace), so markdown / whitespace / dash variants of one sentence map to ONE identity → ONE
shared verdict; the same paper sentence cited from two modules can't stale itself. The verdict is
**inspectable** — a green claim is "judged Q⊢P, by this judge, on this date, with this rationale",
not an opaque "the LLM said yes". `judge_id` is **metadata, not part of the key**: a verdict
produced by a different judge subagent is still valid, so swapping who judges does not
mass-invalidate the cache. `sci check` runs a cross-module **divergence lint** that warns (never
fails) when one `(citekey, paraphrase)` is grounded on *genuinely different* spans across modules —
reconcile those to a single canonical quote.

> **Upgrade note (one-time re-judge).** The cache identity changed from the *raw* span sha to the
> *folded* span sha. Existing `lit_judgments.json` entries keyed by the old raw sha show as
> `stale`/`needs-judgment` once and must be re-run: `sci judge --list` → judge → `sci judge
> --record`. The verdicts themselves are unchanged (same span ⊢ paraphrase support) — only the key
> moved — so the re-judge is mechanical. There is no in-place migration: the cache stores only the
> sha, not the raw span preimage, so the old key can't be re-folded; a clean re-judge is the
> pragmatic path.

**The locator ladder → strength.** *How precisely* a source locates its supporting text caps the
claim's strength (the audit enforces the ceiling), so a paragraph-spanning gloss can't be sold as
a pinpoint quote:

| tier | `source(...)` | the judge reads | max `@strength` |
|---|---|---|---|
| 1 | `quote=` + `paraphrase=` | the verbatim quote (two short snippets) | `strong` |
| 2 | `chunk=` + `paraphrase=` | one libkit chunk span (`bib query` returns chunk ids) | `moderate` |
| 3 | `paraphrase=` only | the whole document (costly, high-variance, least auditable) | `weak` |

Tier 1 is the default; reach for tier 2 only for a fact that genuinely spans a paragraph with no
single quotable sentence. A claim's ceiling is that of its **weakest-located** source; exceeding
it is a blocking `over-strength` finding (strengthen the locator, or lower `@strength`).

**Staleness — re-judge on drift.** The verdict is invalidated the moment `(quote_sha | paraphrase)`
drifts: a quote edit (the cited paper's text changed, or you tightened the quote) flips the
citation to `stale-judgment` and a paraphrase edit to `needs-judgment` (both blocking) — re-`list`,
re-judge, re-`record`, re-run the suite. This is the literature analogue of `stale-review`, but
recomputed every run instead of trusted once. (Note the key no longer pins a model id — the verdict
is the agent's judgment, recorded; there is no model to upgrade.)

**Opt-in and additive.** A source that adds `paraphrase=` is machine-judged; existing `quote=` +
`@reviewed(support=…)` claims keep working unchanged. Until a verdict is recorded, the source stays
`needs-judgment` (non-blocking until the citation needs to back) — never a crash. The cache the
pytest path reads defaults to `<grounding-out>/lit_judgments.json` (next to each grounding report);
override with `--cache`.

**The list → judge → record loop.**

```
# 1. run the claims suite to (re)emit the grounding report (records paraphrase + the span)
uv run --with-editable <scientist> --with libkit pytest program/claims/ --grounding-out program/analysis
# 2. surface the missing/stale support verdicts to judge
sci judge --list --home <data> --json > worklist.json
# 3. YOU judge each {span_text, paraphrase} — ideally via a fresh-context judge subagent for
#    independence — and write {citekey, paraphrase, supported, rationale} records, then record them:
sci judge --record verdicts.json --home <data> --judge-id <who>
# 4. re-run the suite: source() now asserts on the cached verdicts (unsupported → red)
uv run --with-editable <scientist> --with libkit pytest program/claims/ --grounding-out program/analysis
```

**Running.** Generating the literature grounding report needs libkit + `BIBLIOGRAPHER_HOME`
(source `~/.env`): `uv run --with-editable <scientist> --with libkit pytest program/claims/
--grounding-out program/analysis`. The report **audit/render** then read that JSON and need
*neither* (data-claim audits stay light). **Re-validation:** each `source()` sha-pins the cited
paper's text as a provenance input; pin the review to those texts by stamping
`@reviewed(sha="<combined sha>")` (the audit prints the current value when a review is unpinned).
If a cited paper's library text later changes, the recomputed sha no longer matches and the
citation flips to `stale-review` (blocking) — re-read the paper and re-stamp. An un-pinned review
still backs but the audit nudges you to pin it.

#### Bibliometric claims — a claim ABOUT the literature (e.g. "most-cited")

Some load-bearing assertions are about the **literature itself**, not the science: *"X is the
most-cited result on this question," "Y is rarely replicated," "this regime is understudied."* These
are empirical claims about citation counts / the state of the field — and `source()` quote-grounding
**cannot represent them**, because no sentence in any paper asserts its own citation frequency. Left
as free prose they slip past every audit (a quote-checked sentence next to them looks "covered"),
which is exactly how a false "single most-quoted result" once shipped GROUNDED. Ground them instead
as a **`@kind("bibliometric")`** claim:

```python
from grounding import kind, strength, reviewed          # the grounding core
from scientist.grounding import cited_by                 # scientist's literature layer

@kind("bibliometric")
@strength("moderate")
@reviewed(date="2026-06-19", by="independent-review", support=True,
          note="comparison set = the 4 loss-tolerance papers; metric = OpenAlex cited_by_count",
          sha="<the audit prints the value+as_of pin to stamp>")
def test_depth_datum_is_not_the_most_cited():
    "Among the loss-tolerance papers, Silva-Santos 2015 and Daily 2011 are far more cited than the ~50% depth datum."
    assert cited_by("silvasantos2015ube") > cited_by("sonzogni2020assessing")
    assert cited_by("daily2011adeno")   > cited_by("sonzogni2020assessing")
```

`cited_by()`/`metric()` read a **stored OpenAlex metric** off the library record (so the read is
keyless/offline like `source()`; populate it with `bib enrich`), record it as provenance
(`{citekey, metric, value, as_of, source}`), and return the bare number so the **relation is a
plain-Python `assert`** — no operator DSL; use any predicate (`>`, top-k, ratios). Cite it with
`[lit:]` like any claim. The split of duties:

- **The assert proves the arithmetic.** A count that drifts enough to flip the relation fails the
  pytest (RED) — correctness is self-checking.
- **`@reviewed(support=True)` proves the interpretation.** Passing the assert is *necessary but not
  sufficient*: a human/agent must still vet the comparison set and metric choice. An unreviewed
  bibliometric claim is `needs-review` and does **not** back a `[lit:]` cite (mirrors a literature
  claim with no `@reviewed`).
- **The pin is over value + as_of, bucketed.** `@reviewed(sha=…)` pins a sha of each metric's
  `(citekey, value→2-sig-figs, as_of-month)`; a +1 tick does not churn it (the assert catches a real
  flip), but a *material* move or a refreshed snapshot flips the cite to `stale-review` (blocking) —
  re-vet and re-stamp. A snapshot with no `as_of`, or one older than ~12 months, is a non-blocking
  freshness advisory (`metric-asof-unknown`/`metric-asof-stale`) — re-`bib enrich`. As with a
  literature claim, the audit prints the pin to stamp when it is unpinned.

A bibliometric claim is third-party (about the published record), so it is litreview-legal — a
litreview may carry one as a grounded `@kind("bibliometric")` claim (e.g. "the field's most-cited
result is the independent disconfirmer, not the single-lab datum").

#### `[litreview:]` — cite a neutral literature survey (protocol-keyed staleness pin)

A report can ground a whole topic on a **litreview** (`kind=litreview` — a thesis-independent,
neutral survey of the third-party literature a report argues *from*; the full discipline is in
[litreview.md](litreview.md)) with **`[litreview:<id>]`**, where `<id>` is `<exp-or-program>::<slug>`
(almost always `program::<slug>`) or a bare `<slug>`. Unlike `[report:]` it rests on no conclusion —
it points at the assessed evidence map, and a report carrying `[litreview:X]` cites X's `[lit:]` claims
directly rather than re-authoring them. The survey's own integrity (its committed PROSPERO/PRISMA
`protocol.md` + `screening.jsonl`, audited by `sci litreview`) is what guards coverage; `sci report`
adds exactly **one** report-side check, because it is a property of the *consuming* report:

- **`stale-litreview` (blocking) + the `litreview_pins` front matter.** A citing report pins to X's
  **registered search method** — a sha over X's `protocol.md` *Search queries* + `as_of` + `sources`,
  recorded per litreview in YAML front matter:

  ```yaml
  litreview_pins:
    program::target-biodistribution: "a1b2c3d4e5f6"
  ```

  `sci report` computes the *current* pin and surfaces it: an unrecorded pin is a non-blocking nudge
  (it prints the value to paste in, or `--write-pins` writes it); a recorded pin that no longer matches
  is the blocking `stale-litreview` — X's search was re-run with a changed query, a refreshed `as_of`
  snapshot, or an added/dropped source, so its coverage may have moved. Re-examine X, then re-pin.
  Edits elsewhere in X (reworded prose, a new claim, a screening decision) never touch the pin, so a
  litreview can be re-swept often without a BROKEN cascade. There is **no** mechanical omissions/
  coverage gate on the report side — that is carried by the survey-side and report-side completeness
  critics, reading X's screening log (see [litreview.md](litreview.md) → *Consumption*).

> **Prerequisite — the pin needs the cited litreview's `protocol.md`, and `[lit:]` resolution needs
> the full transitive grounding tree regenerated first.** `sci report` reads the grounding reports of
> everything the report transitively rests on — each cited experiment's `grounding_report.json` and any
> `[report:]`/`[litreview:]` dependency's. Those files are **gitignored / regenerable** (a fresh
> checkout has none), so a missing or stale upstream shows `BROKEN` and **masks downstream results**.
> Regenerate the whole tree (`pytest … --grounding-out …` per dep — see *Running* above) before reading
> the audit. See [litreview.md](litreview.md) → *Consumption*.

**Importing a single edge-claim from an adjacent scope — flag-and-delegate.** When a report (or
litreview) needs *one* claim that belongs to a neighbouring scope — e.g. citing an over-side
"headroom" datum as the upper edge of a loss-side band — cite that one `[lit:]`/`[claim:]` claim
directly and **name the report/litreview that owns the adjacent scope, noting the boundary**. That
is the entire obligation: importing an edge-claim does *not* require surveying the adjacent
home-literature yourself, and the completeness critic stays scoped to *this* report's own subject —
it must not pull the neighbouring literature into scope for a single imported edge-claim.

See [litreview.md](litreview.md) → *Consumption* and *Staleness* for the discipline (the
protocol/screening artifacts, the completeness critic) behind both.

### Figures & tables — embed a *grounded derivation*, never an ad-hoc graphic

A cross-experiment report often needs a *new* comparison plot/table no single experiment
produced. Produce it through a **program-level derivation** — the SAME
`grounding.derivation(...)` machinery `derive.py` uses — so the artifact is sha-pinned with
its recorded inputs, then embed it:

```python
# program/analysis/derive.py
from scientist import grounding
from scientist.experiments import program, k1_230101, k1_230202

def main():
    with grounding.derivation(program, __file__) as d:   # `program` is the study handle
        a = k1_230101.analysis.kd_summary                # cross-experiment: just import + read
        b = k1_230202.analysis.kd_summary
        d.write_table("kd_compare.csv", compare(a, b))
        d.write_fig("kd_compare.png", plot(a, b))
```

This records analysis provenance into `program/experiment.yml` (artifact + sha, inputs =
the experiment artifacts read + this recipe). Per-experiment summary reports embed their
own `<exp>/analysis/...` artifacts the same way. **No ad-hoc untracked graphics** — an
embed that no analysis edge produces fails the audit.

Embed with Markdown image syntax (path relative to the report file):

```markdown
![Day-29 knockdown, ASO 7 vs 12](kd_compare.png)
![Per-cohort table](kd_compare.csv)
```

A `.csv` embed is inlined as a Markdown table on render; a figure is embedded as an image.

### Program-level derivations are auditable by `sci reproduce`

`sci reproduce program` re-runs `program/analysis/derive.py` and checks its artifacts
reproduce — exactly like a per-experiment derivation. The one difference: a program
comparison legitimately *fans in other experiments'* recorded `data/`/`analysis/`
artifacts, so its **reads-only-data** contract is relaxed to "reads only **tracked**
inputs" (an untracked/bypass read is still flagged). A per-experiment derivation keeps the
strict read-only-`data/` contract.

## `sci report` — build / audit / render

```bash
# AUDIT (default): validate every [claim:<id>] citation + figure/table embed, mechanically.
sci report <report.md> [--home H] [--json]

# RENDER the validated report to the primary deliverable (PDF), via pandoc.
sci report <report.md> --render out.pdf [--to pdf|html|docx] [--force]

# TRACE the report atop the DAG: report -> each cited claim -> analysis -> data -> raw.
sci report <report.md> --trace          # (or: sci trace <report.md>)

# INDEX a finished report into the store as kind=report (title/abstract + section summaries).
sci report <report.md> --index
```

What the **audit** validates mechanically (a finding fails the audit — `BROKEN`, exit 1):

- **citations** — each `[claim:<id>]` must resolve to a *live* claim in some experiment's
  grounding report, and that claim must be **grounded** by the same rule as §3 / `sci
  trace`: `outcome ∈ {passed, xpass}` **and** `strength ∈ {strong, moderate}`. Verdicts:
  - `backed` — resolves + grounded;
  - `weak-backing` — resolves but contradicted (`xfail`) / drifted (`failed`) /
    unverifiable (`skipped`) / weak — surfaced *with* its outcome+strength (blocking);
  - `missing` — no claim has this id (write the claim first) — blocking;
  - `ambiguous` — a bare node name matches >1 claim — qualify it — blocking.
- **embeds** — each must be a *current* sha-pinned `analysis/` artifact recorded in an
  experiment's (or the program's) ledger. Verdicts: `current` (recorded + on-disk sha
  matches) · `drifted` (bytes differ from the recorded sha) · `missing` (recorded, absent
  on disk) · `untracked` (on disk but no edge records it — an ad-hoc graphic) · `dangling`
  (neither recorded nor on disk). Everything but `current` is blocking.

What stays the **semantic pass** (per §3): is every quantitative sentence actually cited; is
each cited claim *on-topic* for its sentence (`off-topic`); is a number a `derived` inline
combination its claims don't assert; is an unbacked *qualitative* conclusion acceptable
(advisory) or over-reaching. `sci report` does **not** detect assertions — it resolves the
citations/embeds you wrote, so a `GROUNDED` audit certifies *citation/embed integrity, not
assertion↔evidence correspondence*. The `derived` case is the sharp edge: a sentence can cite
two real claims and pass the audit while asserting a product (or a mis-transcribed value)
neither claim makes.

As a **non-blocking recall aid** for that pass, the audit also emits `unsupported-quantity`
**advisories** (shown with `~`, and in `--json` under `advisories`; they never change
GROUNDED/BROKEN): a %/×/fold number in a *cited paragraph* that **no cited claim anywhere in the
report asserts**. It is deliberately high-precision and narrow — paragraph-scoped, with a
report-wide restatement filter, skipping `[report:]`-cited and uncited paragraphs — so it catches
mis-cited/mis-transcribed figures but **not** an *uncited* inline-derived number (a bare table cell,
or a value computed in prose that happens to land near an unrelated claim's number). Those, and the
on-topic/over-reach judgments, remain the subagent's job: the advisory is the mechanical floor, not
the check.

The audit emits a second recall aid, `weak-load-bearing` (same `--json` `advisories` channel,
same never-flips-GROUNDED contract): a **bound** (a %/×/fold quantity, the load-bearing proxy the
tool can see) in a cited paragraph backed **only** by claim(s) that fall short of *robust*. It
fires only when **every** cited claim in the paragraph is non-robust — one strong, independent,
in-scope backing clears it — and names the specific deficit per claim (`strength<strong`,
`single-group` = one lab / `independent_groups<=1`, `suggestive-source` = indirect,
`secondary-source` = a relay, `abstract-only`, `weak-locator` = a tier-≥2 chunk, or an
`interpretive`/`external` claim doing the work). For each flagged claim it also hands the reviewer
the evidence the author had — the claim's `strength` and its review **note** text (the
`@reviewed(note=…)`/caveats — the "all one lab" caveat the author already wrote, when the grounding
report records one; machine-judged claims carry no note, only the per-source signals) — so the
reviewer can weigh scope and robustness with what the author saw, not a bare tag. This is the
mechanical half of the **candor principle** below: it raises *candidates* where importance and
robustness may diverge. It cannot judge which claim is actually load-bearing, nor whether a
source's *measured scope* transfers to the use (a prenatal-model datum bounding a postnatal
therapy) — those stay §3/human judgments; the advisory just surfaces the paragraph for the reviewer
to weigh.

The mechanical advisory covers only quantitative **bounds** (a %/×/fold magnitude). A purely
*qualitative* load-bearing claim on weak evidence produces no advisory and remains the §3
reviewer's responsibility via the verdict mandate — the recall-aid intentionally doesn't try to
catch those, because flagging every qualitative claim on a `moderate` source would flood.

### Candor proportional to centrality — a load-bearing claim on thin evidence must say so

A report can audit fully **GROUNDED** — every quantity attributed to a real claim, every quote
backing its paraphrase — while a **central, load-bearing conclusion rests on evidence that is not
commensurate with its importance**, and the prose never acknowledges it. Grounding checks
*attribution faithfulness*, not *evidentiary weight relative to how much the conclusion leans on
the claim*. The discipline that closes that gap: **a load-bearing claim or bound resting on
less-than-robust evidence must carry an explicit acknowledgment of that strength in the prose where
it does its work** — in the sentence that derives the ceiling, not only buried in an assumptions
list a reader skips. Candor scales with centrality × (lack of robustness): strong, numerous,
in-scope evidence needs no hedge; the requirement bites precisely when a conclusion leans hard on a
claim whose support is anything other than robust.

"Not robust" is broader than "single lab / weak strength" — that is one signal among several.
Non-robust also includes: a contested or unreplicated result, indirect (`suggestive`) or secondary
evidence, an abstract-/title-only source, a result used *outside its measured scope* (the
scope-transfer case the tool cannot see), a tidy quantitative bound resting on one study, or an
analogy doing load-bearing work. The motivating failure: a safety **ceiling** the whole
recommendation depended on was derived from a single datum — "≈50% UBE3A loss is tolerated" — that
was one lab, a prenatal/paternal-deletion model with normal *postnatal* expression
(scope-mismatched to a chronic postnatal therapy), and a non-significant trend; the claim was even
`strength=moderate` with a review note saying "all one lab", yet the prose built the ceiling on it
with no discussion of how thin the support was. The fix is not to drop the claim but to **say what
it rests on, in the sentence that uses it**, and treat the bound as provisional.

### Finalization: the §3 prose↔claims pass and the voice/tone review

Two fresh-context subagent reviews are **mandatory before a report is final**, alongside the
`sci report` audit (not after it): the **§3 prose↔claims pass** (does every quantitative sentence
map to a claim that contains that value; is each citation on-topic; is any number a `derived`
combination) and the **voice/tone review** (does it read like peer prose, not an LLM essay). Both
must be **delegated to a fresh-context subagent** — the author carries the same finish-line bias
that produced the draft and will wave through exactly what the mechanical audit can't see. The full
procedure for each (what to hand the subagent, how to prompt it adversarially, the blocking-vs-
surfaced grading) lives in [report-authoring.md](report-authoring.md) → *Required: the §3 pass* and
*Required: a voice/tone review*; the §3 rationale is in [review-audit.md](review-audit.md). The
objective stop condition is an empty *blocking* list from each, judged by the reviewer, not the
author.

### Render toolchain

Render is via **pandoc** (`brew install pandoc`; a PDF target also needs a LaTeX engine, e.g.
`brew install --cask basictex` → `xelatex`; `--to html` is the portable target, no LaTeX needed). A
`BROKEN` audit refuses to render unless `--force`. The renderer assembles a self-contained
Markdown: each `[claim:<id>]` becomes a native pandoc **footnote** (the cited claim's *statement* +
a compact monospace `claim_id`), left as a true **bottom-of-page footnote** so a reader checks a
grounding without paging to the end; `.csv` embeds inline as full-width Markdown tables; figures are
absolutised and placed in-line full-text-width (not floated); the `[lit:]`-cited papers are
collected into an auto-generated **References** section (deferring to a hand-authored one), which
renders unnumbered. The PDF applies a restrained house style (KOMA `scrartcl`, sans headings + serif body
via fontspec probed through `fc-list`, modern monospace for ids, 1-inch margins, a classification
stamp + date + source-revision sha in the header/footer). All of this is implementation detail —
the exact font probing, the `references.lua`/`layout.lua`/`endnotes.lua` AST filters, and the
fontspec fallbacks — and lives in `report.py`'s `render()`; read it there if you need to change the
toolchain. No styling knobs belong in the report Markdown; the content carries the report, not the
chrome.

### Indexing + traceability

`sci report --index` upserts the report into libkit as a **`kind=report`** document — the
card leads with title + abstract and lists section summaries + the claim ids it cites — so
`sci query "…"` (optionally `--kind report`) answers "which report makes the case for X".
Keyed by a stable `report_id` (`<exp-or-program>::<slug>`); re-indexing upserts in place.

`sci trace <report.md>` (a report node atop the DAG) walks the report down through each
cited claim to the original measurements, flagging breaks — the cross-experiment,
report-rooted counterpart to the per-experiment `sci trace <exp>`.

## Maintaining (for agents working ON scientist)

The report machinery is store-free at its core: parsing + audit + the report-rooted trace
live in `scientist/provenance/report.py` and `trace.py` (PyYAML + stdlib; pandas only for
`.csv` table inlining), matching `trace`/`reproduce`. The `kind=report` indexing lives in
the store layer (`_meta.report_card_markdown`, `_store.upsert_report`). The `[claim:<id>]`
grounded rule + `claim_id` format are kept identical to §3 / `index-claims` / `sci trace`
(a test asserts `report.claim_id_for` agrees with `store._meta.claim_id_for`) — if you
change one, change all and update [review-audit.md](review-audit.md).
