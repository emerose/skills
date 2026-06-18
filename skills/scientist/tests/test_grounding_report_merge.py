"""Tests for the grounding-report writer's **merge-by-test-file** behavior.

The writer (``pytest_sessionfinish`` in ``scientist.grounding.plugin``) used to fully
overwrite ``grounding_report.json`` with only the claims collected in the current run, so
a partial run (e.g. ``pytest program/claims/test_one.py --grounding-out program/analysis``)
silently dropped every other module's claims — making unrelated reports that resolve their
``[claim:]`` against that file go spuriously BROKEN. The writer now merges this run's
records into the existing report at **test-file granularity**.

These tests use only throwaway ``tmp_path`` dirs and a tiny fake pytest config; they never
touch the real data repo or paper library and never invoke a model.
"""
import json
from pathlib import Path

import pytest

from scientist.grounding import plugin as P


# --------------------------------------------------------------------------- #
# helpers / fakes
# --------------------------------------------------------------------------- #
def _rec(id_, *, outcome="passed", kind="result", statement=""):
    """A minimal claim record shaped like the ones the plugin collects."""
    return {
        "id": id_,
        "statement": statement,
        "outcome": outcome,
        "kind": kind,
        "strength": "unspecified",
        "caveats": None,
        "reviewed": None,
        "evidence": {},
        "inputs": [],
        "bypassed": [],
        "reconcile": [],
        "longrepr": None,
    }


class _FakeConfig:
    """Just enough of a pytest config for ``pytest_sessionfinish``: the collected records,
    a ``--grounding-out`` dir, and the ``grounding_fresh`` flag."""

    def __init__(self, records, out_dir, fresh=False):
        self._grounding_records = records
        self.rootpath = out_dir
        self._out = str(out_dir)
        self._fresh = fresh

    def getoption(self, name, default=None):
        if name == "--grounding-out":
            return self._out
        if name == "grounding_fresh":
            return self._fresh
        if name == "--check-drift":
            return False
        return default


class _FakeSession:
    def __init__(self, config):
        self.config = config


def _run(records, out_dir, fresh=False):
    """Drive the writer once and return (json_dict, md_text)."""
    P.pytest_sessionfinish(_FakeSession(_FakeConfig(records, out_dir, fresh=fresh)))
    data = json.loads((out_dir / "grounding_report.json").read_text(encoding="utf-8"))
    md = (out_dir / "grounding_report.md").read_text(encoding="utf-8")
    return data, md


def _ids(data):
    return {c["id"] for c in data["claims"]}


# --------------------------------------------------------------------------- #
# _test_file_of — the merge grain
# --------------------------------------------------------------------------- #
def test_test_file_of_basename_independent_of_prefix():
    # program-style prefix
    assert P._test_file_of(_rec("program/claims/test_lit.py::test_a")) == "test_lit.py"
    # experiment-style prefix (spaces and parens in the dir name)
    assert (P._test_file_of(
        _rec("K1-230102 - UNC In Vivo/analysis/claims/test_K1_230102.py::test_a"))
        == "test_K1_230102.py")
    # parametrized node id
    assert P._test_file_of(_rec("a/b/test_x.py::test_y[1-2]")) == "test_x.py"
    # bare nodeid with no path component still yields something stable
    assert P._test_file_of(_rec("test_x.py::test_y")) == "test_x.py"


# --------------------------------------------------------------------------- #
# _merge_records — pure union logic
# --------------------------------------------------------------------------- #
def test_merge_preserves_untouched_files_and_replaces_run_files():
    prior = [
        _rec("program/claims/test_a.py::test_1"),
        _rec("program/claims/test_a.py::test_2"),
        _rec("program/claims/test_b.py::test_keep"),
    ]
    # this run only touched test_a.py: edited test_1, dropped test_2, added test_3
    current = [
        _rec("program/claims/test_a.py::test_1", outcome="failed"),
        _rec("program/claims/test_a.py::test_3"),
    ]
    merged = P._merge_records(prior, current)
    ids = {r["id"] for r in merged}
    # test_b (untouched) preserved
    assert "program/claims/test_b.py::test_keep" in ids
    # test_a fully replaced by this run: test_2 gone, test_3 added
    assert "program/claims/test_a.py::test_2" not in ids
    assert "program/claims/test_a.py::test_3" in ids
    # edit reflected
    edited = next(r for r in merged if r["id"] == "program/claims/test_a.py::test_1")
    assert edited["outcome"] == "failed"
    # deterministic order (sorted by id)
    assert [r["id"] for r in merged] == sorted(r["id"] for r in merged)


def test_merge_full_run_replaces_everything():
    prior = [_rec("c/test_a.py::t1"), _rec("c/test_b.py::t2")]
    current = [_rec("c/test_a.py::t1", outcome="failed"), _rec("c/test_b.py::t2", outcome="failed")]
    merged = P._merge_records(prior, current)
    assert {r["id"] for r in merged} == {"c/test_a.py::t1", "c/test_b.py::t2"}
    assert all(r["outcome"] == "failed" for r in merged)  # all came from this run


# --------------------------------------------------------------------------- #
# _load_prior_records — graceful degradation
# --------------------------------------------------------------------------- #
def test_load_prior_absent(tmp_path):
    assert P._load_prior_records(tmp_path / "nope.json") == []


def test_load_prior_corrupt(tmp_path):
    p = tmp_path / "grounding_report.json"
    p.write_text("{ this is not json", encoding="utf-8")
    assert P._load_prior_records(p) == []


# --------------------------------------------------------------------------- #
# end-to-end writer behavior
# --------------------------------------------------------------------------- #
def test_partial_run_preserves_other_files(tmp_path):
    # seed a report with two files' claims
    _run([_rec("program/claims/test_a.py::test_a1"),
          _rec("program/claims/test_b.py::test_b1")], tmp_path)
    # now a partial run of only test_a.py
    data, md = _run([_rec("program/claims/test_a.py::test_a1", outcome="failed")], tmp_path)
    assert _ids(data) == {
        "program/claims/test_a.py::test_a1",
        "program/claims/test_b.py::test_b1",   # preserved!
    }
    # the .md reflects the UNION, not just this run
    assert "test_a1" in md and "test_b1" in md


def test_rerun_one_file_reflects_add_edit_delete(tmp_path):
    _run([_rec("c/test_a.py::keep"),
          _rec("c/test_a.py::drop_me"),
          _rec("c/test_b.py::other")], tmp_path)
    data, _ = _run([_rec("c/test_a.py::keep", outcome="failed"),
                    _rec("c/test_a.py::added")], tmp_path)
    assert _ids(data) == {"c/test_a.py::keep", "c/test_a.py::added", "c/test_b.py::other"}
    edited = next(c for c in data["claims"] if c["id"] == "c/test_a.py::keep")
    assert edited["outcome"] == "failed"


def test_full_run_replaces_everything_e2e(tmp_path):
    _run([_rec("c/test_a.py::a"), _rec("c/test_b.py::b")], tmp_path)
    # full run collecting BOTH files again, both now failing, plus drops test_a::a
    data, _ = _run([_rec("c/test_a.py::a2"), _rec("c/test_b.py::b")], tmp_path)
    assert _ids(data) == {"c/test_a.py::a2", "c/test_b.py::b"}


def test_grounding_fresh_overwrites(tmp_path):
    _run([_rec("c/test_a.py::a"), _rec("c/test_b.py::b")], tmp_path)
    data, md = _run([_rec("c/test_a.py::a")], tmp_path, fresh=True)
    assert _ids(data) == {"c/test_a.py::a"}   # clean slate — test_b orphan cleared
    assert "test_b" not in md


def test_corrupt_prior_degrades_to_this_run(tmp_path):
    (tmp_path / "grounding_report.json").write_text("not json at all", encoding="utf-8")
    data, _ = _run([_rec("c/test_a.py::a")], tmp_path)
    assert _ids(data) == {"c/test_a.py::a"}   # didn't crash; wrote this run's records


def test_output_is_deterministic(tmp_path):
    recs = [_rec("c/test_b.py::b"), _rec("c/test_a.py::a")]
    data1, md1 = _run(recs, tmp_path)
    # re-running the identical set is idempotent (merge of a set onto itself)
    data2, md2 = _run(recs, tmp_path)
    assert data1 == data2 and md1 == md2
    assert [c["id"] for c in data1["claims"]] == ["c/test_a.py::a", "c/test_b.py::b"]


def test_empty_run_writes_nothing(tmp_path):
    # no records collected → writer returns early, leaves any existing report untouched
    P.pytest_sessionfinish(_FakeSession(_FakeConfig([], tmp_path)))
    assert not (tmp_path / "grounding_report.json").exists()
