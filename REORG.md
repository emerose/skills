# Scientist reorg — running list

## STATUS: consolidation COMPLETE (Stages A + B1–B5 committed). Remaining follow-ups:
- **doc()→libkit: DECIDED — won't do.** Grounding (verbatim quote-match) and embedding are
  different extraction contracts: claims need deterministic, verbatim (not Markdown), keyless/local
  text — a pure function of the bytes. libkit's loaders (Datalab hosted+keys / soffice, Markdown
  output) serve embedding, where those are features. The pinned pure-Python readers are the right
  tool for grounding; keep them. Comment at the DocRef.text() dispatch records the rationale.
- **env migration (user, no rush)**: ~/.env can move ARCHIVIST_*/EXPERIMENTS_ROOT → SCIENTIST_*
  (one SCIENTIST_HOME now drives store + experiments); old names still work as fallbacks.
- **store reindex once**: store dir renamed .archivist/ → .scientist/ — run `sci reindex` to
  rebuild (the old dir is orphaned; rebuildable by design).
- **main-checkout env hygiene**: a stale pre-consolidation editable install `experiments_analyst`
  lives in /Users/sq/Development/skills/.venv — `uv pip uninstall experiments_analyst` (conftest
  already guards the tests against it).
- minor: residual Pyright cosmetics in analyst (read_csv Optional typing, token reset) left as-is.


Working notes for reorganizing extractor / archivist / analyst (bibliographer stays separate).
Goal: maximally useful + discoverable to an LLM (the *only* caller — humans never run these
by hand). Token-conscious; avoid context pollution from over-broad skills.

## Locked decisions
- **One skill: `scientist`** — subsumes archivist (the name goes away; archive/search/
  catalog/review become internal capabilities under references/).
- **Built on libkit** as the substrate (user is libkit's author; designed it for this).
- **Durable/derived boundary:** git/in-repo holds durable truth (experiment.yml provenance,
  extract.py/derive.py recipes, claims tests, data/ CSVs); libkit holds the rebuildable
  derived layer (embeddings, search index, experiment/file/entity cards, catalog). Wipe
  libkit → reindex → whole.

## What libkit stores (the point of the store)
Highest-value content = **claims + README/summary prose**. Embed those; everything else
is discovery scaffolding.
- **Embedded (semantic search):** `kind=claim` (NEW — each grounded claim, embedded on its
  statement, metadata = strength/outcome/kind/experiment/evidence/input-shas), README/
  narrative summaries, protocol/report prose, curated entity notes.
- **Carded (discoverable, not embedded):** tabular/instrument files → schema+preview.
- **Metadata-only:** binaries.

Claims loop (NEW, closes today's gap — claims are currently never indexed): run pytest claims
→ grounding_report.json → ingest each claim as a libkit `kind=claim` doc. Then "what's the
evidence for X" returns grounded claims with strength + provenance directly. Durable truth =
the pytest test source + experiment.yml; the claim card is rebuildable (re-run + reindex).
INDEX HONESTY: card carries outcome+strength so a contradicted (xfail) / weak claim is never
surfaced as positive evidence without its status. Feeds `sci trace`.

## libkit boundary — RESOLVED
1. Readers split by data shape:
   - **Document/narrative (docx/pdf/pptx) → use libkit's existing high-fidelity readers.**
     Drop analyst's hand-rolled `doc()` extraction (pdfplumber/python-docx/python-pptx);
     delegate to libkit. (May need a clean libkit "extract text for this file" entry point
     separate from indexing — user is the author.)
   - **Tabular/instrument (xlsx/xls/pzfx/prism) → local `labfiles` library, NOT libkit.**
     Tabular data isn't a fit for vector/semantic search. (archivist already cards these as
     schema+preview, doesn't embed their cells — consistent.)
2. Provenance DAG: stays in experiment.yml; libkit indexes it. (confirmed)
3. Content-addressing: keep the pipeline's sha-pinning SEPARATE from libkit's CAS. Two
   independent things that both happen to use sha256 — no coupling work. (confirmed)

## Decisions / direction
- Audience is the LLM, not humans. Human-facing ergonomics (README install, marketplace
  entries, CLI niceties) are low priority; **skill descriptions + repeatable mechanical
  tools** are what matter.
- Metaphor: **"scientist"** for the raw→data→analysis→claims pipeline. (Existing names are
  already personas: bibliographer / archivist / analyst.)
- Progressive disclosure to control tokens: narrow trigger + thin SKILL.md router + detail
  in references/ loaded on demand.

## Candidate architecture (leaning: ONE skill)
Decision (tentative): a single **`scientist`** skill covering the whole experimental-data
tree — produce/ground (extract→derive→claims) AND find/answer (index/search/catalog/review).
Rationale: the persona spans both; single entry point shows the full capability surface;
no trigger-disambiguation between "find evidence" vs "produce evidence"; progressive
disclosure (thin router + references/) removes the token argument for splitting. Skill
boundary = LLM intent (one); code boundary = maintainability (several libraries).

- `scientist` skill: thin router SKILL.md (narrow trigger = "your experimental-data tree"),
  capability detail in references/ (extract.md, derive-claims.md, search-index.md, audit.md…).
- shared **library** (code): the libkit-backed store (index/search/catalog/review) — stays a
  distinct library because it's the only *stateful* part (duckdb index + embedding backend +
  API key); paid only when search/index references load.
- shared **library** (code): `experiment.yml` + provenance schema/read/write/audit.
- shared **library** (code): format readers (xlsx/xls/pzfx/prism/docx/pptx/pdf), currently
  duplicated across extractor (`_readers.py`) and analyst (`doc()`). Optional thin
  standalone skill facade if "read/convert this lab file" is a standalone intent.

Open: (1) does "scientist" subsume the archivist name, or keep "archivist" as an internal
capability label? (2) confirm one description firing on find/organize intents too, scoped
away from bibliographer (papers) and general data wrangling.

## Target structure
```
skills/scientist/
  SKILL.md                 # THIN router: raw→data→analysis→claims + find/answer.
                           # narrow trigger ("your experimental-data tree"). Points to refs.
  references/
    extract.md             # raw→data: recipes, tabular readers, extract audit, cellcov
    derive-claims.md       # data→analysis→claims: derive.py, pytest-as-claims, doc() grounding
    search-index.md        # index/search/catalog/entities/intake/new (ex-archivist)
    review-audit.md        # provenance review, staleness audit, structural check, TRACE
    naming.md · vocab.example.yml
  scripts/
    sci.py                 # one PEP723 CLI (zero-install): extract|audit|cellcov|index|
                           # reindex|search|query|show|read|entity|new|intake|meta|review|
                           # fingerprint|check|catalog|trace|rollup|pr
  scientist/               # one installable pkg, internally modular (code boundary = several)
    provenance/            #   experiment.yml schema + read/write/audit (SHARED CORE)
    labfiles/              #   tabular/instrument readers: xlsx/xls/pzfx/prism
    experiments/           #   typed tracked data access (Study, sha-pinned DataFrames)
    store/                 #   libkit-backed index/search/catalog/review (ex-archivist)
    claims/                #   pytest plugin (the analyst harness); doc()→libkit readers
  tests/ · pyproject.toml  # pytest11 entry point; claims run via `uv run --with-editable`
```
Invocation (both zero-persistent-install):
- deterministic ops: `uv run skills/scientist/scripts/sci.py <cmd>`
- claims: `uv run --with-editable skills/scientist pytest <exp>/analysis/claims`

NEW capability: `sci trace <exp>` — walk a claim raw→data→analysis→claims over the one
provenance core, flag breaks. The end-to-end traceability the ROADMAP wants; only buildable
once all phases share `provenance/`.

## Progress
- [x] Stage A — committed 692e9ea. One `scientist` skill (router + references/), three old
      SKILL.mds removed, marketplace updated. Tests green.
- [x] Stage B1 — committed 85ebb95. provenance/ core (already FULL archivist schema), labfiles/,
      extraction/, sci.py CLI; skills/extractor/ deleted. 20 pass/1 skip + e2e verified.
- [x] Stage B2 — store/ on libkit + provenance core (review/audit/check/fingerprint route through
      provenance; arx subcommands folded into sci via store/cli.py); skills/archivist/ deleted.
      DECISION DEFERRED to B5: keep ARCHIVIST_* env vars + `.archivist/` store dir for now (don't
      break user's ~/.env / orphan their store); rename to SCIENTIST_*/.scientist in B5 w/ user OK.
- [x] Stage B3 — analyst/+experiments/ ported into package; Derivation provenance writer routed
      through the core (3rd duplicate gone); pytest11 entry point; rollup/new-unit/SPEC/playbook
      moved; skills/analyst/ deleted. 78 pass/1 skip under editable install. doc()→libkit DEFERRED:
      libkit has NO offline deterministic text API (PDF=Datalab/keys, Office=soffice, all→Markdown
      breaking quote-match) → KEPT pure-Python + TODO; needs upstream libkit `extract_text`. Notes:
      analysis entries now carry reviewed_at (intentional DAG unification); pre-existing
      Derivation._rel() emits absolute paths when EXPERIMENTS_ROOT is under a symlink (/tmp) — B5 fix.
- [x] Stage B4 — B4a (89cb17c): sci trace + staleness decoupled from store. B4b (e2f8ce1):
      claims indexed into libkit (kind=claim) + honest query rendering. 91 pass/1 skip.
      MUST-FIX (from B2): merged `sci audit <exp>` runs data-extraction audit fine but its
      provenance-staleness pass opens the libkit store and prints "error: no scientist store"
      even though staleness is PURE (experiment.yml only). Decouple: single-exp staleness +
      trace must walk provenance WITHOUT needing a store; store only for all-exp enumeration.
      LINT sweep (B5): provenance:436 sorted-over-None type warn; cli.py:665 entry maybe-None
      subscript; misc unused vars (_sha256_bytes, repo_root param, test locals).
- [x] Stage B5 — code (8499ca8): SCIENTIST_* naming + fallback, symlink path fix, lint. docs:
      README → scientist section + install/no-install examples; bibliographer↔scientist cross-link.

Package layout (skills/scientist/): top-level pkgs `provenance/` (core), `labfiles/` (pure
tabular+doc-table readers), `extraction/` (Extraction `x` helper + run/audit/cellcov),
`experiments/`, `analyst/`, `store/`. `scripts/sci.py` = PEP723 CLI (sys.path-inserts pkg dir,
declares reader deps) for deterministic ops → preserves zero-install. Claims path =
`uv run --with-editable skills/scientist pytest …`. PRESERVE public import names `experiments`
+ `analyst` and the `x`/`build(x)` recipe API — existing data-repo recipes/claims depend on them.

## Staged migration (each stage shippable, tests green, exercised on real data)
- **Stage A — LLM-facing, fast, no code moves.** Write one `scientist` SKILL.md (thin router
  + references/ split from the 3 old SKILL.mds) that points at the EXISTING tools
  (extract.py, arx.py, pytest, rollup.py). Delete/neutralize the 3 old SKILL.md *descriptions*
  so only `scientist` triggers. Delivers the discoverability + token win immediately.
- **Stage B — code consolidation, behind the scenes.**
  B1. Stand up `scientist` pkg; extract `provenance/` core; port extractor (extract/audit/
      cellcov) + move tabular readers into `labfiles/`. (smallest, self-contained)
  B2. Port archivist store/index/search/catalog/review/check/audit onto pkg + libkit.
  B3. Port analyst experiments/claims/derive/rollup; wire pytest plugin; `doc()`→libkit readers.
  B4. Unify the `sci` CLI; add `sci trace`.
  B5. Remove old skill dirs; update marketplace.json/README (low pri).

## Stage B port notes (distilled from code-mapping agents — full maps in session transcripts)

**Shared provenance core (B1) — the one API all three need identically.** Same entry shape
everywhere: `{artifact, artifact_sha256, reviewed_at?, inputs:[{path, sha256}]}` in
experiment.yml's `provenance` list. Edges distinguished only by artifact prefix: `data/…`
(extract), `analysis/…` (derive), `README.md` (review). Core API to extract:
- read+validate sidecar (status enum + synonym-normalize, reject unknown fields, exp_id required)
- write provenance entry (merge/dedup by artifact, sorted for diffability, preserve other edges
  + external inputs)
- staleness(home, exp): re-hash inputs+artifact → {up-to-date|stale|no-provenance} + changed/
  missing/added lists + artifact_changed flag
- in_folder_data_files() (inputs = raw/data/reports/analysis files, EXCLUDING README + sidecar)
Extractor `_write_provenance` (extract.py) + archivist `_experiment.py` + analyst `Derivation`
all reimplement this → unify.

**Readers split (refined).**
- `labfiles` (local lib): TABULAR readers — xlsx/xls/pzfx/prism (extractor `_readers.py`) +
  archivist's schema_and_preview tabular carding. ALSO document-TABLE extraction (docx_tables,
  pdf_pages) — these pull *tables* for data extraction, not prose, so they stay local.
- libkit: document PROSE text — replaces analyst `doc().text()` (pdfplumber/python-docx/
  python-pptx). NOTE: analyst deliberately went offline/deterministic/quote-faithful; replacing
  needs a libkit **offline `extract_text(path)`** entry point separate from ingest/embed. Confirm
  it exists or add it upstream (user = libkit author). DocRef API callers depend on: `.text()`,
  `.contains(phrase, normalize_ws=True)`, `.is_presentation`, `.path`, `.sha256`.
- archivist narrative embedding already uses libkit's pdf/docx/pptx readers via `lib.ingest`.

**libkit surface archivist uses (B2):** `Library.open(db, embedding, model)`, `lib.ingest(path,
metadata)` (doc_id = sha256(bytes); re-ingest no-op), `lib.update_metadata` (wholesale replace →
read-modify-write merge), `lib.list_documents(filters)`, `lib.query(text, limit, filters)`,
`lib.get_document/get_chunk/delete`. Kinds via metadata `kind=experiment|file|entity` (+ NEW
`claim`). Embedder fixed at create (mismatch fatal unless ALLOW flag). Fake embedder in
test_store.py (no keys). Logical identity (exp_id/path) is metadata-driven, not byte-driven.

**Zero-install reality (fix #6 refined):** the grounding harness (capture/bypass-guard/markers/
reconcile/drift) is tightly coupled to pytest + contextvars + git — NOT PEP723-able. Target =
ephemeral **`uv run --with-editable skills/scientist pytest …`** (no standing venv), not a
persistent `uv pip install -e`. Deterministic ops (extract/index/search/audit) stay PEP723 `sci`.

**Top gotchas to preserve:** determinism (byte-identical reruns); Prism content-sniff (PK→zip,
PCFFGRA4→legacy raises, else XML); identifier columns with leading zeros kept as STRINGS;
intake copy-never-move + dry-run; private vocab (vocab.yml/$ARCHIVIST_VOCAB) keeps real vendor
names out of repo; drift uses git-blame on the @strength marker line; off-Drive worktree
(new-unit.sh) for fan-out; libkit embedder-mismatch guard.

## Fix list (unordered; triage later)
1. [token] SKILL.md bodies are too long — they load in full on trigger. archivist ≈268 lines,
   analyst ≈204, extractor ≈105. Convert each to thin router + references/.
2. [trigger] extractor↔archivist trigger overlap on spreadsheet extraction
   ("pull numbers out of xlsx" in both). Sharpen: extractor *produces* data/, archivist *reads*
   values to answer. Strip producing-language from archivist triggers.
3. [structure] Factor format readers into a shared library; dedupe extractor `_readers.py` vs
   analyst `doc()` (both parse docx). Decide: local lib vs upstream libkit (Prism may be too
   niche for libkit).
4. [structure] Extract a shared `experiment.yml`/provenance core consumed by all phases.
5. [feature] Build the per-experiment traceability status (ROADMAP "DAG/enforcement"): walk a
   claim raw→data→analysis→claims, flag breaks. Single highest-value LLM feature; today needs
   6 commands across 3 skills.
6. [consistency] analyst needs `pip install -e`; others are zero-install PEP723 `uv run`.
   Friction for repeatable LLM use + the off-Drive worktree fan-out. Make it zero-install.
7. [discoverability/human, low pri] marketplace.json + README list only bibliographer +
   archivist; extractor + analyst are absent.
8. [cross-link] No machine-discoverable link between sibling skills. Add a short "related"
   pointer near the top of each so loading one surfaces the chain.
