#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["openpyxl>=3.1", "pyyaml>=6.0", "xlrd>=2.0", "python-docx>=1.1"]
# ///
"""Run an experiment's data/extract.py to (re)generate its data/*.csv from raw/,
recording per-file provenance in experiment.yml.

The per-experiment script lives at <exp>/data/extract.py and defines:

    def build(x):
        x.sheet("01_qpcr_cp_dcp.csv", "raw/...Cp-dCp....xlsx")
        x.sheet("02_qpcr_summary.csv", "raw/...qPCR....xlsx", sheet="Test ASOs")
        x.crc_long("03_crc_pct_kd.csv", "raw/...CRC graphs.pzfx")

`x` (an Extraction) provides the shared, generic helpers; all experiment-specific
config and tweaks live in that script, so this skill stays idiosyncrasy-free. For
bespoke cases, read raw rows with x.xlsx(...) / x.pzfx(...) and emit with x.table().

Usage:
    extract.py "<exp dir>"            # dry run → writes previews to data/_preview/, no yml
    extract.py "<exp dir>" --commit   # writes data/*.csv + updates experiment.yml provenance
    extract.py "<exp dir>" --script /path/to/extract.py --preview /tmp/out   # overrides
"""
from __future__ import annotations
import argparse, csv, hashlib, io, runpy, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _readers as R  # noqa: E402


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _rows_to_bytes(header, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


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
                 cols=("plate", "aso", "log_conc", "replicate", "pct_kd")):
        """Reshape a GraphPad .pzfx (one table per plate, one Y column per ASO, one
        subcolumn per replicate) into a tidy long table — the convention's default."""
        header = list(cols)
        rows = []
        for plate, xvals, ycols in self.pzfx(src):
            for aso, subs in ycols:
                if not aso:
                    continue
                for rep, sub in enumerate(subs):
                    for i, val in enumerate(sub):
                        if val == "":
                            continue
                        x = xvals[i] if i < len(xvals) else ""
                        rows.append([plate, aso, x, rep, val])
        self._emit(name, header, rows, [src])


def _load_build(script: Path):
    ns = runpy.run_path(str(script))
    if "build" not in ns:
        raise SystemExit(f"{script} does not define build(x)")
    return ns["build"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", help="experiment folder (path)")
    ap.add_argument("--script", help="extract.py to run (default <exp>/data/extract.py)")
    ap.add_argument("--commit", action="store_true", help="write data/*.csv + experiment.yml provenance")
    ap.add_argument("--preview", help="dry-run output dir (default <exp>/data/_preview)")
    args = ap.parse_args()

    exp = Path(args.exp).resolve()
    if not exp.is_dir():
        raise SystemExit(f"no such experiment dir: {exp}")
    repo = exp.parent
    script = Path(args.script) if args.script else exp / "data" / "extract.py"
    if not script.is_file():
        raise SystemExit(f"no extract script: {script}")

    x = Extraction(exp, repo)
    _load_build(script)(x)

    out_dir = (exp / "data") if args.commit else Path(args.preview or exp / "data" / "_preview")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{'COMMIT' if args.commit else 'DRY-RUN'} → {out_dir}\n")
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
        # Unified provenance entry — same shape archivist uses for README.md. The
        # artifact path (data/…) marks this as an extraction; the recipe is an input.
        prov.append({"artifact": f"data/{o['name']}", "artifact_sha256": _sha256(data),
                     "reviewed_at": today,
                     "inputs": [{"path": p, "sha256": s} for p, s in o["inputs"]] + [recipe]})
        print(f"  {o['name']:28} {len(o['rows']):>5} rows x {len(o['header']):>2} cols   "
              f"← {', '.join(Path(p).name for p, _ in o['inputs'])}")

    if args.commit:
        _write_provenance(exp / "experiment.yml", prov)
        print(f"\nrecorded provenance for {len(prov)} data files in experiment.yml")
    else:
        print(f"\n(dry run — previews written, experiment.yml untouched)")


def _write_provenance(sidecar: Path, entries: list[dict]) -> None:
    """Merge data-file entries into the experiment's unified `provenance` list — one
    entry per artifact, data files alongside README.md (artifact path is the kind
    discriminator). Preserves entries owned by archivist (README.md, etc.) and
    supersedes the legacy `data_provenance` key. Uses the same entry shape archivist
    does, so `arx review`/`arx audit` read and preserve these natively."""
    import yaml
    doc = yaml.safe_load(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    doc.pop("data_provenance", None)   # superseded by unified provenance
    ours = {e["artifact"] for e in entries}
    kept = [e for e in (doc.get("provenance") or [])
            if isinstance(e, dict) and e.get("artifact") not in ours]
    doc["provenance"] = sorted(kept + entries, key=lambda e: e["artifact"])
    # width high so long paths (with spaces) aren't line-wrapped/folded
    sidecar.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=4096),
        encoding="utf-8")


if __name__ == "__main__":
    main()
