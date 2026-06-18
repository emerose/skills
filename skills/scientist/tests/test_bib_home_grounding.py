"""Literature-grounding BIBLIOGRAPHER_HOME resolution — lazy .env fallback, env wins.

Guards the ergonomics fix in ``scientist.grounding._bib_home``: a claim run under pytest
never sources a shell profile, so when BIBLIOGRAPHER_HOME is unset the resolver falls back
to loading ~/.env (or a repo/cwd .env) before erroring. An already-set value always wins.

Run: ``uv run --with pytest --with pandas --with pyyaml \
        pytest skills/scientist/tests/test_bib_home_grounding.py -q``.
"""
from __future__ import annotations

import pytest

import scientist.grounding as grounding
from scientist.grounding import literature


def test_env_var_wins(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    monkeypatch.setenv("BIBLIOGRAPHER_HOME", str(lib))
    assert grounding._bib_home() == lib


def test_falls_back_to_dotenv(monkeypatch, tmp_path):
    lib = tmp_path / "fromdotenv"
    (tmp_path / ".env").write_text(f"BIBLIOGRAPHER_HOME={lib}\n", encoding="utf-8")
    monkeypatch.delenv("BIBLIOGRAPHER_HOME", raising=False)
    monkeypatch.chdir(tmp_path)            # cwd/.env is on the search path
    assert grounding._bib_home() == lib


def test_dotenv_never_overrides_real_env(monkeypatch, tmp_path):
    real = tmp_path / "real"
    (tmp_path / ".env").write_text(f"BIBLIOGRAPHER_HOME={tmp_path / 'dotenv'}\n", encoding="utf-8")
    monkeypatch.setenv("BIBLIOGRAPHER_HOME", str(real))
    monkeypatch.chdir(tmp_path)
    assert grounding._bib_home() == real


def test_clear_error_when_unset_and_no_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("BIBLIOGRAPHER_HOME", raising=False)
    # Run somewhere with no .env on cwd; HOME also has none, so the search finds nothing.
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    monkeypatch.setenv("HOME", str(bare))
    with pytest.raises(grounding.LiteratureError, match="BIBLIOGRAPHER_HOME is not set"):
        grounding._bib_home()


def test_parent_walk_stops_at_repo_root_marker(tmp_path):
    # The parent walk must resolve a repo-root .env but not climb *past* the repo into an
    # unrelated parent. checkout/.git marks the root; mod.py lives two dirs below it, and a
    # decoy .env sits just outside the checkout.
    checkout = tmp_path / "checkout"
    start = checkout / "pkg" / "sub" / "mod.py"
    start.parent.mkdir(parents=True)
    (checkout / ".git").mkdir()
    (checkout / ".env").write_text("X=1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("X=2\n", encoding="utf-8")   # OUTSIDE the checkout

    envs = literature._parent_env_candidates(start)
    assert (checkout / ".env") in envs          # repo-root .env is reachable
    assert (tmp_path / ".env") not in envs      # ...but the walk cannot climb past the root


def test_parent_walk_never_reaches_real_home(monkeypatch, tmp_path):
    # With no repo marker anywhere, $HOME is the only bound: a module nested deep under the
    # real home must NOT pull $HOME/.env (the bug we fixed) in through the parent walk. The
    # home candidate is added separately by _load_dotenv_for, never via this walk.
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    start = home / "a" / "b" / "c" / "mod.py"
    start.parent.mkdir(parents=True)
    (home / ".env").write_text("BIBLIOGRAPHER_HOME=/nope\n", encoding="utf-8")

    envs = literature._parent_env_candidates(start)
    assert (home / ".env") not in envs
    assert all(e.parent != home.resolve() for e in envs)
