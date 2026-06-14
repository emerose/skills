# Reports — `claims → report`

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
**not** a regex assertion-detector.

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
        a = grounding.cross(k1_230101).analysis.kd_summary
        b = grounding.cross(k1_230202).analysis.kd_summary
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

What stays the **semantic pass** (the authoring agent, per §3): is every quantitative
sentence actually cited; is each cited claim *on-topic* for its sentence (`off-topic`); is
an unbacked *qualitative* conclusion acceptable (advisory) or over-reaching. `sci report`
does **not** detect assertions — it resolves the citations/embeds you wrote.

### Render toolchain

Render is via **pandoc** (`brew install pandoc`; a PDF target also needs a LaTeX engine,
e.g. `brew install --cask basictex` → `xelatex`). The renderer first assembles a
self-contained Markdown: `[claim:<id>]` → a footnote whose note carries the cited claim's
statement + `[outcome · strength]` + its `claim_id`; `.csv` embeds inlined as Markdown
tables; figure paths absolutised. `--to html` needs no LaTeX engine (the portable target).
A `BROKEN` audit refuses to render unless `--force`.

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
