"""Unit tests for _generate: files table, deps, README refresh, summary."""

import _generate
import _meta

FILES = [
    {"path": "K1-1/README.md", "role": "readme", "file_type": "md", "indexed_as": "content", "sha256": "r"},
    {"path": "K1-1/data/kd.csv", "role": "data", "file_type": "csv", "indexed_as": "schema", "sha256": "d1"},
    {"path": "K1-1/raw/plate.eds", "role": "raw", "file_type": "eds", "indexed_as": "descriptor", "sha256": "r1"},
    {"path": "K1-1/reports/report.pdf", "role": "report", "file_type": "pdf", "indexed_as": "content", "sha256": "p1"},
]


def test_files_on_disk_table_grouped():
    t = _generate.files_on_disk_table(FILES)
    assert "`K1-1/data/kd.csv`" in t
    # readme sorts before data before raw before report
    assert t.index("README.md") < t.index("kd.csv") < t.index("plate.eds") < t.index("report.pdf")


def test_deps_exclude_readme_use_stored_sha():
    deps = _generate.deps_for_experiment(FILES)
    paths = {d["path"] for d in deps}
    assert "K1-1/README.md" not in paths          # the doc itself isn't its own dep
    assert {"K1-1/data/kd.csv", "K1-1/raw/plate.eds", "K1-1/reports/report.pdf"} == paths
    assert next(d for d in deps if d["path"].endswith("kd.csv"))["sha256"] == "d1"


def test_refresh_readme_preserves_narrative_and_is_idempotent():
    existing = ("# K1-1: Study\n\n## Synopsis\n\nHard-won caveat: LP missed.\n")
    rec = {"exp_id": "K1-1", "name": "Study"}
    out1 = _generate.refresh_readme(existing, rec, FILES)
    assert "Hard-won caveat: LP missed." in out1            # narrative preserved
    assert "## Files on disk" in out1
    assert _meta.parse_deps_block(out1) is not None
    out2 = _generate.refresh_readme(out1, rec, FILES)
    assert out2 == out1                                       # idempotent on unchanged input


def test_refresh_readme_from_scratch_uses_template():
    out = _generate.refresh_readme(None, {"exp_id": "K1-9", "name": "New"}, FILES)
    assert "# K1-9: New" in out
    assert "## Study IDs" in out                              # template scaffold
    assert _meta.get_managed_block(out, "files") is not None


def test_top_summary_has_index_and_deps():
    exps = [{"exp_id": "K1-1", "name": "A", "cro": "CRL"},
            {"exp_id": "K1-2", "name": "B"}]
    deps = [{"path": "K1-1/README.md", "sha256": "a"}, {"path": "K1-2/README.md", "sha256": "b"}]
    out = _generate.top_summary(exps, deps)
    assert _meta.get_managed_block(out, "experiment-index") is not None
    assert "K1-1" in out and "K1-2" in out
    assert _meta.parse_deps_block(out) == deps
