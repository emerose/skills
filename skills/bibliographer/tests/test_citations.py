"""Offline tests for the pure citation-graph analytics (bibliographer.citations).

No network, no libkit — every function is a pure function of the records passed
in. Records carry only what the analytics read: ``citekey``, an OpenAlex
``openalex_id`` (or one under ``metrics``), and a ``references`` list of
OpenAlex work ids.
"""

from bibliographer import citations as C


def _rec(ck, oa=None, refs=None, metrics_oa=None, title=None, no_refs=False):
    r = {"citekey": ck, "title": title or ck}
    if oa:
        r["openalex_id"] = oa
    if metrics_oa:
        r["metrics"] = {"openalex_id": metrics_oa}
    if not no_refs:
        r["references"] = list(refs or [])
    return r


# --------------------------------------------------------------------------- #
# self_id / references_of / has_reference_data
# --------------------------------------------------------------------------- #
def test_self_id_prefers_top_level_then_metrics():
    assert C.self_id(_rec("a", oa="W1", metrics_oa="W2")) == "W1"
    assert C.self_id(_rec("a", metrics_oa="W2")) == "W2"
    assert C.self_id(_rec("a")) is None


def test_references_of_dedupes_and_drops_empty():
    assert C.references_of({"references": ["W1", "W1", "", None, "W2"]}) == {"W1", "W2"}


def test_has_reference_data():
    assert C.has_reference_data([_rec("a", refs=["W1"])]) is True
    assert C.has_reference_data([_rec("a", no_refs=True)]) is False


# --------------------------------------------------------------------------- #
# gap_candidates
# --------------------------------------------------------------------------- #
def test_gaps_ranks_by_distinct_citing_papers():
    # W9 cited by a,b,c ; W8 by a,b ; W7 by a only.
    recs = [
        _rec("a", refs=["W9", "W8", "W7"]),
        _rec("b", refs=["W9", "W8"]),
        _rec("c", refs=["W9"]),
    ]
    gaps = C.gap_candidates(recs, min_citing=2)
    assert [g["work_id"] for g in gaps] == ["W9", "W8"]    # W7 below min_citing
    assert gaps[0]["citing_count"] == 3
    assert gaps[0]["citing_citekeys"] == ["a", "b", "c"]


def test_gaps_excludes_works_already_in_library():
    # W1 is paper b's own id, so even though a and c cite it, it isn't a gap.
    recs = [
        _rec("a", refs=["W1", "W5"]),
        _rec("b", oa="W1", refs=["W5"]),
        _rec("c", refs=["W1", "W5"]),
    ]
    gaps = C.gap_candidates(recs, min_citing=2)
    assert [g["work_id"] for g in gaps] == ["W5"]          # W1 excluded (in library)


def test_gaps_min_citing_one_and_limit():
    recs = [_rec("a", refs=["W1", "W2"]), _rec("b", refs=["W2"])]
    gaps = C.gap_candidates(recs, min_citing=1, limit=1)
    assert len(gaps) == 1
    assert gaps[0]["work_id"] == "W2"                       # most-cited first


def test_gaps_counts_distinct_papers_not_repeats():
    # A duplicated id within one paper's list counts that paper once.
    recs = [_rec("a", refs=["W1", "W1"]), _rec("b", refs=["W1"])]
    gaps = C.gap_candidates(recs, min_citing=2)
    assert gaps[0]["citing_count"] == 2


# --------------------------------------------------------------------------- #
# coupling_clusters
# --------------------------------------------------------------------------- #
def test_clusters_group_by_shared_references():
    # a,b share W1,W2 (2) -> cluster. c,d share W8,W9 (2) -> cluster. e alone.
    recs = [
        _rec("a", refs=["W1", "W2", "W3"]),
        _rec("b", refs=["W1", "W2", "W4"]),
        _rec("c", refs=["W8", "W9"]),
        _rec("d", refs=["W8", "W9"]),
        _rec("e", refs=["W100"]),
    ]
    res = C.coupling_clusters(recs, min_shared=2)
    assert res["clusters"] == [["a", "b"], ["c", "d"]]
    assert res["unclustered"] == ["e"]


def test_clusters_respect_min_shared_threshold():
    # a,b share only W1 (1). At min_shared=2 they don't couple.
    recs = [_rec("a", refs=["W1", "W2"]), _rec("b", refs=["W1", "W3"])]
    assert C.coupling_clusters(recs, min_shared=2)["clusters"] == []
    assert C.coupling_clusters(recs, min_shared=1)["clusters"] == [["a", "b"]]


def test_clusters_transitive_via_union_find():
    # a-b couple, b-c couple -> a,b,c one component even if a,c don't share directly.
    recs = [
        _rec("a", refs=["W1", "W2"]),
        _rec("b", refs=["W1", "W2", "W3", "W4"]),
        _rec("c", refs=["W3", "W4"]),
    ]
    res = C.coupling_clusters(recs, min_shared=2)
    assert res["clusters"] == [["a", "b", "c"]]


def test_clusters_sorted_by_size_then_citekey():
    recs = [
        _rec("x1", refs=["A", "B"]), _rec("x2", refs=["A", "B"]),
        _rec("a1", refs=["C", "D"]), _rec("a2", refs=["C", "D"]), _rec("a3", refs=["C", "D"]),
    ]
    res = C.coupling_clusters(recs, min_shared=2)
    assert res["clusters"] == [["a1", "a2", "a3"], ["x1", "x2"]]   # bigger first


# --------------------------------------------------------------------------- #
# isolation_report
# --------------------------------------------------------------------------- #
def test_isolation_flags_uncoupled_unconnected_paper():
    recs = [
        _rec("a", oa="WA", refs=["W1", "W2"]),
        _rec("b", oa="WB", refs=["W1", "W2"]),       # couples with a
        _rec("lone", oa="WL", refs=["W500", "W501"]),  # shares nothing
    ]
    report = C.isolation_report(recs, min_shared=2)
    flagged = [e["citekey"] for e in report if e["isolated"]]
    assert flagged == ["lone"]


def test_isolation_not_flagged_when_cited_by_a_library_paper():
    # 'target' shares no references, but paper 'a' cites target's own id -> an
    # intra-library edge, so target is connected and not isolated.
    recs = [
        _rec("a", oa="WA", refs=["WT", "W1"]),
        _rec("b", oa="WB", refs=["W1", "W2"]),
        _rec("target", oa="WT", refs=["W900"]),
    ]
    report = C.isolation_report(recs, min_shared=2)
    by_ck = {e["citekey"]: e for e in report}
    assert by_ck["target"]["in_edges"] == 1
    assert by_ck["target"]["isolated"] is False


def test_isolation_unknown_without_reference_data():
    recs = [_rec("a", oa="WA", refs=["W1"]), _rec("nodata", oa="WN", no_refs=True)]
    by_ck = {e["citekey"]: e for e in C.isolation_report(recs)}
    assert by_ck["nodata"]["has_references"] is False
    assert by_ck["nodata"]["isolated"] is False          # unknown, never flagged


def test_isolation_out_edges_count_intra_library_refs():
    recs = [
        _rec("a", oa="WA", refs=["WB", "W1", "W2"]),     # cites b (in library)
        _rec("b", oa="WB", refs=["W1", "W2"]),
    ]
    by_ck = {e["citekey"]: e for e in C.isolation_report(recs, min_shared=2)}
    assert by_ck["a"]["out_edges"] == 1
    assert by_ck["b"]["in_edges"] == 1
