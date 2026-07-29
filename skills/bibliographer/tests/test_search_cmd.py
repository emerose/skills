"""`bib search` — the literal-substring matcher, its relaxed retry, and the
zero-result hint that stops a phrase miss being read as *absence*.

`cmd_search` only ever calls `store.all_records(filters=…)`, so these run against a
tiny in-memory stand-in — no libkit, no embedder, no keys.
"""

import argparse
import asyncio
from typing import Any

import bib

_RECORDS: list[dict[str, Any]] = [
    {
        "citekey": "urraca2013interstitial",
        "title": ("The Interstitial Duplication 15q11.2-q13 Syndrome Includes Autism, "
                  "Mild Facial Anomalies and a Characteristic EEG Signature"),
        "authors_text": "Urraca, Nora; Cleary, Jennifer; Reiter, Lawrence T.",
        "venue": "Autism Research",
        "year": "2013",
        "abstract": "",
        "tags": ["topic:cross-disorder"],
    },
    {
        "citekey": "alageeli2014duplication",
        "title": "Duplication of the 15q11-q13 region: Clinical and genetic study of 30 new cases",
        "authors_text": "Al Ageeli, Essam; Drunat, Séverine; Verloes, Alain",
        "venue": "European Journal of Medical Genetics",
        "year": "2014",
        "abstract": "",
        "tags": ["topic:genetics-genomics"],
    },
    {
        "citekey": "vaswani2017attention",
        "title": "Attention Is All You Need",
        "authors_text": "Vaswani, Ashish",
        "venue": "NeurIPS",
        "year": "2017",
        "abstract": "The dominant sequence transduction models use recurrent networks.",
        "tags": [],
    },
]


class _FakeStore:
    """Just enough store for `cmd_search`: records in, filters honoured."""

    def __init__(self, records):
        self.records = records

    async def all_records(self, filters=None):
        recs = self.records
        for key, want in (filters or {}).items():
            if key == "tags":
                recs = [r for r in recs if want in (r.get("tags") or [])]
            else:
                recs = [r for r in recs if str(r.get(key) or "") == want]
        return recs


def _args(query=None, *, author=None, year=None, tag=None, json=False):
    return argparse.Namespace(query=query, author=author, year=year, tag=tag, json=json)


def _search(capsys, query=None, **kw):
    asyncio.run(bib.cmd_search(_args(query, **kw), _FakeStore(_RECORDS)))
    return capsys.readouterr()


def test_single_token_matches(capsys):
    out = _search(capsys, "Urraca")
    assert "urraca2013interstitial" in out.out
    assert "1 result(s)" in out.out
    assert out.err == ""            # a clean hit says nothing on stderr


def test_phrase_must_be_verbatim_and_adjacent(capsys):
    # The exact title fragment hits...
    assert "urraca2013interstitial" in _search(capsys, "Includes Autism, Mild Facial").out
    # ...and the haystack is one blob, so a phrase may span title→authors.
    assert "urraca2013interstitial" in _search(capsys, "EEG Signature Urraca").out


def test_multiword_miss_relaxes_to_all_words_anywhere(capsys):
    # The incident query: every word is in that record, none of them adjacent.
    out = _search(capsys, "Urraca interstitial duplication characteristic EEG")
    assert "urraca2013interstitial" in out.out
    assert "1 result(s)" in out.out
    # The relaxation is announced, never silent.
    assert "literal phrase" in out.err
    assert "all 5 words" in out.err


def test_relaxed_retry_finds_the_right_paper_despite_word_order(capsys):
    out = _search(capsys, "Al Ageeli duplication 15q11-q13 clinical genetic")
    assert "alageeli2014duplication" in out.out
    assert "1 result(s)" in out.out


def test_true_zero_reports_per_word_counts_and_denies_absence(capsys):
    out = _search(capsys, "Urraca zzzqqxnotoken")
    assert "0 result(s)" in out.out
    assert "NOT evidence the paper is absent" in out.err
    assert "urraca (1)" in out.err                      # this word does match
    assert "words matching nothing: zzzqqxnotoken" in out.err
    assert "bib query" in out.err                       # points at the right tool


def test_single_token_zero_still_hints(capsys):
    out = _search(capsys, "zzzqqxnotoken")
    assert "0 result(s)" in out.out
    assert "NOT evidence the paper is absent" in out.err
    assert "bib query" in out.err


def test_hint_and_relaxation_keep_json_clean(capsys):
    out = _search(capsys, "Urraca interstitial duplication", json=True)
    import json as _json
    payload = _json.loads(out.out)                      # stdout stays parseable JSON
    assert [r["citekey"] for r in payload] == ["urraca2013interstitial"]
    assert "literal phrase" in out.err


def test_filters_still_narrow_the_pool(capsys):
    out = _search(capsys, tag="topic:genetics-genomics")
    assert "alageeli2014duplication" in out.out
    assert "1 result(s)" in out.out
    out = _search(capsys, "attention", author="vaswani")
    assert "vaswani2017attention" in out.out


def test_haystack_covers_every_documented_field(capsys):
    assert "vaswani2017attention" in _search(capsys, "sequence transduction").out   # abstract
    assert "vaswani2017attention" in _search(capsys, "NeurIPS").out                 # venue
    assert "alageeli2014duplication" in _search(capsys, "topic:genetics-genomics").out  # tags
