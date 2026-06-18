"""Literature-grounding BIBLIOGRAPHER_HOME resolution — lazy .env fallback, env wins.

Guards the ergonomics fix in ``scientist.grounding._bib_home``: a claim run under pytest
never sources a shell profile, so when BIBLIOGRAPHER_HOME is unset the resolver falls back
to loading ~/.env (or a repo/cwd .env) before erroring. An already-set value always wins.

Run: ``uv run --with pytest --with pandas --with pyyaml \
        pytest skills/scientist/tests/test_bib_home_grounding.py -q``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scientist.grounding as grounding


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
