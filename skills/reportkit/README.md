# reportkit

The generic **grounded-report engine** shared by the in-repo science skills (`scientist`
today; a `research` skill next). A *report* is the terminal phase of the
`raw → data → analysis → claims → report` pipeline: human-facing, git-diffable Markdown that
collects sha-pinned, grounded claims into an argument and embeds figures/tables — under the
discipline *no quantitative prose without a backing*.

reportkit is the **mechanical** half of that: parse a report's inline citations + embeds,
audit each against the live grounding evidence, surface non-blocking review advisories, render
a self-contained Markdown (footnoted citations, inlined `*.csv` tables) and drive pandoc, and
trace a report down through its citations to the raw measurements.

## Not a PyPI package

It knows the in-house `[claim:]` citation grammar, so it is only useful inside this repo. The
host skill reaches it via a `sys.path` insert (there is no workspace/root `pyproject`), e.g.
`scientist._bootstrap_reportkit()`. It still carries its own `pyproject.toml` + `tests/` so it
can be unit-tested standalone (`pip install -e skills/reportkit && pytest`).

## The citation-resolver registry — the extension seam

The engine natively resolves three citation kinds: `[claim:<id>]` (a grounded internal claim),
`[report:<id>]` (a lemma sub-report), and `![..](..)` embeds. It knows **nothing** about
literature, libraries, or any domain store. Every other citation scheme plugs in through
`register_citation(...)`:

```python
import reportkit
reportkit.register_citation(
    "lit",
    regex=LIT_RE,            # parse_report discovers its citations
    parse_key="lit_cites",   # audit() returns its records under this key
    resolve=...,             # audit hook:  (cites, ctx) -> (records, findings, advisories)
    note_text=...,           # render hook: (cid, rctx) -> footnote text
    bib_entries=...,         # render hook: (cids, rctx) -> [(sort_key, works-cited entry)]
    render_lines=...,        # audit-output hook: (result) -> [str]
    quantity_cites=True,     # opt into the prose-quantity advisory pool
)
```

`scientist.provenance.literature_cites` registers the `[lit:]` / `[litreview:]` schemes this
way (and is imported on scientist's normal report paths). That seam is what lets the literature
layer live in — and later move between — host skills without the engine importing any of it.

## Layout

- `reportkit/report.py` — parse / audit / render / advisories / scope + the registry.
- `reportkit/trace.py` — the report-rooted trace (`trace_report`), with the per-experiment
  claim→raw walk injected as `trace_fn` (the host reads the experiment ledger).
- `reportkit/_grounding_io.py` — locate + load `grounding_report.json`.
- `reportkit/_ledger.py` — read-only `experiment.yml` primitives (hash, parse, edges) used to
  ground embeds against the recorded analysis artifacts.
- `reportkit/*.lua` — pandoc filters bundled as package data.
