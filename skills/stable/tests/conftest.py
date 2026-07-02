"""Put the skill root and its scripts/ dir on sys.path for the tests.

Lets `import stable` (the package) and `import stable_cli` (scripts/stable_cli.py)
resolve without an install, regardless of pytest's rootdir.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "scripts"):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
