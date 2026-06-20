"""Make the skill importable from the tests.

Two import surfaces are exposed: the `bibliographer` package (library —
`from bibliographer.store import BiblioStore`) lives at the skill root, and the
`bib` CLI module lives under `scripts/`. Both dirs go on `sys.path`.

Run the suite with uv (it pulls the test-time deps without a virtualenv):

    # fast tests only (test_store skips if libkit isn't present):
    uv run --with pytest --with httpx pytest skills/bibliographer/tests/ -q

    # including the store integration test:
    uv run --with pytest --with httpx --with "libkit>=0.5.0" \
            --with diskcache --with platformdirs \
            pytest skills/bibliographer/tests/ -q
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "scripts"))  # the `bib` CLI module
sys.path.insert(0, str(_root))               # the `bibliographer` package
