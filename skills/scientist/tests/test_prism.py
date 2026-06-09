"""Tests for the GraphPad Prism `.prism` (zip) reader and the format-sniffing dispatch.

The core tests build a tiny synthetic `.prism` in a tmp dir (no external data), covering
the three table layouts the reader must handle:
  - a `column` table (no X, no leading row-titles column),
  - an XY `survival` table (an X dataset + Y groups; full-precision floats),
  - a `grouped` table (a leading row-titles column + multi-subcolumn `replicate ranges`).

An opt-in parity test (`test_prism_matches_pzfx_pair`) asserts `read_prism` reproduces
`read_pzfx` table-for-table on a real `.prism`/`.pzfx` pair of the same data, when one is
supplied via the `SCIENTIST_PRISM_PAIR` env var; it is skipped by default (CI included).
"""

import os
import json
import zipfile
from pathlib import Path

import pytest

from scientist import labfiles as R


# --------------------------------------------------------------------------- #
# synthetic .prism builder
# --------------------------------------------------------------------------- #
def _single(title, attr):
    return {"@class": "DataSet", "title": title, "attributes": [attr],
            "replicates": [{"@class": "DriverReplicate", "valueType": "real"}]}


def _grouped(title, nsub):
    return {"@class": "DataSet", "title": title, "attributes": ["DS_ATTR_Y"],
            "replicate ranges": [{"range": f"0~{nsub - 1}",
                                  "replicate": {"@class": "DriverReplicate", "valueType": "real"}}]}


def _build_prism(path: Path):
    """Write a minimal but representative `.prism` zip and return the expected
    `read_prism` output (list of (title, header, rows))."""
    sets, sheets, tables = {}, {}, {}      # uid -> json/csv
    data_order = []

    def add_sheet(suid, tuid, title, table):
        table = {"@class": table.pop("class", "DataTable"), "uid": tuid, **table}
        sheets[suid] = {"@class": "DataSheet", "uid": suid, "title": title, "table": table}
        data_order.append(suid)

    # 1) column table: no X, no leading column, two y_single groups
    sets["A1"], sets["B1"] = _single("A", "DS_ATTR_Y"), _single("B", "DS_ATTR_Y")
    add_sheet("S1", "T1", "Column Assay",
              {"class": "DataTable", "format": "column", "dataSets": ["A1", "B1"]})
    tables["T1"] = (2, "1.5,2\n3,4.25\n,5\n")

    # 2) XY survival table: X dataset + two y_single groups; full-precision float
    sets["X2"] = _single("Day", "DS_ATTR_X")
    sets["G1"], sets["G2"] = _single("G1", "DS_ATTR_Y"), _single("G2", "DS_ATTR_Y")
    add_sheet("S2", "T2", "Survival",
              {"class": "XYDataTable", "format": "survival",
               "xDataSet": "X2", "dataSets": ["G1", "G2"]})
    tables["T2"] = (3, "1,1,\n2,,0\n3,72.7179749000000015,1\n")

    # 3) grouped table: leading row-titles column + 3 replicate subcolumns per group
    sets["P3"], sets["Q3"] = _grouped("P", 3), _grouped("Q", 3)
    add_sheet("S3", "T3", "Grouped Assay",
              {"class": "DataTable", "format": "grouped", "dataFormat": "y_replicates",
               "dataSets": ["P3", "Q3"]})
    tables["T3"] = (7, "1,10,11,,20,21,\n2,12,,13,22,,23\n")

    document = {"@class": "Document", "sheets": {"data": data_order, "graphs": [],
                                                 "analyses": [], "info": []}}

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("document.json", json.dumps(document))
        for uid, obj in sets.items():
            z.writestr(f"data/sets/{uid}.json", json.dumps(obj))
        for suid, obj in sheets.items():
            z.writestr(f"data/sheets/{suid}/sheet.json", json.dumps(obj))
        for tuid, (ncol, csv) in tables.items():
            z.writestr(f"data/tables/{tuid}/content.json",
                       json.dumps({"numberOfColumns": ncol, "numberOfRows": csv.count("\n")}))
            z.writestr(f"data/tables/{tuid}/data.csv", csv)

    expected = [
        ("Column Assay", ["", "A_0", "B_1"],
         [["0", "1.5", "2"], ["1", "3", "4.25"], ["2", "", "5"]]),
        ("Survival", ["", "_0", "G1_0", "G2_1"],
         [["0", "1", "1", ""], ["1", "2", "", "0"], ["2", "3", "72.7179749", "1"]]),
        ("Grouped Assay", ["", "P_0", "P_1", "P_2", "Q_3", "Q_4", "Q_5"],
         [["0", "10", "11", "", "20", "21", ""],
          ["1", "12", "", "13", "22", "", "23"]]),
    ]
    return expected


# --------------------------------------------------------------------------- #
# synthetic-fixture tests
# --------------------------------------------------------------------------- #
def test_read_prism_layouts(tmp_path):
    p = tmp_path / "synthetic.prism"
    expected = _build_prism(p)
    assert R.read_prism(p) == expected


def test_dispatch_routes_zip_to_prism(tmp_path):
    """read_pzfx sniffs content: a zip `.prism` routes to read_prism (so a recipe can
    point x.pzfx at either a .pzfx or a .prism and get the same shape)."""
    p = tmp_path / "synthetic.prism"
    expected = _build_prism(p)
    assert R.read_pzfx(p) == expected


def test_structured_view(tmp_path):
    p = tmp_path / "synthetic.prism"
    _build_prism(p)
    structured = R.read_prism_structured(p)
    titles = [t for t, _, _ in structured]
    assert titles == ["Column Assay", "Survival", "Grouped Assay"]
    # the survival table's x_values is the first (only) X subcolumn
    _, xvals, ycols = structured[1]
    assert xvals == ["1", "2", "3"]
    assert [y[0] for y in ycols] == ["G1", "G2"]
    assert ycols[0][1] == [["1", "", "72.7179749"]]   # G1's single subcolumn


def test_legacy_binary_raises(tmp_path):
    p = tmp_path / "legacy.pzfx"
    p.write_bytes(b"PCFFGRA4" + b"\x00" * 64)
    with pytest.raises(ValueError, match="legacy binary"):
        R.read_pzfx(p)


def test_sniff(tmp_path):
    zp, bp, xp = tmp_path / "a.prism", tmp_path / "b.pzf", tmp_path / "c.pzfx"
    zp.write_bytes(b"PK\x03\x04rest")
    bp.write_bytes(b"PCFFGRA4rest")
    xp.write_bytes(b"<?xml version='1.0'?><GraphPadPrismFile/>")
    assert R._prism_sniff(zp) == "zip"
    assert R._prism_sniff(bp) == "binary"
    assert R._prism_sniff(xp) == "xml"


# --------------------------------------------------------------------------- #
# real-pair parity (opt-in): read_prism reproduces read_pzfx table-for-table
# --------------------------------------------------------------------------- #
# Set SCIENTIST_PRISM_PAIR="<file.prism>,<file.pzfx>" — two exports of the SAME data —
# to check the readers agree on real data. Skipped by default; no data paths or values
# are committed (keep any real test files out of this repo).
_PAIR = os.environ.get("SCIENTIST_PRISM_PAIR")


def _dedupe_redundant_x(header, rows):
    """The XML `.pzfx` export of a survival/XY table writes both an `XColumn` and an
    `XAdvancedColumn` holding identical X values, so read_pzfx emits the X column twice
    (`_0`, `_1`). The `.prism` has one true X. Drop pzfx's redundant duplicate adjacent
    X subcolumns so the two readers are comparable."""
    xcols = [i for i, n in enumerate(header) if n.startswith("_")]
    drop = {b for a, b in zip(xcols, xcols[1:]) if all(r[a] == r[b] for r in rows)}
    keep = [i for i in range(len(header)) if i not in drop]
    h, k = [], 0
    for i in keep:
        if header[i].startswith("_"):
            h.append(f"_{k}"); k += 1
        else:
            h.append(header[i])
    return h, [[r[i] for i in keep] for r in rows]


@pytest.mark.skipif(not _PAIR, reason="set SCIENTIST_PRISM_PAIR=<prism>,<pzfx> to run")
def test_prism_matches_pzfx_pair():
    prism_path, pzfx_path = (Path(p) for p in _PAIR.split(","))
    prism = R.read_prism(prism_path)
    pzfx = {t: (h, r) for t, h, r in R.read_pzfx(pzfx_path)}
    assert {t for t, _, _ in prism} == set(pzfx)
    for title, ph, pr in prism:
        qh, qr = _dedupe_redundant_x(*pzfx[title])
        assert (ph, pr) == (qh, qr), title
