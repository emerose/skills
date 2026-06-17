"""Derivation provenance recording — repo-relative input paths under a symlinked root.

The bug this guards: ``Derivation._rel`` resolved the input path but compared it against
an *unresolved* repo root. When the data-repo root is reached through a symlink (macOS
``/tmp`` -> ``/private/var/...`` is the canonical case), the two prefixes mismatch and
``relative_to`` falls back to an ABSOLUTE path — so recorded provenance inputs leak the
machine's real filesystem layout instead of staying repo-relative. The fix resolves both
sides; this test reproduces the symlink and asserts every recorded input is repo-relative.

Run: ``uv run --with pytest --with pandas --with pyyaml pytest skills/scientist/tests/test_derivation.py -q``.
"""
from __future__ import annotations

import os

import pytest

import scientist.grounding as grounding
import scientist.provenance as P


def _build_repo(root):
    """A minimal data repo: <root>/K1-000000 - Demo/ with one data CSV + a derive recipe."""
    exp = root / "K1-000000 - Demo"
    (exp / "data").mkdir(parents=True)
    (exp / "analysis").mkdir(parents=True)
    (exp / "data" / "01_assay.csv").write_text("guide_id,value\nA,1\nB,2\n", encoding="utf-8")
    (exp / "experiment.yml").write_text("exp_id: K1-000000\n", encoding="utf-8")
    recipe = exp / "analysis" / "derive.py"
    recipe.write_text("# derive recipe\n", encoding="utf-8")
    return exp, recipe


def test_derivation_records_repo_relative_inputs_under_symlinked_root(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")

    # A real data repo behind a SYMLINK: link_root -> real_root. resolve() on the link
    # yields real_root, so an unresolved-root relative_to would break (the bug).
    real_root = tmp_path / "real"
    real_root.mkdir()
    exp_real, recipe_real = _build_repo(real_root)

    link_root = tmp_path / "link"
    link_root.symlink_to(real_root, target_is_directory=True)
    # Point the harness's data-root at the SYMLINKED path (as a checkout under /tmp would be).
    monkeypatch.setenv("SCIENTIST_HOME", str(link_root))

    from scientist import experiments as E

    # Reach the study through the symlinked root and run a derivation that reads a data
    # table and writes a derived table — exactly the path the recorder takes.
    study = E.k1_000000
    with grounding.derivation(study, str(recipe_real)) as d:
        src = study.assay                      # tracked read -> captured input
        d.write_table("derived.csv", src.assign(value2=src["value"] * 10))

    sidecar = P._load_raw(exp_real)
    entries = [e for e in (sidecar.get("provenance") or []) if isinstance(e, dict)]
    assert entries, "derivation should have written a provenance entry"
    all_inputs = [i for e in entries for i in (e.get("inputs") or [])]
    assert all_inputs, "the derivation entry should record inputs"

    for i in all_inputs:
        path = i["path"]
        assert not os.path.isabs(path), f"recorded input path is absolute, not repo-relative: {path}"
        # repo-relative => begins with the experiment folder name, never a /private/... prefix
        assert path.startswith("K1-000000 - Demo/"), f"input not repo-relative: {path}"

    # the data table input and the recipe are both present, both repo-relative
    paths = {i["path"] for i in all_inputs}
    assert "K1-000000 - Demo/data/01_assay.csv" in paths
    assert "K1-000000 - Demo/analysis/derive.py" in paths
