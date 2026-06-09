"""The store dir is always .scientist/. Pure — no libkit/keys."""
from pathlib import Path

from scientist.store._store import STORE_DIRNAME


def test_store_dirname_is_scientist():
    assert STORE_DIRNAME == ".scientist"
