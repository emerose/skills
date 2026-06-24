# FDA personnel dossiers (`reg personnel`)

There is **no structured FDA staff API.** Dossiers are *derived*, not fetched.

## The signal: electronic-signature blocks

Every Drugs@FDA review PDF ends with an electronic-signature manifestation page:

```
This is a representation of an electronic record that was signed
electronically and this page is the manifestation of the electronic signature.
--------------------------------------------------------------------
/s/
--------------------------------------------------------------------
JOHN J FARLEY on behalf of EDWARD M COX
10/10/2014
```

`reg personnel build` reads the Drugs@FDA reviews **already in the library**,
parses these blocks (`extract_signatures`), aggregates by person, and writes one
Markdown dossier per person (`doc_type=personnel`) listing every review they
signed. So it only surfaces people whose reviews you've ingested — ingest the
relevant `drugsfda` documents first.

```bash
reg drugsfda add NDA205834 --type medical clinpharm summary letter
reg personnel build --dry-run        # preview: who, how many reviews
reg personnel build                  # write/refresh the dossiers
reg list --type personnel
reg show person-edward-m-cox
```

## Authoring non-signers + enriching signers (`reg personnel add`)

The leadership chain that signs *nothing* — HHS Secretary, FDA Commissioner,
center/office/division directors — won't appear from `build`. Author them, and
enrich the harvested signers with role/bio, using `personnel add`:

```bash
reg personnel add "Robert F. Kennedy Jr." --role "Secretary of HHS" --office HHS \
  --bio "…" --source "https://www.hhs.gov/about/leadership/robert-kennedy.html" --tag hhs
reg personnel add "Teresa J Buracchio" --role "Director, Office of Neuroscience" \
  --division "Office of Neuroscience" --center CDER --bio "…" --tag signatory
```

Both `add` and `build` **upsert/merge**: enriching a signer keeps their harvested
`signed_reviews`, and re-running `build` keeps a hand-authored `bio`. **Match the
exact signature name form (incl. middle initial — `Teresa J Buracchio`, not
`Teresa Buracchio`)** when enriching a signer, or you create a second dossier;
get the form from `reg personnel build --dry-run`. Fill role/division from the
fda.gov org charts and the CDER "Key Officials" roster.

## Proxy signatures

Approval letters are often signed `"X on behalf of Y"`. We record **Y** (the
official of record — typically the division/office director) as the person and
**X** as `signed_by` on that review entry. So `person-edward-m-cox` is created
even though John Farley physically signed.

## Enriching a dossier (the manual/agent part)

The harvested dossier has the reviews and the name; role/division/bio are blank.
Fill them by research:

- **Org charts** (office/division directors by name):
  `https://www.fda.gov/about-fda/fda-organization/fda-organization-charts`, the
  CDER text chart, and the CDER Key Officials PDF.
- **Leadership bios** for senior staff:
  `https://www.fda.gov/about-fda/center-drug-evaluation-and-research-cder/cder-leadership-bios`.
- Web research / publications for line reviewers.

Add what you find to the record's `role`/`division`/`office`/`center`/`bio`/
`sources` and re-ingest (`personnel build` rewrites from signatures but preserves
nothing you've hand-added — for now, keep enrichment in a note or extend the
build to merge; see the maintaining section of SKILL.md). Be honest in any report
that line-reviewer identities come from signature parsing and may be incomplete.
