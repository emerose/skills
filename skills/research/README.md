# research

The **literature layer** of the scientific-data program: neutral PROSPERO/PRISMA literature
reviews, per-paper attributed paper-claims, `[lit:]` grounded third-party claims, and bibliometric
claims. Split out of [`scientist`](../scientist) (which now owns only experiments) so the two
skills stay independent — they compose only through the shared report engine
([`reportkit`](../reportkit)).

See [SKILL.md](SKILL.md) for the full guide.

## Layout

```
research/
  research/            # the package
    __init__.py        # bootstraps reportkit; exposes paper/source/converge/metric/cited_by + the judge cache
    literature.py      # verify a quote against a bibliographer-library paper (read-only/keyless)
    judgments.py       # the literature support-verdict cache (pure, offline)
    refresh.py         # the `res judge` worklist + record step
    literature_cites.py# the [lit:]/[litreview:] citation layer (registers with the reportkit registry on import)
    report.py          # research's report façade (reportkit engine + the literature citation layer)
    litreview.py       # the litreview audit (PROSPERO/PRISMA survey)
    reviewtree.py      # the Phase-3 review-node tree ([litreview:] edge graph)
    paperclaims.py     # a paper's pre-extracted attributed claim set
    coverage.py        # library papers cited by no grounded claim
    plugin.py          # the companion pytest plugin (loads the support-verdict cache; --judge-cache)
    cli_utils.py       # CLI helpers (home resolution)
  scripts/res.py       # the `res` CLI (PEP 723; zero-install via uv)
  bin/res              # launcher shim
  tests/               # the literature test suite
```

## Dependencies

- **pytest-grounding** (PyPI) — the claim-grounding core.
- **reportkit** (in-repo, `../reportkit`) — the generic report engine, reached via `sys.path`.
- **bibliographer** (sibling skill, `../bibliographer`) — the paper library, reached via `sys.path`;
  set `$BIBLIOGRAPHER_HOME`.

research never imports `scientist`, and `scientist` never imports `research`.

## Running the tests

```sh
cd skills/research
uv venv .venv && . .venv/bin/activate
uv pip install -e . pytest-grounding
python -m pytest -q
```

`reportkit` and `bibliographer` are resolved from the sibling skills via `sys.path`; no extra
install is needed for the suite.
