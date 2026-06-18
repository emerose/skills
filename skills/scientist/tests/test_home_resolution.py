"""SCIENTIST_HOME resolution — explicit env wins; cwd walk-up is a marker-based fallback.

Guards the ergonomics fix in ``scientist.experiments.root``: running from inside a data
repo should "just work" without exporting SCIENTIST_HOME, but an explicit env var must
always take precedence and an unmarked tree must still give the clear error.

Run: ``uv run --with pytest --with pandas --with pyyaml \
        pytest skills/scientist/tests/test_home_resolution.py -q``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scientist.experiments import _infer_root, _is_data_root, root


def _make_repo(tmp_path: Path, marker: str) -> Path:
    """A minimal data-repo root carrying one recognised marker set."""
    repo = tmp_path / "05 - Scientific Data"
    repo.mkdir()
    if marker == "scientist":
        (repo / ".scientist").mkdir()
    elif marker == "layout":
        (repo / "LAYOUT.md").write_text("# layout\n", encoding="utf-8")
        (repo / "program").mkdir()
    else:  # pragma: no cover - test programming error
        raise ValueError(marker)
    return repo


@pytest.mark.parametrize("marker", ["scientist", "layout"])
def test_walk_up_finds_root_from_subdir(tmp_path, monkeypatch, marker):
    repo = _make_repo(tmp_path, marker)
    deep = repo / "K1-000000 - Demo" / "analysis"
    deep.mkdir(parents=True)
    monkeypatch.delenv("SCIENTIST_HOME", raising=False)
    monkeypatch.chdir(deep)
    assert _infer_root() == repo.resolve()
    assert root() == repo.resolve()


def test_layout_md_alone_is_not_a_root(tmp_path, monkeypatch):
    # LAYOUT.md without a program/ dir must NOT be mistaken for a root.
    d = tmp_path / "notroot"
    d.mkdir()
    (d / "LAYOUT.md").write_text("# nope\n", encoding="utf-8")
    assert not _is_data_root(d)


def test_explicit_env_var_wins_over_inference(tmp_path, monkeypatch):
    real = _make_repo(tmp_path, "layout")
    other = tmp_path / "elsewhere"
    other.mkdir()
    inside = real / "sub"
    inside.mkdir()
    # cwd is inside `real`, but SCIENTIST_HOME points elsewhere — the env var wins.
    monkeypatch.setenv("SCIENTIST_HOME", str(other))
    monkeypatch.chdir(inside)
    assert root() == other.resolve()


def test_unset_and_unmarked_gives_clear_error(tmp_path, monkeypatch):
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.delenv("SCIENTIST_HOME", raising=False)
    monkeypatch.chdir(bare)
    assert _infer_root() is None
    with pytest.raises(RuntimeError, match="SCIENTIST_HOME is not set"):
        root()
