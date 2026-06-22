"""Unit tests for claims coverage (the banked-but-unclaimed completeness check).

Pure-data tests of the set-difference and recency filter; the bib-CLI glue lives in sci.py.
"""

from research import coverage as C


def _claim(*citekeys, kind="literature"):
    return {"kind": kind,
            "evidence": {"lit_sources": [{"citekey": ck} for ck in citekeys]}}


def test_cited_citekeys_pulls_from_lit_sources():
    idx = {
        "a::t.py::test_one": _claim("punt2022molecular", "scoles2011increased"),
        "a::t.py::test_two": _claim("punt2022molecular"),          # dup across claims
        "a::t.py::test_data": {"kind": "result", "evidence": {"pct": 48}},  # no sources
    }
    assert C.cited_citekeys(idx) == {"punt2022molecular", "scoles2011increased"}


def _lib(*pairs):
    return [{"citekey": ck, "added_at": ts} for ck, ts in pairs]


def test_coverage_basic_diff_and_pct():
    lib = _lib(("a", "2026-06-01T00:00:00+00:00"),
               ("b", "2026-06-10T00:00:00+00:00"),
               ("c", "2026-06-17T00:00:00+00:00"))
    res = C.coverage(lib, cited={"a"})
    assert res["library_total"] == 3
    assert res["cited_count"] == 1
    assert res["uncited_count"] == 2
    assert res["coverage_pct"] == 33.3
    # uncited newest-first
    assert [r["citekey"] for r in res["uncited"]] == ["c", "b"]


def test_since_filter_flags_recent_uncited():
    lib = _lib(("old", "2026-06-01T00:00:00+00:00"),
               ("new1", "2026-06-16T09:00:00+00:00"),
               ("new2", "2026-06-17T09:00:00+00:00"))
    res = C.coverage(lib, cited=set(), since="2026-06-16")
    assert [r["citekey"] for r in res["flagged"]] == ["new2", "new1"]   # newest-first, old excluded


def test_no_since_uses_recent_n():
    lib = _lib(*[(f"k{i}", f"2026-06-{i:02d}T00:00:00+00:00") for i in range(1, 6)])
    res = C.coverage(lib, cited=set(), recent_n=2)
    assert [r["citekey"] for r in res["flagged"]] == ["k5", "k4"]
    assert res["uncited_count"] == 5


def test_cited_not_in_library_surfaced():
    lib = _lib(("a", "2026-06-01T00:00:00+00:00"))
    res = C.coverage(lib, cited={"a", "ghost2099typo"})
    assert res["cited_not_in_library"] == ["ghost2099typo"]
    assert res["cited_count"] == 1   # only library∩cited counts toward coverage


def test_query_scopes_and_ranks_uncited():
    """--query restricts the worklist to topic-relevant uncited papers and ranks them by score
    (the off-topic uncited paper is dropped; a cited paper never appears even if it scores)."""
    lib = _lib(("ontopic_hi", "2026-06-01T00:00:00+00:00"),
               ("ontopic_lo", "2026-06-02T00:00:00+00:00"),
               ("offtopic", "2026-06-17T00:00:00+00:00"),
               ("cited_hit", "2026-06-10T00:00:00+00:00"))
    # bib-query hits (citekey -> score), mocked the way the library is mocked elsewhere.
    scores = {"ontopic_lo": 0.40, "ontopic_hi": 0.90, "cited_hit": 0.95}
    res = C.coverage(lib, cited={"cited_hit"}, query="ube3a dosage", query_scores=scores)
    # ranked by score desc; off-topic dropped (not in scores); cited paper excluded though it scored.
    assert [r["citekey"] for r in res["flagged"]] == ["ontopic_hi", "ontopic_lo"]
    assert res["flagged"][0]["score"] == 0.90
    assert res["query"] == "ube3a dosage"
    # the coarse library-wide tally is unchanged underneath the scoping.
    assert res["uncited_count"] == 3


def test_query_combines_with_since():
    lib = _lib(("old_hit", "2026-06-01T00:00:00+00:00"),
               ("new_hit", "2026-06-17T00:00:00+00:00"))
    scores = {"old_hit": 0.99, "new_hit": 0.10}
    res = C.coverage(lib, cited=set(), since="2026-06-16",
                     query="topic", query_scores=scores)
    assert [r["citekey"] for r in res["flagged"]] == ["new_hit"]   # old_hit excluded by --since


def test_query_render_smoke():
    lib = _lib(("a", "2026-06-01T00:00:00+00:00"), ("b", "2026-06-17T00:00:00+00:00"))
    out = C.render_coverage(C.coverage(lib, cited=set(), query="topic X",
                                       query_scores={"a": 0.5, "b": 0.7}))
    assert "topic X" in out and "score" in out and "b" in out


def test_empty_library():
    res = C.coverage([], cited={"a"})
    assert res["coverage_pct"] == 0.0 and res["library_total"] == 0


def test_render_smoke():
    lib = _lib(("a", "2026-06-01T00:00:00+00:00"), ("b", "2026-06-17T00:00:00+00:00"))
    out = C.render_coverage(C.coverage(lib, cited={"a"}, since="2026-06-16"))
    assert "claims coverage" in out and "b" in out
