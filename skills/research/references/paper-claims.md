# Extracting paper-claims — the attributed-claim guide

A **paper-claim** is one assertion a third-party paper makes, written into the program's own
vocabulary and pinned to a verbatim span of the paper's text. Extracting a paper's claim set
*once* — into research's own store — is what lets an external `[lit:]` citation resolve to a
pre-extracted record instead of being re-authored, and re-read, in every report that cites the
paper.

This is the high-value, judgment-heavy half of the layer. The tool (`res paper-claims …`) only
scaffolds, schema-checks, and quote-verifies; **you** read the paper and author the JSONL. This
guide is the discipline that authoring must hold.

> One companion rule sits above everything here: **never discard the source.** A paper-claim is a
> *reversible pin into a retained paper* — the PDF stays in the bibliographer library, the claim
> only indexes it. The atomization below is lossy; the cure is that the original is always one
> `verify`/re-read away, never that the atoms are complete.

---

## 1. Attributed, not grounded — the load-bearing distinction

Internal `kind=claim` records are **grounded**: a re-runnable pytest spec checks a statement
against data the program owns. A paper-claim can never be that. It is **attributed**: it records
*what the paper says*, and its only fidelity is faithfulness-to-text. Hold three rules:

- **Pin to the text, never to reality.** "Silva-Santos et al. report ≈50% prenatal loss is
  tolerated" — not "≈50% prenatal loss is tolerated." The claim is true as an attribution even if
  the finding is later overturned; it is the *paper's* assertion you are recording.
- **Carry the paper's hedging into `strength`.** A paper that *suggests* must not become a store
  record that *demonstrates*. The paper's confidence register is part of the evidence — preserve
  it (see §5).
- **Never launder assertion into fact.** Keep paper-claims structurally and visually distinct from
  grounded claims everywhere they surface ("the paper reports X" vs "we measured X"). The audit
  and the renderer keep them distinct on purpose; your paraphrasing must too.

If you ever find yourself writing a paraphrase that reads like a program conclusion, stop — you
are laundering. Re-attribute it.

---

## 2. Comprehensive finding-grain (plus an explicit null pass)

Extract at **finding grain, comprehensively** — not every sentence (that is noise), not a lazy
headline-only skim (that re-creates the per-review re-read this layer exists to kill). Concretely,
one claim for each of:

- every **finding** (primary result the paper asserts);
- every **key secondary result** (a supporting measurement, a subgroup, a dose/time point that the
  argument leans on);
- every **stated limitation / caveat** the authors themselves name;
- **the null/negative pass** — run an explicit second sweep for what the paper found *not* to
  matter, *no* difference, *no* effect, a failed manipulation, a negative control. Mark each
  `null_result: true`. This is a separate, deliberate pass because positive results are
  over-salient; you will miss negatives unless you hunt for them on purpose. Negative space is
  evidence.

**Paper-claim sets are not size-capped.** A dense paper yields a *larger* set, never a more
compressed one — compression is the review's job (Phase 3), not the extractor's. If a paper
supports twenty distinct findings, write twenty claims.

The irreducible residual is extractor recall: a connection only visible in the full text that no
atom captured. You do not eliminate it — you mitigate it with cheap, idempotent re-extraction
(§8) and by never discarding the source.

---

## 3. The per-paper précis claim (exactly one)

At the head of each set, write **exactly one** claim with `precis: true`: the paper's own arc and
headline in one or two sentences — what it set out to show and what it concludes. This is the
cheapest place to preserve narrative across atomization (atomizing a paper otherwise loses its
arc, emphasis, and conditionality). `res paper-claims validate` **requires** exactly one précis
row — zero is `missing-precis`, more than one is `multiple-precis`.

The précis still needs a `quote` and `evidence_sha` like any claim — anchor it to the paper's own
abstract/conclusion sentence, not a sentence you synthesized.

---

## 4. Don't over-attribute — mark `borrowed` background

Extract the paper's **own** contributions. A paper's introduction restates findings whose true
source is *another* paper; if you extract those as this paper's claims you double-count when the
real source is also in the library. When a claim is background the paper borrows rather than
establishes, set `borrowed: true` (and keep the paraphrase about what *this* paper repeats, not
re-derives). When in doubt about whether a result is the paper's own, read the methods — did *they*
measure it?

---

## 5. Verbatim `hedge` + normalized `strength`

Two fields carry the paper's confidence register, deliberately redundant:

- **`strength`** — normalized to `strong` | `moderate` | `weak`, reflecting *the paper's* hedging,
  not your confidence in reality:
  - `weak` — "suggests", "may", "appears to", "is consistent with", a single observation;
  - `moderate` — "is associated with", "we observe", a measured effect without strong claims;
  - `strong` — "demonstrates", "establishes", a replicated/controlled result the paper stands on.
- **`hedge`** — the *verbatim* hedge snippet beside it ("the authors write 'appears to be
  tolerated'"). The normalized strength is searchable; the verbatim hedge is faithful. Keep both —
  a normalization can be argued with, the quote cannot.

---

## 6. `methods_qualifier` on every claim

Every claim carries a **`methods_qualifier`** — the conditions under which the paper's assertion
holds ("in vitro, HEK293", "n=12, single cohort, mouse"). A finding read context-free is a
misattribution waiting to happen: "50% knockdown" means something different in a dish and in an
animal. The qualifier *travels with the claim* so a downstream reader can never lift the number
out of its conditions. If the paper genuinely states no qualifier, say so explicitly
("not stated") rather than leaving it empty — the field is required.

---

## 7. `conditioned_on` — keep "B given A" alive

Atomization breaks conditional structure: "in the high-dose arm (A), knockdown reached 70% (B)"
becomes two claims, and B read alone is wrong. Link B to A with
`conditioned_on: ["<citekey>::<slug-of-A>"]`. `validate` resolves every same-paper
`conditioned_on` link to a sibling row (an unresolved one is a finding). A cross-paper link (a
different citekey) is allowed but not resolved in this phase.

---

## 8. Idempotent re-extraction — never discard the source

Re-running extraction **rewrites `<citekey>.jsonl` from scratch.** It is cheap and repeatable, and
it is the mitigation for extractor-recall residual: re-read, re-extract, get a more complete set.
Because the file is a whole-file rewrite, keep claim **slugs stable** across re-extractions where
the claim is the same (so an existing `[lit:]` citation keeps resolving) — change a slug only when
the claim itself changed. The PDF is never modified by any of this; the JSONL only indexes it.

---

## 8a. Anatomy of a record

One JSON object per line. Required fields are `id`, `paper`, `citekey`, `kind` (always
`"attributed"`), `paraphrase`, `quote`, `evidence_sha`, `strength`, `methods_qualifier`.

```jsonc
{
  "id": "silvasantos2015::prenatal-loss-50pct",   // <citekey>::<kebab-slug>; citekey half == citekey
  "paper": "doi:10.1234/abc",                      // source id; cross-walks to the bib library
  "citekey": "silvasantos2015",
  "kind": "attributed",
  "paraphrase": "≈50% prenatal loss is tolerated in the model",  // OUR vocabulary — the grep target
  "quote": "we observed loss of roughly half of the litters with no change in survival",  // verbatim
  "evidence_sha": "<sha256 of the folded quote span>",            // the integrity pin (see below)
  "locator": { "page": 5, "section": "Results" },
  "strength": "moderate",                          // the paper's hedging, normalized
  "hedge": "the authors write 'appears to be tolerated'",
  "n": 12, "p": "<0.05",
  "caveats": ["single cohort", "no replication"],
  "methods_qualifier": "in vivo, mouse, n=12",
  "conditioned_on": ["silvasantos2015::dosing-window"],
  "precis": false, "borrowed": false, "null_result": false
}
```

- **`paraphrase` is the search target**, and it is written in *our* consistent vocabulary — that
  shared vocabulary between searcher and store is what makes plain substring/`rg` matching adequate
  without a semantic index. `quote`/`hedge` keep the paper's own words for fidelity.
- **`evidence_sha`** is the sha256 of the *folded* quote span (NFKC + dash-fold + emphasis-strip +
  whitespace-collapse — the same fold quote-matching uses). You normally don't compute it by hand:
  re-running extraction and `res paper-claims verify` recomputes/checks it. It is the pin
  `verify` re-checks against the retained PDF.

---

## 9. The workflow

```
res paper-claims scaffold <citekey>    # confirm the paper resolves in the library (read-only),
                                       # open paper-claims/<citekey>.jsonl, print this brief
# … read the PDF, author one JSON object per line per §1–§8a …
res paper-claims validate <citekey>    # schema: required fields, kind, ids, one precis, links resolve
res paper-claims verify <citekey>      # quote-integrity: each quote still located in the paper text
res paper-claims --json --query <substr>   # load + emit for the `--json | python3 -c` pattern
```

- **`scaffold`** resolves the paper in the bibliographer library read-only (it never writes bib),
  creates the empty JSONL if absent, and warns if the paper is **abstract-only** (extraction will
  be shallow — `bib fetch` the full text first if you can).
- **`validate`** is offline/structural — it reads no paper text. **`verify`** reads the retained
  paper text and flags drift (a quote re-OCR'd away → `quote-drift`; a quote hand-edited without
  re-extraction → `evidence-sha-mismatch`).
- A `[lit:<citekey>::<slug>]` citation in a report or review resolves to the stored paper-claim
  (exact id; no tail matching). The report audit renders it **attributed** — "Author year report:
  …" — never as a grounded "we measured" note.

---

## 10. Storage & boundaries (what the tool guarantees)

- One `<home>/paper-claims/<citekey>.jsonl` per source paper, **library-wide** (shared across every
  review/report in the program). JSONL so it line-greps, line-diffs, and rewrites idempotently.
- Per-paper sharding is deliberate: under fan-out extraction each paper is its own file, so two
  extractions never race on one file or collide on a git merge.
- **No bibliographer write, ever.** bib is a read-only source of PDFs (and `discover --json`).
- **No DB on the critical path.** The JSONL is the source of truth; a semantic index is deferred
  and added only if measured recall shows plain matching under-recalls.
