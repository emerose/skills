# Authoring a `[lit:]` literature claim — the grounding rubric

> Load this when **authoring or reviewing** a literature claim — a third-party fact grounded on a
> paper in the bibliographer library. It is the literature half of the claim-grounding discipline,
> owned by `research`. The report-side *consumption* of a `[lit:]` / `[litreview:]` citation (how an
> experiment report cites one, the audit verdicts, the auto-generated References, the staleness pin)
> lives in scientist's report engine — [../../scientist/references/report.md](../../scientist/references/report.md)
> → `[lit:<id>]` and `[litreview:]`. The litreview discipline that *organizes* these claims into a
> neutral survey is [litreview.md](litreview.md); per-paper attributed extraction is
> [paper-claims.md](paper-claims.md).

A `[lit:]` claim grounds a **third-party fact** on a paper in the [bibliographer](../../bibliographer)
library. It is a pytest spec — `@kind("literature")` calling
`source(citekey, quote=…, paraphrase=…, test=…, system=…, primary=…, group=…)` — and `converge(...)`
for a multi-source fact. Literature claims live in a claim module under the data tree
(`program/claims/test_literature.py`, or a litreview's own `test_litreview_<slug>.py`), authored with
research's grounding surface:

```python
from grounding import kind, strength, converge          # the grounding core (pytest-grounding)
from research import source                              # research's literature layer

@kind("literature")
@strength("strong")                                      # ≥2 independent groups, direct, primary
def test_target_knockdown_in_humans():
    "Antisense knockdown of the target is well tolerated in humans."
    converge(
        source("noor2015q", quote="53% knockdown at the top dose",
               paraphrase="knockdown reached 53% at the top dose"),  # hugs the quote; synthesis in the docstring
        source("smith2019",  chunk=14,                   # tier 2: a paragraph-spanning fact
               paraphrase="no dose-limiting toxicity was reported"),
    )
```

The spec **fails if the verbatim quote is not in the cited paper's stored text** (read from the LOCAL
bibliographer library DuckDB — keyless, offline; only the library's semantic *query* embeds), and
**fails outright if the cited paper is marked retracted** (OpenAlex / Retraction Watch, as of the
last `bib add`/enrich — a claim must not rest on retracted work; pass `allow_retracted=True` only to
discuss the retraction itself). A `[lit:]` citation is **second-class to a data `[claim:]`** — it is
rendered as a distinct "Literature" footnote and must never read as data-grounded.

## Authoring and reviewing a literature claim

A `[lit:]` claim grounds *attribution faithfulness* (the paper really says this), not truth — you
cite what the field reports. Two layers:

1. **Quote (tool, every audit).** Find a *verbatim* phrase in the paper that states the fact and
   pass it as `quote=`. Matching folds Unicode dashes/whitespace, but the words must be exact; use
   `bib show`/`bib query` reads to copy a real phrase. A short, specific sentence beats a long one
   (less to break). One paper can back several quotes; it appears once per endnote.
2. **Support.** Whether the quote actually *supports the paraphrase* — read in context, no
   quote-mining — is the load-bearing judgment. Record it ONE of two ways:
   - **Machine-judged (preferred, executable).** Add `paraphrase="…"` to `source()` (alongside
     `quote=`). The support judgment becomes a *re-runnable, cache-pinned* entailment check —
     "does quote Q fairly support paraphrase P?" — over two short strings (NOT "read the whole
     paper and decide if it supports X"). The judge is **you, the orchestrating agent** (ideally a
     fresh-context judge subagent you spawn — see **The machine support judge** below); the tool
     only lists the work (`res judge --list`) and records your verdict (`res judge --record`). A
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
     [../../scientist/references/report-authoring.md](../../scientist/references/report-authoring.md) —
     the same rule applies whether the relay is a review paper or a Kicho-authored report.)
   - **independence** — set `group=` so co-lab / shared-model papers count as ONE group; the
     endnote's "N independent" comes from distinct groups.

**Write the paraphrase as a faithful compression of a SELF-CONTAINED quote — not a summary of the
paper's finding.** The judge sees only the quote and the paraphrase, never the paper, and asks the
narrow question "does *this span* entail *this paraphrase*?". So the quote must itself contain the
subject + the number + the scope the paraphrase asserts, and the paraphrase must say *only* what the
span says. Hug the quote; put the synthesis in the docstring.

**Strength is evidential weight, judged — not retrieval location.** `@strength`: **strong** =
independent (≥2 distinct groups) + direct + primary; **moderate** = a single group's direct,
primary, unreplicated result; **weak** = single + *suggestive* (a related experiment that only
implies the claim), or secondary, or contested. Keep author seniority / citation count as *context*
in `note`/`caveats`, **never** as a scoring input — weighting by prestige is argument-from-authority
and the machinery would launder it as rigor. `source()` records the cited paper's **credibility
markers** from the library automatically — venue legitimacy (DOAJ; journal vs. preprint), citation
impact (FWCI / percentile / journal h-index), and the retraction flag — and they surface on the
endnote as reader context. They are exactly the "context, not scoring" signals above: shown so a
reader can weigh the source, and deliberately **never** fed into `@strength`, `support`, or any
quality gate (an impact gate would also push toward the high-profile *review* over the lower-cited
*primary* paper — the opposite of the rule above).

**A weak literature claim still backs — cite the weak disconfirmer, do not drop it.** Strength for a
`[lit:]` claim is *descriptive*, not a gate: unlike a data `[claim:]` (which must be
moderate-or-strong to back), a `weak` literature claim that is reviewed, supported, and quote-pinned
**backs its citation** and renders as an appropriately weak endnote (`lit_verdict` blocks only on a
failed quote, a non-literature claim, an un-reviewed/un-judged claim, a stale verdict, an unsupported
one, or a strength that exceeds the locator ceiling — never on `weak` itself). So
single/suggestive/secondary evidence is *citable*, not unusable. This matters most for
**disconfirming** evidence, which is often legitimately weak (one contrary case, an inferential
tolerance argument): write it as a `weak` `[lit:]` claim and cite it, rather than demoting it to a
bare reference or omitting it. Dropping a weak-but-real disconfirmer to keep the claim set "clean" is
the exact failure the disconfirming-evidence requirement exists to prevent.

**When the library has only an abstract or a title — and you need the body, pause and ask.** Many
papers are paywalled, so the bibliographer ingests only their metadata + abstract (and the
oldest/most-locked, just the title) — `bib fetch` can't get an open-access PDF. You can still quote a
real sentence from an abstract, and a title sometimes carries the headline result (mark those
`suggestive`), but you **cannot pin the specific finding** (an exact number, the actual mechanism,
the method that makes it *direct*) that lives only in the body. So: check each cited paper's available
text (a few hundred chars = title only; ~1–3k = abstract; more = full text). If a paper is
abstract-/title-only **and it is load-bearing** — the claim's strength or a specific number genuinely
depends on the body you don't have — **stop and ask the user for the full text** (they may have
institutional access: `bib fetch <key> --pdf <downloaded.pdf>`). Don't silently ground a load-bearing
claim on a title gloss, and don't silently drop it either; surface it. For *corroborating*
abstract-only sources, grounding on the abstract as `suggestive` is fine — just say so in the
`@reviewed` note. Record which cited papers are abstract-/title-only so the gap is visible, not
buried.

## The machine support judge — an executable, re-runnable support verdict

The legacy `@reviewed(support=True)` is a trusted, hand-stamped boolean the audit *never re-checks*:
it re-verifies the verbatim quote and the paper-text sha every run, but it never re-examines whether
the paraphrase is a fair reading of the quote. That is the weak link (quote-mining survives a green
audit). `source(paraphrase=…)` closes it by making the support judgment **executable**: the narrow,
local question "does quote Q entail paraphrase P?" gets a recorded, re-runnable verdict — and the
claims suite asserts on that *cached* verdict.

**Who judges: you, the orchestrating agent — not the tool.** There is **no model inside `res`** (or
`bib`). You are already an LLM that read the paper, so the tool re-owning a model and re-judging would
be backwards. Instead the loop is *list → judge → record*, and the judging is done **by a
fresh-context judge subagent you spawn** — independence matters: don't let the context that *wrote*
the paraphrase grade it. The tool's only jobs are deterministic: surface the work and record/verify
the verdict.

- **`res judge --list`** emits the worklist of `[lit:]` sources whose verdict is **missing or
  stale**, each as `{claim_id, citekey, tier, span_text, paraphrase, evidence_sha}` — `span_text` is
  the verbatim quote (tier 1) or the resolved chunk text (tier 2). Spawn a fresh subagent, hand it
  `span_text` + `paraphrase`, and ask the one narrow question: *does the span fairly support the
  paraphrase?* → `{supported, rationale}`.
- **`res judge --record <file|->`** ingests those verdicts `{citekey, paraphrase, supported,
  rationale}` (echo the worklist's `evidence_sha` back for an extra stale-span guard) and writes them
  to the cache. The pin is **recomputed by the tool** from the report's current span — a caller can't
  record a verdict against a stale or wrong span; a record whose `(citekey, paraphrase)` (or echoed
  `evidence_sha`) no longer resolves is rejected. `--judge-id` stamps *who* judged.

**The determinism discipline (non-negotiable).** The claims suite is a re-runnable, offline,
deterministic pytest suite — that is the whole system's value, and it is **unchanged**: no model is
ever called on the pytest path. `source()` and the report audit only ever *read* the verdict cache —
a plain JSON file, a pure function of bytes, no key, no network. Only `res judge --record` writes it.
A report audit and a normal grounding run stay free and deterministic.

**The cache + its key.** Each verdict answers one entailment question, keyed by the pair
`(evidence_sha, paraphrase)` and stored in `lit_judgments.json` next to the grounding report (a
machine-owned artifact, like `grounding_report.json` — never hand-edited): `{supported, judge_id,
timestamp, rationale, …}`. `evidence_sha` is the sha of the **folded** span (the same normalization
quote-matching uses — NFKC, Unicode-dash fold, strip Markdown `*`/`_`, collapse whitespace), so
markdown / whitespace / dash variants of one sentence map to ONE identity → ONE shared verdict; the
same paper sentence cited from two modules can't stale itself. The verdict is **inspectable** — a
green claim is "judged Q⊢P, by this judge, on this date, with this rationale", not an opaque "the LLM
said yes". `judge_id` is **metadata, not part of the key**: a verdict produced by a different judge
subagent is still valid, so swapping who judges does not mass-invalidate the cache.

**The locator ladder → strength.** *How precisely* a source locates its supporting text caps the
claim's strength (the audit enforces the ceiling), so a paragraph-spanning gloss can't be sold as a
pinpoint quote:

| tier | `source(...)` | the judge reads | max `@strength` |
|---|---|---|---|
| 1 | `quote=` + `paraphrase=` | the verbatim quote (two short snippets) | `strong` |
| 2 | `chunk=` + `paraphrase=` | one libkit chunk span (`bib query` returns chunk ids) | `moderate` |
| 3 | `paraphrase=` only | the whole document (costly, high-variance, least auditable) | `weak` |

Tier 1 is the default; reach for tier 2 only for a fact that genuinely spans a paragraph with no
single quotable sentence. A claim's ceiling is that of its **weakest-located** source; exceeding it
is a blocking `over-strength` finding (strengthen the locator, or lower `@strength`).

**Staleness — re-judge on drift.** The verdict is invalidated the moment `(quote_sha | paraphrase)`
drifts: a quote edit (the cited paper's text changed, or you tightened the quote) flips the citation
to `stale-judgment` and a paraphrase edit to `needs-judgment` (both blocking) — re-`list`, re-judge,
re-`record`, re-run the suite. This is the literature analogue of `stale-review`, but recomputed
every run instead of trusted once.

**Opt-in and additive.** A source that adds `paraphrase=` is machine-judged; existing `quote=` +
`@reviewed(support=…)` claims keep working unchanged. Until a verdict is recorded, the source stays
`needs-judgment` (non-blocking until the citation needs to back) — never a crash. The cache the pytest
path reads defaults to `<grounding-out>/lit_judgments.json` (next to each grounding report); override
with `--cache`.

**The list → judge → record loop.**

```
# 1. run the claims suite to (re)emit the grounding report (records paraphrase + the span)
uv run --with-editable <research> --with libkit pytest program/claims/ --grounding-out program/analysis
# 2. surface the missing/stale support verdicts to judge
res judge --list --home <data> --json > worklist.json
# 3. YOU judge each {span_text, paraphrase} — ideally via a fresh-context judge subagent for
#    independence — and write {citekey, paraphrase, supported, rationale} records, then record them:
res judge --record verdicts.json --home <data> --judge-id <who>
# 4. re-run the suite: source() now asserts on the cached verdicts (unsupported → red)
uv run --with-editable <research> --with libkit pytest program/claims/ --grounding-out program/analysis
```

**Running.** Generating the literature grounding report needs libkit + `BIBLIOGRAPHER_HOME` (research
loads `~/.env` to find it): `uv run --with-editable <research> --with libkit pytest program/claims/
--grounding-out program/analysis`. The report **audit/render** then read that JSON and need neither.
Each `source()` sha-pins the cited paper's text as a provenance input; pin the review to those texts
by stamping `@reviewed(sha="<combined sha>")` (the audit prints the current value when a review is
unpinned). If a cited paper's library text later changes, the recomputed sha no longer matches and the
citation flips to `stale-review` (blocking) — re-read the paper and re-stamp. An un-pinned review still
backs but the audit nudges you to pin it.

## Bibliometric claims — a claim ABOUT the literature (e.g. "most-cited")

Some load-bearing assertions are about the **literature itself**, not the science: *"X is the
most-cited result on this question," "Y is rarely replicated," "this regime is understudied."* These
are empirical claims about citation counts / the state of the field — and `source()` quote-grounding
**cannot represent them**, because no sentence in any paper asserts its own citation frequency. Left
as free prose they slip past every audit (a quote-checked sentence next to them looks "covered"),
which is exactly how a false "single most-quoted result" once shipped GROUNDED. Ground them instead as
a **`@kind("bibliometric")`** claim:

```python
from grounding import kind, strength, reviewed          # the grounding core
from research import cited_by                            # research's literature layer

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
