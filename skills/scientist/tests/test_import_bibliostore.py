"""`_import_bibliostore()` locates the sibling bibliographer skill across layouts.

The bibliographer skill was refactored so ``BiblioStore`` lives in the importable
``bibliographer`` package (``bibliographer/bibliographer/store.py``,
``from bibliographer.store import BiblioStore``) — there is no ``scripts/_store.py``
anymore. These tests pin the parent-walk that finds it, hermetically: a fake skill
tree is built under ``tmp_path`` and ``literature.__file__`` is pointed at a fake
``literature.py`` inside it, so the walk resolves the fake sibling rather than the
real repo checkout. No DuckDB / libkit is touched.

Run: ``uv run --with-editable skills/scientist pytest
skills/scientist/tests/test_import_bibliostore.py -q``.
"""
from __future__ import annotations

import sys

import pytest

import scientist.grounding.literature as lit


@pytest.fixture
def _isolate_import(monkeypatch):
    """Reset the cached class + scrub any importable bibliographer/_store so the walk,
    not a leftover module, decides resolution. Restores sys.path on teardown."""
    monkeypatch.setattr(lit, "_BIBLIOSTORE", None)
    saved_path = list(sys.path)
    for mod in ("_store", "bibliographer", "bibliographer.store"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    yield
    sys.path[:] = saved_path
    for mod in ("_store", "bibliographer", "bibliographer.store"):
        sys.modules.pop(mod, None)


def _fake_skill_tree(root, *, layout: str):
    """Build ``root/scientist/scientist/grounding/literature.py`` plus a sibling
    bibliographer skill in the given ``layout`` ("package" = current, "scripts" = old).
    Returns ``(fake literature.py path, skill-root expected on sys.path, store marker)``."""
    grounding = root / "scientist" / "scientist" / "grounding"
    grounding.mkdir(parents=True)
    fake_lit = grounding / "literature.py"
    fake_lit.write_text("# fake\n", encoding="utf-8")

    marker = f"BiblioStore_{layout}"
    body = f"class BiblioStore:\n    marker = {marker!r}\n"
    if layout == "package":
        pkg = root / "bibliographer" / "bibliographer"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "store.py").write_text(body, encoding="utf-8")
        skill_root = str(root / "bibliographer")
    else:  # old scripts/_store.py layout
        scripts = root / "bibliographer" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "_store.py").write_text(body, encoding="utf-8")
        skill_root = str(scripts)
    return fake_lit, skill_root, marker


def test_resolves_current_package_layout(tmp_path, monkeypatch, _isolate_import):
    fake_lit, skill_root, marker = _fake_skill_tree(tmp_path, layout="package")
    monkeypatch.setattr(lit, "__file__", str(fake_lit))

    BiblioStore = lit._import_bibliostore()

    assert BiblioStore.marker == marker          # the sibling *package* store
    assert skill_root in sys.path                # skill root inserted so `import bibliographer` resolves
    assert lit._BIBLIOSTORE is BiblioStore       # cached for the process


def test_resolves_legacy_scripts_layout(tmp_path, monkeypatch, _isolate_import):
    fake_lit, scripts_dir, marker = _fake_skill_tree(tmp_path, layout="scripts")
    monkeypatch.setattr(lit, "__file__", str(fake_lit))

    BiblioStore = lit._import_bibliostore()

    assert BiblioStore.marker == marker          # back-compat: old scripts/_store.py still found
    assert scripts_dir in sys.path


def test_missing_bibliographer_raises(tmp_path, monkeypatch, _isolate_import):
    grounding = tmp_path / "scientist" / "scientist" / "grounding"
    grounding.mkdir(parents=True)
    fake_lit = grounding / "literature.py"
    fake_lit.write_text("# fake\n", encoding="utf-8")
    monkeypatch.setattr(lit, "__file__", str(fake_lit))

    with pytest.raises(lit.LiteratureError, match="can't locate the bibliographer skill"):
        lit._import_bibliostore()
