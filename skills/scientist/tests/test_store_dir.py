"""resolve_store_dirname: new stores use .scientist/, but an existing legacy
.archivist/ store is reused in place (no reindex). Pure — no libkit/keys."""
from pathlib import Path

from store._store import resolve_store_dirname, STORE_DIRNAME, LEGACY_STORE_DIRNAME


def test_default_is_scientist(tmp_path: Path):
    assert resolve_store_dirname(tmp_path) == STORE_DIRNAME


def test_legacy_archivist_reused_in_place(tmp_path: Path):
    (tmp_path / LEGACY_STORE_DIRNAME).mkdir()
    assert resolve_store_dirname(tmp_path) == LEGACY_STORE_DIRNAME


def test_scientist_preferred_when_both_exist(tmp_path: Path):
    (tmp_path / LEGACY_STORE_DIRNAME).mkdir()
    (tmp_path / STORE_DIRNAME).mkdir()
    assert resolve_store_dirname(tmp_path) == STORE_DIRNAME


def test_existing_scientist_used(tmp_path: Path):
    (tmp_path / STORE_DIRNAME).mkdir()
    assert resolve_store_dirname(tmp_path) == STORE_DIRNAME
