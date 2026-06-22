"""Argparse wiring + dispatch for the store subcommands (the former ``arx`` CLI),
folded into the ``sci`` entry point.

This is the thin wiring layer: it owns ``register()`` (adds the store subcommands to
an existing ``sci`` parser) and ``dispatch()`` (runs the selected one, a sync wrapper
over the async handler), plus the read-only-classification and store-open machinery.
The command *handlers* themselves live in cohesive submodules grouped by family:

* :mod:`_cli_index`    — ``init``, ``index``, ``reindex``, ``index-claims``
* :mod:`_cli_browse`   — ``list``, ``show``, ``search``, ``query``, ``file``, ``read``, ``entity``
* :mod:`_cli_scaffold` — ``new``, ``intake``, ``catalog``
* :mod:`_cli_audit`    — ``check``, ``audit``, ``meta``, ``fingerprint``, ``review`` (+ the store-free audit report)
* :mod:`_cli_pr`       — ``pr``

Leaf helpers shared by those handlers (home resolution, ``.env`` loading, experiment
discovery, the ``_HomeOnly`` stand-in) live in :mod:`_cli_common`. This module
re-exports the names external callers (``scripts/sci.py``, the tests) import from
``store.cli``, so the public surface is unchanged.

All ``experiment.yml`` access (read/validate/write the sidecar, record provenance,
staleness, review inputs) routes through :mod:`provenance` — never re-implemented.

Configuration: the data-tree root resolves from ``--home``, else ``$SCIENTIST_HOME``,
else cwd; the store dir is ``.scientist/catalog.duckdb``.
Third-party keys (``DEEPINFRA_API_KEY`` / ``DATALAB_API_KEY``) are untouched.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from .. import cli_utils  # noqa: F401  (kept for back-compat imports of store.cli.cli_utils)
from ..cli_utils import die, emit_json  # re-exported for existing call sites

from . import _audit, _files, _generate, _intake, _meta, _pr  # noqa: F401  (re-exported)
from ._store import STORE_DIRNAME, Store, EmbedderConfigError

# Leaf helpers shared with the handler modules (re-exported: external code and tests
# reach several of these through ``store.cli``).
from ._cli_common import (  # noqa: F401
    MAX_EMBED_BYTES,
    _HomeOnly,
    _experiment_dirs,
    _find_experiment_dir,
    _home,
    _load_dotenv,
    _require_initialized,
)

# Command handlers, grouped by family.
from ._cli_index import (  # noqa: F401
    cmd_init,
    cmd_index,
    cmd_reindex,
    cmd_index_claims,
    _index_experiment,
    _load_grounding_report,
)
from ._cli_browse import (  # noqa: F401
    cmd_list,
    cmd_show,
    cmd_search,
    cmd_query,
    cmd_file,
    cmd_read,
    cmd_entity,
)
from ._cli_scaffold import (  # noqa: F401
    cmd_new,
    cmd_intake,
    cmd_catalog,
    _parse_routes,
)
from ._cli_audit import (  # noqa: F401
    cmd_check,
    cmd_audit,
    cmd_meta,
    cmd_fingerprint,
    cmd_review,
    audit_report,
    print_audit_report,
    _staleness_entry,
    _source_files_on_disk,
)
from ._cli_pr import (  # noqa: F401
    cmd_pr,
    _maybe_pr,
    _changed_paths,
)

# The set of subcommands this module owns (so sci can route them here).
STORE_COMMANDS = (
    "init", "index", "reindex", "index-claims", "list", "show", "search", "query",
    "file", "read", "entity", "new", "intake", "meta", "review", "fingerprint",
    "catalog", "check", "audit", "pr",
)


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
COMMANDS = {
    "init": cmd_init,
    "index": cmd_index,
    "reindex": cmd_reindex,
    "index-claims": cmd_index_claims,
    "list": cmd_list,
    "show": cmd_show,
    "search": cmd_search,
    "query": cmd_query,
    "file": cmd_file,
    "read": cmd_read,
    "entity": cmd_entity,
    "new": cmd_new,
    "intake": cmd_intake,
    "catalog": cmd_catalog,
    "check": cmd_check,
    "audit": cmd_audit,
    "meta": cmd_meta,
    "fingerprint": cmd_fingerprint,
    "review": cmd_review,
    "pr": cmd_pr,
}

# Store subcommands that only READ the libkit store open it ``read_only=True``
# (libkit >=0.4.0): a read-only open takes no exclusive write lock, so many of
# them run concurrently instead of serialising. Everything not listed opens
# read-write — the safe default (a *write* command opened read-only crashes with
# ``ReadOnlyStore``; a read command opened read-write just keeps today's locking).
# Verified each reads only (no upsert/merge/delete/ingest of store documents):
#   list/show/search/query/file/entity — pure metadata/chunk reads.
#   read     — dumps a tabular file from disk; only opens the store for its home.
#   catalog  — reads experiments; the CATALOG.md/json it writes are plain files,
#              not store documents.
#   check    — structural report; "reports only; never mutates".
#   audit    — provenance-staleness report + worklist; never mutates the store.
#   meta/fingerprint — read the experiment.yml sidecar; no store writes.
# Deliberately read-write (they write the store): init, index, reindex,
# index-claims, new, intake, review. (`pr` is pure git and never opens the store.)
_READ_ONLY_COMMANDS = frozenset({
    "list", "show", "search", "query", "file", "read", "entity",
    "catalog", "check", "audit", "meta", "fingerprint",
})


def register(sub: argparse._SubParsersAction) -> None:
    """Register the store subcommands on an existing ``sci`` subparser action.

    Each store subcommand carries a ``--home`` flag (managed data folder; default
    ``$SCIENTIST_HOME``, else cwd) and a ``--json`` flag.
    """
    def add(name: str, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--home",
                        help="managed data folder (default: $SCIENTIST_HOME or cwd)")
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        return sp

    add("init", "create the libkit store and .gitignore under the data folder")
    sp = add("index", "index one experiment folder (by exp_id or path)")
    sp.add_argument("experiment")
    add("reindex", "index every experiment folder under the data folder")
    sp = add("index-claims", "index grounded claims from an experiment's grounding_report.json")
    sp.add_argument("experiment")
    sp.add_argument("--report", help="grounding_report.json to index "
                    "(default <exp>/analysis/grounding_report.json then <exp>/grounding_report.json)")
    sp = add("list", "list experiments (default), files, entities, claims, or reports")
    sp.add_argument("--kind",
                    choices=["experiment", "file", "entity", "claim", "report"],
                    default="experiment")
    sp.add_argument("--experiment", help="when --kind file/claim/report: limit to this exp_id")
    sp = add("show", "show one experiment and its files")
    sp.add_argument("experiment")
    sp = add("search", "metadata search across experiments and files")
    sp.add_argument("text")
    sp = add("query", "semantic + full-text search inside indexed content")
    sp.add_argument("text")
    sp.add_argument("--limit", type=int, default=8)
    sp.add_argument("--kind",
                    choices=["experiment", "file", "entity", "claim", "report"],
                    default=None)
    sp = add("file", "show one file record (by relative path)")
    sp.add_argument("path")
    sp = add("read", "dump a tabular file (csv/tsv/xlsx) to stdout")
    sp.add_argument("path")
    sp = add("entity", "list derived entities or show one entity's experiments")
    sp.add_argument("entity_action", choices=["list", "show"])
    sp.add_argument("name", nargs="?", help="entity name (for show)")
    sp = add("new", "scaffold a new experiment folder (subdirs + README template)")
    sp.add_argument("exp_id", help="internal id, e.g. K1-000003")
    sp.add_argument("name", help="short name, e.g. 'Rat IT Chronic Tox'")
    sp.add_argument("--cro", help="contract research org")
    sp.add_argument("--study-id", help="external/CRO study id")
    sp.add_argument("--model", help="species/model")
    sp = add("intake", "file a delivery (folder/files) into an experiment per LAYOUT.md")
    sp.add_argument("experiment", help="target experiment (exp_id or folder)")
    sp.add_argument("source", help="a delivery folder or a single file (copied, not moved)")
    sp.add_argument("--route", action="append", metavar="NAME=SUBDIR",
                    help="place file NAME in SUBDIR (protocol/reports/data/raw/analysis); "
                         "repeatable — the agent's per-document role call")
    sp.add_argument("--commit", action="store_true", help="actually copy + index (default: dry-run)")
    add("catalog", "export the experiment catalog (CATALOG.md + catalog.json)")
    sp = add("check", "structural integrity report (missing/unindexed files, layout, redundant "
             "archives) + cross-module literature-quote divergence lint")
    sp.add_argument("experiment", nargs="?", help="limit to one experiment (default: all)")
    sp = add("audit", "provenance staleness of the experiment.yml ledger + a worklist for the "
             "semantic pass (which includes the prose↔claims check)")
    sp.add_argument("experiment", nargs="?", help="limit to one experiment (default: all)")
    sp = add("meta", "show an experiment's structured metadata (experiment.yml)")
    sp.add_argument("experiment")
    sp = add("fingerprint", "show the input files (+ sha256) review would record for an experiment")
    sp.add_argument("experiment")
    sp = add("review", "record provenance after verifying the README vs its data (explicit input list)")
    sp.add_argument("experiment")
    sp.add_argument("--input", action="append", metavar="REPO_REL_PATH",
                    help="declare an external dependency (repeatable; e.g. a Shared/ CRO file)")
    sp.add_argument("--date", help="review date YYYY-MM-DD (default: today)")
    sp = add("pr", "package working-tree changes into a branch + pull request")
    sp.add_argument("title")
    sp.add_argument("paths", nargs="*", help="paths to include (default: all changes)")
    sp.add_argument("--body", help="PR body")
    sp.add_argument("--dry-run", action="store_true", help="show the git/gh steps, do nothing")


async def _run(args: argparse.Namespace) -> None:
    home = _home(args)
    _load_dotenv(home)
    handler = COMMANDS[args.cmd]
    if args.cmd == "init":
        home.mkdir(parents=True, exist_ok=True)
    else:
        _require_initialized(home)
    if args.cmd == "pr":            # pure git; no libkit store needed
        await handler(_HomeOnly(home), args)  # type: ignore[arg-type]
        return
    read_only = args.cmd in _READ_ONLY_COMMANDS
    try:
        store = Store.open(home, read_only=read_only)
    except EmbedderConfigError as e:
        die(str(e))
    except FileNotFoundError as e:
        # A read-only open never creates the store; a first-run read lands here.
        # (`_require_initialized` already guards most cases, but keep the message clear.)
        die(str(e))
    try:
        await handler(store, args)
    finally:
        await store.close()


def store_exists(args: argparse.Namespace) -> bool:
    """Whether a libkit store is initialized under the resolved data folder."""
    home = _home(args)
    return (home / STORE_DIRNAME / "catalog.duckdb").exists()


def dispatch_audit_storeless(args: argparse.Namespace) -> int:
    """Run `sci audit` WITHOUT opening the libkit store: pure provenance staleness over
    on-disk experiment folders. Used when no store is initialized so a single on-disk
    experiment can be audited without a store (and without a "no scientist store" error).
    """
    home = _home(args)
    _load_dotenv(home)
    report = audit_report(home, getattr(args, "experiment", None))
    print_audit_report(report, args.json)
    return 0


async def _run_index_report(args: argparse.Namespace, card: dict[str, Any]) -> None:
    home = _home(args)
    _load_dotenv(home)
    _require_initialized(home)
    try:
        store = Store.open(home)
    except EmbedderConfigError as e:
        die(str(e))
    try:
        rec = await store.upsert_report(card)
        if args.json:
            emit_json({"report_id": rec.get("report_id"), "scope": rec.get("scope"),
                       "exp_id": rec.get("exp_id"), "document_id": rec.get("document_id")})
        else:
            print(f"indexed report {rec.get('report_id')} "
                  f"({rec.get('scope')}{', ' + rec['exp_id'] if rec.get('exp_id') else ''}) "
                  f"into the store")
    finally:
        await store.close()


def index_report(args: argparse.Namespace, card: dict[str, Any]) -> int:
    """Open the libkit store and upsert a ``kind=report`` document from a prepared ``card``
    dict (built store-free by ``provenance.report`` + ``sci report``). Sync wrapper."""
    try:
        asyncio.run(_run_index_report(args, card))
    except KeyboardInterrupt:
        die("interrupted", code=130)
    return 0


def dispatch(args: argparse.Namespace) -> int:
    """Run the selected store subcommand (sync wrapper over the async handler)."""
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        die("interrupted", code=130)
    return 0
