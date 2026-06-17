# Getting a PDF when Unpaywall doesn't have it

`add`/`import` auto-fetch an open-access PDF when one exists, and `bib fetch
<citekey>` does the same for an existing record (e.g. a citation-only **stub**).
Both try the keyless open-access tiers automatically. When that fails, work *down*
this ladder — cheaper/cleaner sources first — and once you have a file, attach it:

```bash
bib fetch <citekey>                      # auto: try all open-access sources
bib fetch <citekey> --pdf path/to.pdf    # attach a PDF you obtained manually
```

Attaching upgrades the stub to a full record (re-files it, preserves the citekey)
and the import path embeds it for search. After attaching, it's worth re-checking
the content matches (a quick `bib audit`, or just open it) — a wrong download is
worse than a stub.

## Tier 1 — open repositories (automated)

`bib fetch <citekey>` already tries these, in order, byte-verifying each
response really begins with `%PDF-` (a publisher landing page or Cloudflare
"Just a moment…" challenge that answers `200` is rejected, and the next tier is
tried):

- **arXiv** — when the record has an arXiv id (`https://arxiv.org/pdf/<id>.pdf`).
- **bioRxiv / medRxiv** — for `10.1101/…` DOIs.
- **Unpaywall** — *every* advertised PDF location for the DOI, not just the
  "best" one (which is often a publisher landing page that serves HTML; a
  repository mirror further down the list frequently downloads cleanly).
- **Europe PMC** — `/articles/<PMCID>?pdf=render`, the scraper-friendly route for
  the PMC open-access subset. The **PMCID is derived from the DOI/PMID** via the
  NCBI ID converter when the record (e.g. a citation-only stub) doesn't carry one,
  so this fires even for DOI-only stubs.
- **NCBI PMC OA service** — `oa.fcgi`, a structured fallback that returns a direct
  PDF href for the OA subset when Europe PMC's render endpoint declines.
- **NCBI PMC direct (PoW)** — for PMCID records *not* in the OA subset (notably
  **NIH author manuscripts**, which are publicly readable in PMC but which Europe
  PMC's render and `oa.fcgi` both decline). Scrapes the real PDF filename from the
  article landing page and solves the `cloudpmc-viewer-pow` proof-of-work gate (see
  the note below). This is the route that an open-access paywalled-journal paper
  (e.g. a *Sci. Transl. Med.* AAAS article deposited by NIH funding) takes.
- **Semantic Scholar** — its `openAccessPdf` link.

PDF byte downloads use a browser-like User-Agent (the polite-pool UA is kept for
the metadata APIs, which reward a real mailto). If a record still lacks the
identifier a source needs, `enrich` it first (that often adds a DOI/arXiv/PMCID),
then `fetch`.

### Note: NCBI PMC direct downloads and proof-of-work

Most PMC PDFs come via **Europe PMC** (`europepmc.org`), which has no anti-bot gate.
Fetching directly from **NCBI** (`pmc.ncbi.nlm.nih.gov`) is harder: it needs
browser-like request headers (a plain UA gets HTML, not the PDF), the real PDF
filename must be scraped from the article landing page (the bare `/pdf/` path
returns HTML), and the response is often a JavaScript **proof-of-work challenge**
that must be solved before the PDF is served. `fetch` now does all of this
automatically (`fetch_pmc_authorms_pdf` in `scripts/_resolvers.py`), as a tier
below the OA-subset routes — so author-manuscript deposits no longer need a manual
download.

The challenge mechanics, for reference: the HTML carries `POW_CHALLENGE`,
`POW_DIFFICULTY`, `POW_COOKIE_NAME` (default `cloudpmc-viewer-pow`), and
`POW_COOKIE_PATH`. It's solved by brute-forcing a nonce so that
`sha256(challenge + str(nonce))` (hex) begins with `difficulty` leading zeros, then
setting the cookie to `"{challenge},{nonce}"` on `pmc.ncbi.nlm.nih.gov` and
re-requesting the PDF. The difficulty is capped (~6) — each level is 16× the work,
so a hostile page can't make the solver grind forever. A **real browser** (Tier 3)
runs the same JavaScript and solves it transparently, so the browser route remains a
fallback if NCBI ever changes the scheme. A second reference implementation lives in
`hive-papers`: `github.com/emerose/hivemind` →
`libs/hive-papers/src/hive/papers/services/clients/pubmed_client.py`
(`_solve_pow_challenge` / `_solve_pmc_pow`).

## Tier 2 — other open sources (agent-assisted discovery)

If Tier 1 comes up empty, the paper may still be open *somewhere* the automated
sources don't index: a **preprint** of the published version, an author's copy in
an institutional repository, a PMC record under a different id, or a hit on
**PubMed / Semantic Scholar / Google Scholar**. Search for the title, find an
open copy, download it, and `bib fetch <ck> --pdf <file>`. (Google Scholar has no
API and blocks scraping — use it via the browser, not automated requests.)

## Tier 3 — institutional access via the browser

When the user has institutional access, the publisher's own PDF is reachable
through their authenticated browser session. Use the **Claude-in-Chrome** tools
(`mcp__claude-in-chrome__*`):

1. Navigate to the article (resolve the DOI: `https://doi.org/<doi>`).
2. Confirm you're on the right article, then download the publisher PDF.
3. `bib fetch <ck> --pdf <downloaded.pdf>`.

Heed link-safety: verify the destination, and don't follow suspicious links. This
is the right tool for paywalled articles the user is *entitled* to read.

## Tier 4 — peer sources (only when the user is authorized)

Sites like `https://sci-hub.ru` / `https://sci-hub.st` can return a PDF by DOI.
These are a **last resort with strict conditions**:

- **Only when the user has legitimate access** to the work (e.g. an institutional
  subscription) and is using such a site merely as a more convenient route to a
  paper they're entitled to.
- **Only with the user's explicit, per-use authorization.** Ask first; never make
  it a default or silent step. The user owns the legal/ethical call.

The tool ships no peer-source URLs and never uses them automatically. When
authorized, the agent fetches the DOI from the mirror the user designates (via the
browser, or a direct download), confirms the PDF is the correct paper, and then
`bib fetch <ck> --pdf <file>`. If there's any doubt about authorization, stop and
ask — or stay at Tier 3.

**Practical note:** these mirrors sit behind Cloudflare, so a headless
`curl`/`httpx` request to `https://sci-hub.…/<doi>` typically returns **403** (a
JS challenge page), and mirror availability/domains churn. In practice this route
needs the **browser** to pass the challenge — at which point institutional access
(Tier 3) is usually the cleaner choice anyway.

## Summary

Prefer the most open source available; escalate only as needed; always confirm the
file is the right paper before attaching; and let the user make the Tier-4 call.
