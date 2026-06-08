"""Make the skill's top-level packages importable from the tests.

Run the suite with uv (it pulls test-time deps without a virtualenv):

    uv run --with pytest pytest skills/scientist/tests/ -q
"""

import sys
from pathlib import Path

# skills/scientist/ — the package root holding provenance/ labfiles/ extraction/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
