"""Analysis reproduction audit — `provenance.reproduce` / `sci reproduce`.

Re-runs an experiment's ``analysis/derive.py`` and checks the three independent verdicts:

* **runs** — ``derive.main()`` executes (an erroring recipe is flagged),
* **reproduces** — the regenerated ``analysis/tables|fig/*`` match the recorded artifacts
  within tolerance (a tampered recorded artifact is flagged non-reproducing),
* **reads_only_data** — the derivation read only the experiment's ``data/`` (a recipe that
  reaches into ``raw/`` is flagged, even when its numbers still reproduce).

Each test builds a tiny synthetic experiment with a real ``derive.py`` (mirroring
``test_derivation.py``), records a baseline by running the derivation once normally, then
audits it with :func:`provenance.reproduce.reproduce`.

Run: ``uv run --with pytest --with pandas --with pyyaml pytest skills/scientist/tests/test_reproduce.py -q``.
"""
from __future__ import annotations

import sys

import pytest

import scientist.grounding as grounding          # noqa: F401 — ensures THIS tree's package binds
from scientist.provenance import reproduce as R


# --------------------------------------------------------------------------- #
# fixtures / builders
# --------------------------------------------------------------------------- #
FAITHFUL_DERIVE = '''\
from scientist import grounding
from scientist.experiments import {code} as k

def doubled(study):
    df = study.assay                       # tracked data/ read
    return df.assign(value2=df["value"] * 2)

def main():
    with grounding.derivation(k, __file__) as d:
        d.write_table("doubled.csv", doubled(k))
'''

# Reads raw/raw.csv DIRECTLY (off-data) inside the derivation, but still derives its table
# only from data/ — so it reproduces yet violates reads-only-data.
OFF_DATA_DERIVE = '''\
import os
import pandas as pd
from scientist import grounding
from scientist.experiments import {code} as k

def main():
    with grounding.derivation(k, __file__) as d:
        _raw = pd.read_csv(os.path.join(os.path.dirname(__file__), "..", "raw", "raw.csv"))
        df = k.assay
        d.write_table("doubled.csv", df.assign(value2=df["value"] * 2))
'''

ERRORING_DERIVE = '''\
from scientist import grounding
from scientist.experiments import {code} as k

def main():
    with grounding.derivation(k, __file__) as d:
        raise ValueError("boom")
'''

# Writes a table AND a figure (exercises the figure-comparison path).
FIG_DERIVE = '''\
from scientist import grounding
from scientist.experiments import {code} as k

def doubled(study):
    df = study.assay
    return df.assign(value2=df["value"] * 2)

def plot(study):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    df = study.assay
    fig, ax = plt.subplots()
    ax.bar(df["guide_id"], df["value"])
    return fig

def main():
    with grounding.derivation(k, __file__) as d:
        d.write_table("doubled.csv", doubled(k))
        d.write_fig("bars.png", plot(k))
'''


def _build_exp(root, exp_id, derive_src):
    """A minimal data repo ``<root>/<exp_id> - Demo`` with one data CSV, a raw CSV, and a
    derive recipe. Returns the experiment dir."""
    name = f"{exp_id} - Demo"
    exp = root / name
    (exp / "data").mkdir(parents=True)
    (exp / "raw").mkdir(parents=True)
    (exp / "analysis").mkdir(parents=True)
    (exp / "data" / "01_assay.csv").write_text(
        "guide_id,value\nA,1\nB,2\nC,3\n", encoding="utf-8")
    (exp / "raw" / "raw.csv").write_text(
        "guide_id,value\nA,1\nB,2\nC,3\n", encoding="utf-8")
    (exp / "experiment.yml").write_text(f"exp_id: {exp_id}\n", encoding="utf-8")
    code = exp_id.lower().replace("-", "_")               # K1-000000 -> k1_000000
    (exp / "analysis" / "derive.py").write_text(derive_src.format(code=code), encoding="utf-8")
    return exp


def _study(exp_id):
    from scientist import experiments as E
    return getattr(E, exp_id.lower().replace("-", "_"))


def _evict_derive(exp_id):
    """Drop the cached derive module so a rewritten derive.py is re-loaded."""
    sys.modules.pop(f"experiments_derive_{exp_id.replace('-', '_')}", None)


def _record_baseline(exp_id):
    """Run the derivation once NORMALLY to write the recorded artifacts + provenance."""
    _evict_derive(exp_id)
    _study(exp_id).derive.main()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Each test gets a fresh experiments cache so a reused exp_id never resolves to a
    previous test's tmp path."""
    from scientist import experiments as E
    E._studies.clear()
    yield
    E._studies.clear()


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_faithful_analysis_reproduces(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    exp_id = "K1-000010"
    exp = _build_exp(tmp_path, exp_id, FAITHFUL_DERIVE)
    monkeypatch.setenv("SCIENTIST_HOME", str(tmp_path))
    _record_baseline(exp_id)

    result = R.reproduce(exp)

    assert result["status"] == "REPRODUCES"
    assert result["runs"] and result["reproduces"] and result["reads_only_data"]
    assert result["off_data_reads"] == []
    arts = {a["artifact"]: a for a in result["artifacts"]}
    assert "analysis/tables/doubled.csv" in arts
    # a deterministic table reproduces byte-for-byte
    assert arts["analysis/tables/doubled.csv"]["verdict"] == "exact"
    # the recorded analysis artifact was NOT overwritten by the audit (scratch only)
    assert (exp / "analysis" / "tables" / "doubled.csv").is_file()


def test_tampered_artifact_flagged_non_reproducing(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    exp_id = "K1-000011"
    exp = _build_exp(tmp_path, exp_id, FAITHFUL_DERIVE)
    monkeypatch.setenv("SCIENTIST_HOME", str(tmp_path))
    _record_baseline(exp_id)

    # Tamper the RECORDED artifact: the re-run will recompute the correct numbers and
    # they will no longer match what's on disk.
    recorded = exp / "analysis" / "tables" / "doubled.csv"
    text = recorded.read_text(encoding="utf-8").replace("A,1,2", "A,1,999")
    assert "A,1,999" in text, "test setup: expected to rewrite a known cell"
    recorded.write_text(text, encoding="utf-8")

    result = R.reproduce(exp)

    assert result["runs"] is True
    assert result["reproduces"] is False
    assert result["status"] == "BROKEN"
    art = next(a for a in result["artifacts"] if a["artifact"] == "analysis/tables/doubled.csv")
    assert art["verdict"] == "mismatch"
    assert "value2" in art["detail"]


def test_off_data_read_flagged(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    exp_id = "K1-000012"
    exp = _build_exp(tmp_path, exp_id, OFF_DATA_DERIVE)
    monkeypatch.setenv("SCIENTIST_HOME", str(tmp_path))
    _record_baseline(exp_id)

    result = R.reproduce(exp)

    # The numbers still reproduce (the table is derived only from data/)...
    assert result["runs"] is True
    assert result["reproduces"] is True
    # ...but the recipe reached into raw/, so reads-only-data fails and overall is BROKEN.
    assert result["reads_only_data"] is False
    assert result["status"] == "BROKEN"
    assert result["off_data_reads"], "expected the raw/ read to be flagged"
    flagged = " ".join(f["path"] for f in result["off_data_reads"])
    assert "raw" in flagged and "raw.csv" in flagged


def test_erroring_derivation_flagged_non_running(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    exp_id = "K1-000013"
    exp = _build_exp(tmp_path, exp_id, FAITHFUL_DERIVE)
    monkeypatch.setenv("SCIENTIST_HOME", str(tmp_path))
    _record_baseline(exp_id)

    # Swap in a recipe that raises, then re-load it.
    (exp / "analysis" / "derive.py").write_text(
        ERRORING_DERIVE.format(code=exp_id.lower().replace("-", "_")), encoding="utf-8")
    _evict_derive(exp_id)

    result = R.reproduce(exp)

    assert result["runs"] is False
    assert result["status"] == "BROKEN"
    assert "boom" in (result.get("error") or "")


def test_no_derivation(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    exp_id = "K1-000014"
    exp = _build_exp(tmp_path, exp_id, FAITHFUL_DERIVE)
    (exp / "analysis" / "derive.py").unlink()
    monkeypatch.setenv("SCIENTIST_HOME", str(tmp_path))

    result = R.reproduce(exp)
    assert result["status"] == "NO-DERIVATION"
    assert result["runs"] is False


def test_figure_regenerates(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    pytest.importorskip("matplotlib")
    exp_id = "K1-000015"
    exp = _build_exp(tmp_path, exp_id, FIG_DERIVE)
    monkeypatch.setenv("SCIENTIST_HOME", str(tmp_path))
    _record_baseline(exp_id)

    result = R.reproduce(exp)

    assert result["status"] == "REPRODUCES"
    fig = next(a for a in result["artifacts"] if a["artifact"] == "analysis/fig/bars.png")
    # figures are not byte-required to match; exact (same env) or a tolerant dims match both pass
    assert fig["type"] == "fig"
    assert fig["verdict"] in ("exact", "regenerated")


def test_png_dims_helper():
    # 1x1 PNG header: signature + IHDR length/type + width=1 height=1
    import struct
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 7, 5)
    assert R._png_dims(png) == (7, 5)
    assert R._png_dims(b"not a png") is None


# A PROGRAM-level derivation (program/analysis/derive.py): the home for cross-experiment
# *report* comparison artifacts. It legitimately fans in another experiment's tracked
# data — which would be an off-data read for a per-experiment derivation, but is allowed
# at program scope.
PROGRAM_DERIVE = '''\
from scientist import grounding
from scientist.experiments import program
from scientist.experiments import {code} as k

def main():
    with grounding.derivation(program, __file__) as d:
        df = k.assay                       # cross-experiment tracked read (allowed at program scope)
        d.write_table("compare.csv", df.assign(value2=df["value"] * 2))
'''


def test_program_derivation_reproduces(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    from scientist import experiments as E

    exp_id = "K1-000020"
    _build_exp(tmp_path, exp_id, FAITHFUL_DERIVE)        # supplies k.assay (data/01_assay.csv)
    prog = tmp_path / "program"
    (prog / "analysis").mkdir(parents=True)
    code = exp_id.lower().replace("-", "_")
    (prog / "analysis" / "derive.py").write_text(PROGRAM_DERIVE.format(code=code), encoding="utf-8")
    monkeypatch.setenv("SCIENTIST_HOME", str(tmp_path))

    # fresh caches so program + study resolve against this tmp root
    E._program = None
    sys.modules.pop("experiments_derive_program", None)
    E.program.derive.main()                              # record baseline normally

    result = R.reproduce(prog)

    assert result["status"] == "REPRODUCES", result
    assert result["runs"] and result["reproduces"]
    # the cross-experiment data read is NOT flagged at program scope
    assert result["reads_only_data"] is True
    assert result["off_data_reads"] == []
    arts = {a["artifact"]: a for a in result["artifacts"]}
    assert "analysis/tables/compare.csv" in arts
    assert arts["analysis/tables/compare.csv"]["verdict"] == "exact"


def test_render_smoke(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    exp_id = "K1-000016"
    exp = _build_exp(tmp_path, exp_id, FAITHFUL_DERIVE)
    monkeypatch.setenv("SCIENTIST_HOME", str(tmp_path))
    _record_baseline(exp_id)
    text = R.render(R.reproduce(exp))
    assert exp_id in text and "REPRODUCES" in text
    assert "reads-only-data: yes" in text
