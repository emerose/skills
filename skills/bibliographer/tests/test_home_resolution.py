"""BIBLIOGRAPHER_HOME resolution — lazy + graceful, explicit env wins.

Guards the ergonomics fix: the default library home is resolved in dispatch (AFTER
``_load_dotenv``), not read at import time, so a $BIBLIOGRAPHER_HOME set only in ~/.env
is honoured. An explicit value (env or --home) always wins.

Run: ``uv run --with pytest pytest skills/bibliographer/tests/test_home_resolution.py -q``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

bib = pytest.importorskip("bib")  # importorskip: needs libkit + siblings on path


def test_default_home_reads_env(monkeypatch, tmp_path):
    lib = tmp_path / "mylib"
    monkeypatch.setenv("BIBLIOGRAPHER_HOME", str(lib))
    assert bib._default_home() == lib


def test_default_home_falls_back_when_unset(monkeypatch):
    monkeypatch.delenv("BIBLIOGRAPHER_HOME", raising=False)
    assert bib._default_home() == bib.FALLBACK_HOME


def test_load_dotenv_then_default_home(monkeypatch, tmp_path):
    """The footgun fix: $BIBLIOGRAPHER_HOME defined only in an .env is picked up because
    _load_dotenv runs before _default_home() reads it."""
    lib = tmp_path / "fromdotenv"
    env = tmp_path / ".env"
    env.write_text(f"BIBLIOGRAPHER_HOME={lib}\n", encoding="utf-8")
    monkeypatch.delenv("BIBLIOGRAPHER_HOME", raising=False)
    monkeypatch.chdir(tmp_path)            # cwd/.env is on the search path
    bib._load_dotenv(None)
    assert bib._default_home() == lib


def test_real_env_var_beats_dotenv(monkeypatch, tmp_path):
    """An already-set env var must not be overridden by an .env file."""
    real = tmp_path / "real"
    env = tmp_path / ".env"
    env.write_text(f"BIBLIOGRAPHER_HOME={tmp_path / 'dotenv'}\n", encoding="utf-8")
    monkeypatch.setenv("BIBLIOGRAPHER_HOME", str(real))
    monkeypatch.chdir(tmp_path)
    bib._load_dotenv(None)
    assert bib._default_home() == real
