"""Indexing command handlers: ``init``, ``index``, ``reindex``, ``index-claims``
(plus the shared ``_index_experiment`` walker and the grounding-report loader).

These are the store *writers* — they walk experiment folders, embed/catalogue files,
upsert experiment + claim documents, and prune stale claims. Structured metadata
comes only from the schema'd ``experiment.yml`` sidecar via :mod:`provenance`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .. import provenance
from ..cli_utils import die, emit_json
from . import _files, _meta
from ._store import STORE_DIRNAME, Store
from ._cli_common import MAX_EMBED_BYTES, _find_experiment_dir


async def cmd_init(store: Store, _args: argparse.Namespace) -> None:
    # opening already created the store; ensure a .gitignore covers it.
    gi = store.home / ".gitignore"
    needed = [f"{STORE_DIRNAME}/", ".DS_Store"]
    existing = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    add = [ln for ln in needed if ln not in existing]
    if add:
        with gi.open("a", encoding="utf-8") as fh:
            if existing and existing[-1].strip():
                fh.write("\n")
            fh.write("\n".join(add) + "\n")
    print(f"initialized scientist store at {store.home / store._store_dirname}")
    if add:
        print(f"  added to .gitignore: {', '.join(add)}")


async def cmd_index(store: Store, args: argparse.Namespace) -> None:
    found = _find_experiment_dir(store.home, args.experiment)
    if not found:
        die(f"no experiment matching {args.experiment!r} under {store.home}")
    exp_dir, parsed = found
    result = await _index_experiment(store, exp_dir, parsed, verbose=not args.json)
    if args.json:
        emit_json(result)
    else:
        print(f"indexed {result['exp_id']}: {result['files_indexed']} files "
              f"({result['narrative']} narrative, {result['tabular']} tabular, "
              f"{result['binary']} binary)")


async def cmd_reindex(store: Store, args: argparse.Namespace) -> None:
    results = []
    exp_dirs = [(c, p) for c in sorted(store.home.iterdir())
                if c.is_dir() and (p := _meta.parse_experiment_dirname(c.name))]
    for i, (child, parsed) in enumerate(exp_dirs, 1):
        r = await _index_experiment(store, child.resolve(), parsed, verbose=not args.json)
        results.append(r)
        if not args.json:
            print(f"  [{i}/{len(exp_dirs)}] {r['exp_id']}: {r['files_indexed']} files "
                  f"({r['narrative']}n/{r['tabular']}t/{r['binary']}b)", flush=True)
    if args.json:
        emit_json(results)
    else:
        total = sum(r["files_indexed"] for r in results)
        print(f"indexed {len(results)} experiments, {total} files total")


async def _index_experiment(store: Store, exp_dir: Path,
                            parsed: dict[str, Any], *, verbose: bool) -> dict[str, Any]:
    counts = {"narrative": 0, "tabular": 0, "binary": 0, "files_indexed": 0}
    for f in _files.iter_experiment_files(exp_dir):
        abs_path: Path = f["abs_path"]
        rel = store.relpath(abs_path)
        size = abs_path.stat().st_size
        rec: dict[str, Any] = {
            "exp_id": parsed["exp_id"],
            "path": rel,
            "filename": f["filename"],
            "role": f["role"],
            "file_type": f["ext"].lstrip("."),
            "size": size,
            "sha256": _files.sha256_file(abs_path),
        }
        cls = f["classification"]
        try:
            if cls == "narrative" and size <= MAX_EMBED_BYTES:
                try:
                    rec["indexed_as"] = _meta.INDEXED_CONTENT
                    await store.add_file(rec, ingest_path=abs_path)
                except Exception as e:
                    # Parse/loader failure: don't drop the file — catalogue it as a
                    # descriptor so it's still discoverable, and note why.
                    if verbose:
                        print(f"  ! {rel}: {type(e).__name__}: {e} (catalogued as descriptor)",
                              file=sys.stderr)
                    rec["indexed_as"] = _meta.INDEXED_DESCRIPTOR
                    rec["note"] = f"content not embedded ({type(e).__name__}); catalogued only"
                    await store.add_file(rec, card_markdown=_meta.file_card_markdown(rec))
            elif cls == "tabular":
                schema, preview = _files.schema_and_preview(abs_path)
                rec["indexed_as"] = _meta.INDEXED_SCHEMA
                if schema:
                    rec["schema"] = schema
                card = _meta.file_card_markdown(rec, schema=schema, preview=preview)
                await store.add_file(rec, card_markdown=card)
            else:
                rec["indexed_as"] = _meta.INDEXED_DESCRIPTOR
                if cls == "narrative":
                    rec["note"] = "narrative file too large to embed; catalogued only"
                card = _meta.file_card_markdown(rec)
                await store.add_file(rec, card_markdown=card)
        except Exception as e:  # one bad file shouldn't abort the experiment
            if verbose:
                print(f"  ! {rel}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        counts[cls] += 1
        counts["files_indexed"] += 1

    exp_rec: dict[str, Any] = {
        "exp_id": parsed["exp_id"],
        "name": parsed["name"],
        "title": parsed["name"],
        "folder": store.relpath(exp_dir),
        "file_counts": counts,
    }
    # Structured metadata comes ONLY from the schema'd experiment.yml sidecar — never
    # scraped from README prose. A missing/invalid sidecar leaves metadata minimal
    # (and `check`/`audit` flag it); a bad sidecar surfaces a clear error, not silence.
    sidecar = exp_dir / provenance.SIDECAR_NAME
    if sidecar.is_file():
        try:
            meta = provenance.read_sidecar(exp_dir)
            meta.pop("exp_id", None)        # folder is authoritative for the id
            meta.pop("provenance", None)    # the ledger isn't experiment-card metadata
            exp_rec.update(meta)
            exp_rec.setdefault("name", parsed["name"])
        except provenance.SidecarError as e:
            print(f"  ! {parsed['exp_id']}: {e}", file=sys.stderr)
            exp_rec["metadata_error"] = str(e)
    await store.upsert_experiment(exp_rec)
    summary = {"exp_id": parsed["exp_id"], **counts}
    if not verbose:
        summary["metadata"] = {k: exp_rec.get(k) for k in
                               ("cro", "cro_study_ids", "assays", "asos", "model", "status")}
    return summary


def _load_grounding_report(exp_dir: Path, override: str | None) -> tuple[Path, list[dict[str, Any]]]:
    """Locate + parse the grounding_report.json for an experiment.

    Search order: ``--report PATH`` if given, else ``<exp>/analysis/grounding_report.json``
    then ``<exp>/grounding_report.json`` (the shared :mod:`provenance` locate ladder).
    Returns the resolved path + the claims list. Dies with a clear, actionable error if no
    report is found or it's malformed.
    """
    report_path = provenance.find_report(exp_dir, override or None)
    if report_path is None:
        candidates = [Path(override)] if override else provenance.report_candidates(exp_dir)
        looked = ", ".join(str(p) for p in candidates)
        die(f"no grounding report found (looked: {looked}). Run the claims first, e.g.\n"
            f"  uv run --with-editable skills/scientist pytest "
            f"\"{exp_dir / 'analysis' / 'claims'}\"")
    try:
        data = provenance.load_report(report_path)
    except (OSError, ValueError) as e:
        die(f"could not read grounding report {report_path}: {e}")
    claims = provenance.claims_of(data)
    if claims is None:
        die(f"grounding report {report_path} has no 'claims' list")
    return report_path, claims


async def cmd_index_claims(store: Store, args: argparse.Namespace) -> None:
    """Index the grounded claims from an experiment's grounding_report.json into the
    libkit store as ``kind=claim`` documents, then prune any claims that have been
    removed from the report (rebuildable store)."""
    import json

    found = _find_experiment_dir(store.home, args.experiment)
    if not found:
        die(f"no experiment matching {args.experiment!r} under {store.home}")
    exp_dir, parsed = found
    exp_id = parsed["exp_id"]
    report_path, claims = _load_grounding_report(exp_dir, args.report)

    indexed_ids: list[str] = []
    for claim in claims:
        nodeid = claim.get("id") or ""
        claim_id = _meta.claim_id_for(exp_id, nodeid)
        rec: dict[str, Any] = {
            "exp_id": exp_id,
            "claim_id": claim_id,
            "statement": claim.get("statement") or "",
            "outcome": claim.get("outcome"),
            "strength": claim.get("strength"),
            "claim_kind": claim.get("kind"),
            "caveats": claim.get("caveats"),
            "evidence_json": json.dumps(claim.get("evidence") or {},
                                        ensure_ascii=False, sort_keys=True, default=str),
            "inputs": [{"path": i.get("path"), "sha256": i.get("sha256")}
                       for i in (claim.get("inputs") or [])],
            "source": nodeid,
        }
        await store.upsert_claim(rec)
        indexed_ids.append(claim_id)

    pruned = await store.replace_experiment_claims(exp_id, indexed_ids)
    if args.json:
        emit_json({"exp_id": exp_id, "report": store.relpath(report_path),
                   "indexed": len(indexed_ids), "pruned": pruned})
    else:
        print(f"indexed {len(indexed_ids)} claims for {exp_id} "
              f"(from {store.relpath(report_path)}); pruned {pruned} stale")
