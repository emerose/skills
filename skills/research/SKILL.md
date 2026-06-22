---
name: research
description: >-
  Manage the literature layer of a scientific-data program — neutral PROSPERO/PRISMA literature
  reviews, per-paper attributed paper-claims, and bibliometric claims — as grounded, auditable,
  re-runnable artifacts beside the experiments. Author a conclusion-free literature review (a
  thesis-independent survey of what the third-party field reports, how strong each piece is, and
  where it disagrees or is silent) with a pre-registered search protocol and a PRISMA screening
  log; ground each load-bearing statement as a [lit:] claim that pins a verbatim quote to a paper
  in the bibliographer library plus a recorded support verdict; extract a paper's claims once into
  an attributed per-paper store; and assert bibliometric claims (most-cited, rarely-replicated)
  off stored OpenAlex metrics. Use this skill whenever the user wants to do a literature review or
  evidence survey, ground a claim on a published paper ("what does Smith 2020 actually say,"
  "back this with the literature"), check whether a quote fairly supports a paraphrase, build or
  audit a review tree, extract a paper's attributed claims, ask "which papers in the library no
  claim cites," or make a claim about the literature itself — even if they don't say "research."
  For the experiment pipeline (raw→data→analysis→claims→report) use scientist; for the raw paper
  library (add/search/discover PDFs) use bibliographer.
---

# Research

The **literature layer** of the scientific-data program — split out of `scientist` so that
scientist owns *experiments* (raw → data → analysis → claims → report) and research owns
everything *literature*:

```
bibliographer library  →  [lit:] claims / paper-claims  →  litreview (PRISMA survey)  →  cited by a report
   (the paper PDFs)         (grounded third-party facts)      (a neutral evidence map)
```

research grounds third-party statements the same way scientist grounds experimental ones — a
machine-checkable spec linking a statement to sha-pinned evidence with a strength — except the
evidence is a **published paper** in the [bibliographer](../bibliographer) library, not a CRO
measurement. The three artifact kinds:

- **`[lit:]` claims** — a grounded third-party fact: a verbatim quote located in a paper's stored
  text *plus* a recorded support verdict ("does the quote fairly back the paraphrase?"). Authored
  as a re-runnable pytest spec (`source(citekey=…, quote=…, paraphrase=…)`).
- **paper-claims** — a paper's pre-extracted **attributed** claim set, extracted once into a
  per-paper JSONL store (`res paper-claims`), so a paper's assertions are reusable without
  re-reading the PDF — and are never laundered into program facts.
- **litreviews** — a neutral, conclusion-free **PROSPERO/PRISMA survey** of one sub-question, with
  a pre-registered `protocol.md`, a `screening.jsonl` flow log, and a tree of review nodes for a
  large survey. A report *argues from* a litreview; the litreview itself draws no conclusion.
- **bibliometric claims** — a claim *about* the literature (most-cited, rarely-replicated),
  grounded on a stored OpenAlex metric (`cited_by()` / `metric()`), not a quote.

The only caller is an LLM agent. The bundled tools make a literature survey *mechanical,
repeatable, and auditable* — every cited paper screened, every quote pinned, every support
judgment recorded by a fresh-context judge.

## How research composes with the other skills

research is **independent** of scientist — neither imports the other. They meet only at the shared
in-repo report engine (`reportkit`): research's `[lit:]` / `[litreview:]` citation layer registers
with the engine's citation registry, so an *experiment* report (`sci report`) can cite `[lit:]` /
`[litreview:]` when research is installed. When it isn't, the engine emits a non-blocking warning
(the citation isn't silently dropped). research reaches:

- **pytest-grounding** (PyPI) — the claim-grounding core (`from grounding import …`);
- **reportkit** (in-repo) — the generic report engine, via a `sys.path` reach;
- **bibliographer** (sibling skill) — the paper library, read-only/keyless (`$BIBLIOGRAPHER_HOME`).

research operates on the **same data tree** as scientist (`$SCIENTIST_HOME`): litreviews live under
`program/litreviews/<slug>/`, `[lit:]` claim modules under `…/claims/`, and the per-paper
paper-claims store at the home root. research owns **no** store of its own — its citation tracking
lives in the grounding reports (the `[lit:]` citekeys), the litreview files, and the bibliographer
library.

## The CLI — `res`

```sh
# Literature reviews (neutral PROSPERO/PRISMA surveys)
res new-litreview <slug>                              # scaffold protocol.md + screening.jsonl + review.md + claim module
res litreview <review.md> --ingest-discover <bib-discover.json>   # seed the PRISMA screening log
res litreview <review.md>                             # audit: every [lit:] backed, literature-only, protocol+screening committed
res litreview <review.md> --render out.pdf            # render (via pandoc); tree-aware

# Paper-claims (a paper's pre-extracted attributed claim set)
res paper-claims scaffold <citekey>                   # open the JSONL + emit the extraction brief
res paper-claims verify <citekey>                     # quote-integrity: each quote still located in the paper
res paper-claims --query "<topic>"                    # load + grep the store

# Literature support verdicts ([lit:] entailment — NO model in the tool)
res judge --list                                      # the [lit:] sources whose verdict is missing/stale
res judge --record verdicts.json                      # write a fresh-context judge subagent's verdicts into the pinned cache

# Coverage (library papers cited by no grounded claim)
res coverage --query "<topic>"                        # the completeness counterpart to a report
```

`res` is zero-install (PEP 723 deps inline) via `uv run skills/research/scripts/res.py …`, or put
`skills/research/bin/` on `$PATH` and run `res …`. Set `$SCIENTIST_HOME` (the data tree) and
`$BIBLIOGRAPHER_HOME` (the paper library), or pass `--home`.

## Grounding a `[lit:]` claim

A `[lit:]` claim is a pytest spec. The quote tripwire (`source(quote=…)`) runs every audit; the
*support* judgment (`paraphrase=…`) is recorded once by a fresh-context judge via `res judge` and
cached — never re-decided by a model on the test path, so the suite stays offline and deterministic.

```python
from grounding import kind, strength, statement   # noqa: F401
from research import source                         # noqa: F401

@kind("literature")
@strength("moderate")
def test_lumbar_aso_reaches_cord():
    """Reviewer note: scope / why this paper backs the assertion."""
    statement("Intrathecal ASO reaches the lumbar cord at therapeutic exposure.")
    source(citekey="smith2020aso", quote="verbatim span from the paper",
           paraphrase="Intrathecal ASO reaches the lumbar cord at therapeutic exposure.")
```

Run the claims suite with `--grounding-out` to emit the grounding report the audit reads. research's
companion pytest plugin loads the support-verdict cache so `source(paraphrase=…)` can pin its cached
verdict; it coexists with the grounding plugin (which owns `--grounding-out`) and scientist's
companion (the `experiment` fixture).

Detailed references — the litreview discipline, the review-node tree, and paper-claims extraction —
move into this skill alongside the rest of the literature docs.
