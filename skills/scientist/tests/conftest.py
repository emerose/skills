"""Make the consolidated ``scientist`` package importable from the tests.

Run the suite with uv (it pulls test-time deps without a virtualenv):

    uv run --with pytest pytest skills/scientist/tests/ -q

A sibling editable install (e.g. a stale ``scientist`` in another ``.venv``) can
register a MetaPathFinder that shadows this tree's ``scientist`` package regardless
of ``sys.path`` order. To guarantee the suite always exercises THIS package's
source, we both prepend the package root to ``sys.path`` and evict any
already-imported ``scientist`` module (and its submodules) that resolves outside
this tree, so the next import re-binds it here.
"""

import importlib
import sys
from pathlib import Path

# skills/scientist/ — the dir that CONTAINS the `scientist` package.
PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

# Evict a ``scientist`` (or any submodule) already bound to a location OUTSIDE this
# tree (a stale editable install), so imports below resolve to PKG_ROOT.
_mod = sys.modules.get("scientist")
_file = getattr(_mod, "__file__", None)
if _mod is not None and _file is not None and not str(Path(_file).resolve()).startswith(str(PKG_ROOT)):
    for _k in [k for k in sys.modules if k == "scientist" or k.startswith("scientist.")]:
        del sys.modules[_k]

# Drop any MetaPathFinder that an external editable install registered for the
# package (its finder otherwise outranks our sys.path entry on the next import).
for _finder in list(sys.meta_path):
    _mod_file = getattr(sys.modules.get(type(_finder).__module__, None), "__file__", "") or ""
    if "__editable__" in type(_finder).__module__ and not str(Path(_mod_file).resolve() if _mod_file else "").startswith(str(PKG_ROOT)):
        # Only drop finders that don't point into this tree.
        try:
            sys.meta_path.remove(_finder)
        except ValueError:
            pass

importlib.invalidate_caches()
