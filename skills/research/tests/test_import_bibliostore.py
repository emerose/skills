"""`_import_bibliostore()` locates the sibling bibliographer skill.

``BiblioStore`` lives in the importable ``bibliographer`` package
(``bibliographer/bibliographer/store.py``, ``from bibliographer.store import BiblioStore``). This
test pins the parent-walk that finds it, hermetically: a fake skill tree is built under
``tmp_path`` and ``literature.__file__`` is pointed at a fake ``literature.py`` inside it, so the
walk resolves the fake sibling rather than the real repo checkout. No DuckDB / libkit is touched.

Run: ``uv run --with-editable skills/research pytest
skills/research/tests/test_import_bibliostore.py -q``.
"""
from __future__ import annotations

import sys

import pytest

import research.literature as lit


@pytest.fixture
def _isolate_import(monkeypatch):
    """Reset the cached class + scrub any importable bibliographer so the walk, not a leftover
    module, decides resolution. Restores sys.path on teardown."""
    monkeypatch.setattr(lit, "_BIBLIOSTORE", None)
    saved_path = list(sys.path)
    for mod in ("bibliographer", "bibliographer.store"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    yield
    sys.path[:] = saved_path
    for mod in ("bibliographer", "bibliographer.store"):
        sys.modules.pop(mod, None)


def _fake_skill_tree(root):
    """Build ``root/research/research/literature.py`` plus a sibling ``bibliographer`` package.
    Returns ``(fake literature.py path, skill-root expected on sys.path, store marker)``."""
    pkg_dir = root / "research" / "research"
    pkg_dir.mkdir(parents=True)
    fake_lit = pkg_dir / "literature.py"
    fake_lit.write_text("# fake\n", encoding="utf-8")

    marker = "BiblioStore_package"
    body = f"class BiblioStore:\n    marker = {marker!r}\n"
    pkg = root / "bibliographer" / "bibliographer"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "store.py").write_text(body, encoding="utf-8")
    return fake_lit, str(root / "bibliographer"), marker


def test_resolves_package_layout(tmp_path, monkeypatch, _isolate_import):
    fake_lit, skill_root, marker = _fake_skill_tree(tmp_path)
    monkeypatch.setattr(lit, "__file__", str(fake_lit))

    BiblioStore = lit._import_bibliostore()

    assert BiblioStore.marker == marker          # the sibling *package* store
    assert skill_root in sys.path                # skill root inserted so `import bibliographer` resolves
    assert lit._BIBLIOSTORE is BiblioStore       # cached for the process


def test_missing_bibliographer_raises(tmp_path, monkeypatch, _isolate_import):
    pkg_dir = tmp_path / "research" / "research"
    pkg_dir.mkdir(parents=True)
    fake_lit = pkg_dir / "literature.py"
    fake_lit.write_text("# fake\n", encoding="utf-8")
    monkeypatch.setattr(lit, "__file__", str(fake_lit))

    with pytest.raises(lit.LiteratureError, match="can't locate the bibliographer skill"):
        lit._import_bibliostore()
