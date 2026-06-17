"""Make the skill's `scripts/` modules importable from the tests.

Run the suite with uv (it pulls the test-time deps without a virtualenv):

    # fast tests only (test_store skips if libkit isn't present):
    uv run --with pytest --with httpx pytest skills/bibliographer/tests/ -q

    # including the store integration test:
    uv run --with pytest --with httpx --with "libkit>=0.2.2" \
            --with diskcache --with platformdirs \
            pytest skills/bibliographer/tests/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
