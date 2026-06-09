"""The extraction engine: an :class:`Extraction` (``x``) bound to one experiment,
the ``build(x)`` runner, and provenance recording for ``data/`` edges.

A per-experiment recipe lives at ``<exp>/data/extract.py`` and defines::

    def build(x):
        x.sheet("01_qpcr_cp_dcp.csv", "raw/...Cp-dCp....xlsx")
        x.sheet("02_qpcr_summary.csv", "raw/...qPCR....xlsx", sheet="Test guides")
        x.crc_long("03_crc_pct_kd.csv", "raw/...CRC graphs.pzfx")

``x`` provides the shared, generic helpers; all experiment-specific config lives in
the recipe so the engine stays idiosyncrasy-free. Readers come from ``labfiles``;
``data/`` provenance edges are written via ``provenance.record_provenance`` (the
recipe ``data/extract.py`` is recorded as the final input of every output).
"""

from __future__ import annotations

import csv
import io
import runpy
from datetime import date
from pathlib import Path

from .. import labfiles as R
from .. import provenance as P


def _rows_to_bytes(header, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _sha256(b: bytes) -> str:
    return P._sha256_bytes(b)


class Extraction:
    """Helpers bound to one experiment. Methods read from raw/ and emit a data file,
    recording its source inputs for provenance."""

    def __init__(self, exp_dir: Path, repo_root: Path):
        self.exp = exp_dir
        self.repo = repo_root
        self.outputs: list[dict] = []   # {name, header, rows, inputs:[(relpath, sha)]}

    # --- source readers (return raw rows for custom recipes) ---
    def xlsx(self, src: str, sheet: str | None = None, drop_blank_rows: bool = True):
        return R.read_xlsx_sheet(self.exp / src, sheet, drop_blank_rows)

    def pzfx(self, src: str):
        return R.read_pzfx_structured(self.exp / src)

    def docx_tables(self, src: str):
        """All tables in a .docx report, in document order (each a list of rows of
        cell strings). For CRO deliverables that ship only as a Word report; select
        the table(s) you need by index and emit with x.table(..., sources=[src])."""
        return R.read_docx_tables(self.exp / src)

    def pdf_pages(self, src: str, pages: list[int] | None = None):
        """Per-page text lines of a PDF report (each page a list of lines, in reading
        order, internal spacing preserved). `pages` = 1-based page numbers to extract.
        For CRO deliverables whose finalized data tables ship only as a PDF (Provantis
        exports etc.); parse the lines into rows and emit with x.table(..., sources=[src])."""
        return R.read_pdf_pages(self.exp / src, pages)

    def _input(self, src: str) -> tuple[str, str]:
        p = self.exp / src
        rel = f"{self.exp.name}/{src}"
        return rel, _sha256(p.read_bytes())

    def _emit(self, name: str, header, rows, srcs: list[str]):
        self.outputs.append({
            "name": name, "header": [str(c) for c in header],
            "rows": [[str(c) for c in r] for r in rows],
            "inputs": [self._input(s) for s in srcs],
        })

    # --- high-level emitters ---
    def sheet(self, name: str, src: str, sheet: str | None = None, drop_blank_rows: bool = True):
        """Emit one worksheet faithfully as a data file."""
        h, rows = self.xlsx(src, sheet, drop_blank_rows)
        self._emit(name, h, rows, [src])

    def table(self, name: str, header, rows, sources: list[str]):
        """Emit an arbitrary table built by the recipe (declare its raw sources)."""
        self._emit(name, header, rows, sources)

    def crc_long(self, name: str, src: str,
                 cols=("plate", "guide", "log_conc", "replicate", "pct_kd")):
        """Reshape a GraphPad .pzfx (one table per plate, one Y column per guide, one
        subcolumn per replicate) into a tidy long table — the convention's default."""
        header = list(cols)
        rows = []
        for plate, xvals, ycols in self.pzfx(src):
            for guide, subs in ycols:
                if not guide:
                    continue
                for rep, sub in enumerate(subs):
                    for i, val in enumerate(sub):
                        if val == "":
                            continue
                        x = xvals[i] if i < len(xvals) else ""
                        rows.append([plate, guide, x, rep, val])
        self._emit(name, header, rows, [src])


def load_build(script: Path):
    ns = runpy.run_path(str(script))
    if "build" not in ns:
        raise SystemExit(f"{script} does not define build(x)")
    return ns["build"]


def run_build(exp: Path, script: Path) -> Extraction:
    """Instantiate an :class:`Extraction` for ``exp`` and run the recipe's ``build(x)``."""
    x = Extraction(exp, exp.parent)
    load_build(script)(x)
    return x


def extract(exp: Path, *, script: Path | None = None, commit: bool = False,
            preview: Path | None = None) -> None:
    """Run an experiment's recipe to (re)generate its ``data/*.csv`` from ``raw/``.

    Dry run (default) writes previews to ``data/_preview/`` and leaves ``experiment.yml``
    untouched. ``commit=True`` writes ``data/*.csv`` and records per-file provenance.
    """
    exp = Path(exp).resolve()
    if not exp.is_dir():
        raise SystemExit(f"no such experiment dir: {exp}")
    repo = exp.parent
    script = Path(script) if script else exp / "data" / "extract.py"
    if not script.is_file():
        raise SystemExit(f"no extract script: {script}")

    x = run_build(exp, script)

    out_dir = (exp / "data") if commit else Path(preview or exp / "data" / "_preview")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{'COMMIT' if commit else 'DRY-RUN'} → {out_dir}\n")
    # the recipe that produced these files is itself a dependency → recorded as an input
    try:
        recipe_rel = str(script.resolve().relative_to(repo))
    except ValueError:
        recipe_rel = script.name
    recipe = {"path": recipe_rel, "sha256": _sha256(script.read_bytes())}
    today = date.today().isoformat()
    prov = []
    for o in x.outputs:
        data = _rows_to_bytes(o["header"], o["rows"])
        (out_dir / o["name"]).write_bytes(data)
        # Unified provenance entry — same shape the README review uses. The artifact
        # path (data/…) marks this as an extraction; the recipe is an input.
        prov.append({"artifact": f"data/{o['name']}", "artifact_sha256": _sha256(data),
                     "reviewed_at": today,
                     "inputs": [{"path": p, "sha256": s} for p, s in o["inputs"]] + [recipe]})
        print(f"  {o['name']:28} {len(o['rows']):>5} rows x {len(o['header']):>2} cols   "
              f"← {', '.join(Path(p).name for p, _ in o['inputs'])}")

    if commit:
        P.record_provenance(exp, prov, repo_root=repo)
        print(f"\nrecorded provenance for {len(prov)} data files in experiment.yml")
    else:
        print("\n(dry run — previews written, experiment.yml untouched)")
