"""Scaffolding / authoring command handlers: ``new``, ``intake``, ``catalog``
(plus the ``--route`` parser).

``new`` lays out an experiment folder + starter README/experiment.yml and indexes
it; ``intake`` files a delivery into an experiment per LAYOUT.md (dry-run by
default, copy+reindex on ``--commit``); ``catalog`` exports the human/JSON index.
Both ``new`` and ``intake`` reuse the shared :func:`_index_experiment` walker.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import provenance
from ..cli_utils import die, emit_json
from . import _intake, _meta
from ._store import Store
from ._cli_common import _find_experiment_dir
from ._cli_index import _index_experiment


async def cmd_new(store: Store, args: argparse.Namespace) -> None:
    """Scaffold a new experiment folder: subdirs + a prose README + a starter
    experiment.yml (the structured metadata), then index it."""
    parsed = _meta.parse_experiment_dirname(f"{args.exp_id} - {args.name}")
    if not parsed:
        die(f"invalid experiment id/name (expected K1-YYMMXX): {args.exp_id!r}")
    folder = store.home / f"{args.exp_id} - {args.name}"
    if folder.exists():
        die(f"folder already exists: {folder}")
    for sub in ("raw", "data", "protocol", "reports", "analysis"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text(
        _meta.readme_template({"exp_id": args.exp_id, "name": args.name}), encoding="utf-8")
    meta = provenance.validate({
        "exp_id": args.exp_id, "name": args.name, "status": "planned",
        "cro": args.cro, "model": args.model,
        "cro_study_ids": [args.study_id] if args.study_id else [],
    })
    provenance.write_sidecar(folder, meta)
    await _index_experiment(store, folder.resolve(), parsed, verbose=not args.json)
    if args.json:
        emit_json({"created": store.relpath(folder), "exp_id": args.exp_id})
    else:
        print(f"created {store.relpath(folder)} (raw/ data/ protocol/ reports/ analysis/ "
              f"+ README.md + experiment.yml)")
        print(f"indexed as {args.exp_id}; write README.md prose + fill experiment.yml")


def _parse_routes(raw: list[str] | None) -> dict[str, str]:
    """Parse repeatable ``--route "NAME=subdir"`` flags into a {name: subdir} map.

    NAME is a source file's basename; subdir must be one of LAYOUT's subfolders. The
    agent supplies these after reading the delivery — the *content* judgment intake no
    longer guesses (a document's role depends on what it contains)."""
    routes: dict[str, str] = {}
    for item in raw or []:
        name, sep, sub = item.partition("=")
        name, sub = name.strip(), sub.strip().lower()
        if not sep or not name or not sub:
            die(f"bad --route {item!r}: expected NAME=subdir "
                f"(subdir one of {', '.join(_intake.SUBDIRS)})")
        if sub not in _intake.SUBDIRS:
            die(f"bad --route {item!r}: subdir must be one of {', '.join(_intake.SUBDIRS)}")
        routes[name] = sub
    return routes


async def cmd_intake(store: Store, args: argparse.Namespace) -> None:
    """File a delivery (folder or files) into an experiment per LAYOUT.md.

    Copies (never moves) from the source; dry-run by default — review the plan,
    then re-run with --commit to copy + index. A document's *role* (protocol vs
    reports vs raw) is the agent's judgment, supplied per file with repeatable
    `--route "NAME=subdir"`; unrouted files fall back to a format/`raw` default the
    dry-run marks as a guess to confirm. See references/search-index.md.
    """
    import shutil

    src = Path(args.source).expanduser()
    if not src.exists():
        die(f"source not found: {src}")
    sources = sorted(p for p in src.rglob("*") if p.is_file()) if src.is_dir() else [src]

    found = _find_experiment_dir(store.home, args.experiment)
    if not found:
        die(f"no experiment matching {args.experiment!r} — scaffold it first with `sci new`")
    exp_dir, parsed = found
    routes = _parse_routes(getattr(args, "route", None))
    plan = _intake.plan_intake(sources, exp_dir, routes=routes)

    if args.json and not args.commit:
        emit_json({"experiment": parsed["exp_id"], "dry_run": True,
                   "plan": [{"src": str(p["src"]), "dest": store.relpath(p["dest"]),
                             "subdir": p["subdir"], "routed_by": p["routed_by"],
                             "collision": p["exists"]} for p in plan]})
        return
    if not args.commit:
        print(f"intake plan for {parsed['exp_id']} (dry-run — nothing copied):")
        by_sub: dict[str, int] = {}
        guessed = 0
        for p in plan:
            by_sub[p["subdir"]] = by_sub.get(p["subdir"], 0) + 1
            flag = "  ⚠ overwrites existing" if p["exists"] else ""
            mark = "  ? unreviewed default" if p["routed_by"] in ("default", "ext") else ""
            guessed += 1 if mark else 0
            print(f"  {p['subdir']:8} ← {p['src'].name}{flag}{mark}")
        print(f"  ({len(plan)} files: " + ", ".join(f"{n} {s}" for s, n in sorted(by_sub.items())) + ")")
        if guessed:
            print(f"  {guessed} file(s) fell back to a default placement — read them and re-route any "
                  f"protocol/reports/data with --route \"NAME=subdir\".")
        print("re-run with --commit to copy these in and index.")
        return

    copied = 0
    for p in plan:
        p["dest"].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p["src"], p["dest"])
        copied += 1
    result = await _index_experiment(store, exp_dir, parsed, verbose=not args.json)
    if args.json:
        emit_json({"experiment": parsed["exp_id"], "copied": copied, "indexed": result})
    else:
        print(f"copied {copied} files into {parsed['exp_id']} and reindexed "
              f"({result['files_indexed']} files total)")


async def cmd_catalog(store: Store, args: argparse.Namespace) -> None:
    """Export the experiment catalog: CATALOG.md (human index) + catalog.json."""
    import json

    exps = await store.experiments()
    exps.sort(key=lambda r: r.get("exp_id") or "")
    clean = [{k: v for k, v in e.items() if not k.startswith("_") and k != "content_hash"}
             for e in exps]
    md_path = store.home / "CATALOG.md"
    json_path = store.home / store._store_dirname / "catalog.json"
    md_path.write_text(_meta.catalog_markdown(exps), encoding="utf-8")
    json_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2, default=str, sort_keys=True),
                         encoding="utf-8")
    if args.json:
        emit_json({"experiments": len(exps), "markdown": store.relpath(md_path),
                   "json": store.relpath(json_path)})
    else:
        print(f"wrote {store.relpath(md_path)} and {store.relpath(json_path)} "
              f"({len(exps)} experiments)")
