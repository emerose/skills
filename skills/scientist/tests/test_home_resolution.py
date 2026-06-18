"""SCIENTIST_HOME resolution — explicit env wins; cwd walk-up is a marker-based fallback.

Guards the ergonomics fix in ``scientist.experiments.root``: running from inside a data
repo should "just work" without exporting SCIENTIST_HOME, but an explicit env var must
always take precedence and an unmarked tree must still give the clear error.

Run: ``uv run --with pytest --with pandas --with pyyaml \
        pytest skills/scientist/tests/test_home_resolution.py -q``.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scientist.cli_utils import resolve_home
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


# --------------------------------------------------------------------------- #
# cli_utils.resolve_home — the shared resolver used by sci.py + store/cli.py.
# Precedence: --home → $SCIENTIST_HOME → inferred checkout root → None.
# --------------------------------------------------------------------------- #
def _ns(home: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(home=home)


def test_resolve_home_flag_wins(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    repo = _make_repo(tmp_path, "scientist")  # would be inferred…
    monkeypatch.setenv("SCIENTIST_HOME", str(repo))  # …and env is set…
    monkeypatch.chdir(repo)
    # …but an explicit --home beats both.
    assert resolve_home(_ns(str(other))) == other.resolve()


def test_resolve_home_env_beats_inference(tmp_path, monkeypatch):
    env_home = tmp_path / "env_home"
    env_home.mkdir()
    repo = _make_repo(tmp_path, "scientist")
    monkeypatch.setenv("SCIENTIST_HOME", str(env_home))
    monkeypatch.chdir(repo)
    assert resolve_home(_ns()) == env_home.resolve()


def test_resolve_home_infers_checkout_root(tmp_path, monkeypatch):
    # The KEY behavioral fix: with no --home and no env, walk up to the marker.
    repo = _make_repo(tmp_path, "scientist")
    deep = repo / "K1-000000 - Demo" / "analysis"
    deep.mkdir(parents=True)
    monkeypatch.delenv("SCIENTIST_HOME", raising=False)
    monkeypatch.chdir(deep)
    assert resolve_home(_ns()) == repo.resolve()


def test_resolve_home_none_when_nothing_resolves(tmp_path, monkeypatch):
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.delenv("SCIENTIST_HOME", raising=False)
    monkeypatch.chdir(bare)
    # Nullable contract: callers that must error on "no data folder" rely on this.
    assert resolve_home(_ns()) is None
