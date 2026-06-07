"""Make the skill's `scripts/` modules importable from the tests.

Run the suite with uv (it pulls test-time deps without a virtualenv):

    uv run --with pytest pytest skills/extractor/tests/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
