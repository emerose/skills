"""Compact the library's libkit ``catalog.duckdb`` to reclaim disk bloat.

Why this exists
---------------
libkit stores everything — documents, chunks, the DuckDB VSS **HNSW** index
(``chunks_vector_hnsw``) and the FTS snapshot — in one DuckDB file. Two DuckDB
facts make that file grow far past its logical size under normal use:

* **DuckDB never shrinks a database file in place.** Deletes/updates free blocks
  *inside* the file (reused for later writes) but are never returned to the OS,
  so the file sits at its high-water mark. ``VACUUM``/``CHECKPOINT`` do not
  change this.
* The experimental **persistent HNSW index** rewrites/appends on every
  ``CHECKPOINT``; under heavy add/delete/update churn its on-disk footprint
  balloons, and because of the point above that growth is sticky.

In production this was observed to reach **225 GB for ~1,700 papers** (logical
data ~1 GB). We measured the candidate fixes on a churned throwaway library:

==========================  ===================  ==========================
method                       reclaims file space  note
==========================  ===================  ==========================
``hnsw_compact_index``       no                   runs, but file unchanged
``CHECKPOINT`` + ``VACUUM``  no                   in-place, never shrinks
``DROP INDEX`` + recreate    no                   freed blocks stay in file
``COPY FROM DATABASE`` →      **yes** (≈3×+)       rewrites into a fresh file
fresh file
==========================  ===================  ==========================

Only writing a **fresh** database reclaims space, because only a fresh file
omits the freed blocks. ``COPY FROM DATABASE`` rebuilds a compact HNSW index and
the FTS snapshot in the process. That is the mechanism here.

Mechanism (validated: 225 GB → 609 MB in ~10 s)
-----------------------------------------------
Use an *in-memory* orchestrator connection with VSS loaded (without
``LOAD vss`` the COPY fails with ``unknown index type 'HNSW'``), attach the old
file read-only and a fresh destination, then ``COPY FROM DATABASE``::

    con = duckdb.connect()                  # in-memory
    con.execute("INSTALL vss; LOAD vss;")
    con.execute("SET hnsw_enable_experimental_persistence=true;")
    con.execute("ATTACH '<old>' AS src (READ_ONLY)")
    con.execute("ATTACH '<new>' AS dst")
    con.execute("COPY FROM DATABASE src TO dst")
    con.execute("DETACH src"); con.execute("DETACH dst")

We use libkit's pinned ``duckdb`` (1.5.4) for storage compatibility — it is the
version libkit links, so the bytes it writes are exactly what libkit will read.

Safety
------
``compact`` is destructive (it replaces the catalog), so it is defensive:

* It refuses to run if a writer is active — detected via libkit's own
  ``<db>.writelock`` (filelock). It *acquires* that lock for the whole operation
  so no writer can start mid-compaction.
* It rebuilds into a temp file in the same directory, then **verifies** the new
  file (document/chunk counts match the source, the HNSW + FTS indexes exist,
  and a sample vector query returns rows) *before* swapping anything.
* The swap renames the old file aside to ``catalog.duckdb.bloated-bak`` (kept
  until success) and moves any stale ``catalog.duckdb.wal*`` out of the way —
  a leftover WAL would otherwise be mis-replayed onto the new file and corrupt
  it. The backup is removed only after the swap succeeds (``--keep-backup``
  keeps it).

This module talks to DuckDB directly rather than through ``libkit.Library``: the
operation runs with the library *closed* and rewrites the file out from under
it, which is exactly what an open ``Library`` handle must not see.
"""

from __future__ import annotations

import contextlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# libkit's HNSW / FTS object names (see libkit/store/schema.py). Kept in sync
# with the store layer; the verify step asserts these exist in the rebuilt file.
HNSW_INDEX = "chunks_vector_hnsw"
FTS_SCHEMA = "fts_main_chunks"

BACKUP_SUFFIX = ".bloated-bak"


class CompactError(RuntimeError):
    """Compaction could not be performed (writer active, verify failed, …)."""


def _duckdb():
    import duckdb

    return duckdb


def file_size(path: Path) -> int:
    """On-disk size of the catalog plus any WAL sidecar (the real footprint)."""
    total = 0
    if path.exists():
        total += path.stat().st_size
    wal = path.with_name(path.name + ".wal")
    if wal.exists():
        total += wal.stat().st_size
    return total


def human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} TB"


def block_stats(db_path: Path) -> dict[str, Any]:
    """``PRAGMA database_size`` used/free/total blocks for a bloat estimate.

    Read-only, via a throwaway connection (with VSS loaded so attaching a
    library that carries an HNSW index doesn't error). ``free_blocks`` near zero
    with ``used_blocks ≈ total_blocks`` is the signature of a file that
    ``VACUUM`` cannot reclaim — the bloat is *live* blocks (a fat HNSW index),
    so only a rewrite shrinks it.
    """
    duckdb = _duckdb()
    con = duckdb.connect()
    try:
        con.execute("INSTALL vss; LOAD vss;")
        con.execute("SET hnsw_enable_experimental_persistence=true;")
        con.execute(f"ATTACH '{db_path}' AS s (READ_ONLY)")
        con.execute("USE s")
        row = con.execute("PRAGMA database_size").fetchone()
        cols = [d[0] for d in con.description]
        con.execute("USE memory")
        con.execute("DETACH s")
    finally:
        con.close()
    stats = dict(zip(cols, row))
    used = stats.get("used_blocks")
    free = stats.get("free_blocks")
    block = stats.get("block_size")
    if isinstance(used, int) and isinstance(free, int) and isinstance(block, int):
        stats["used_bytes"] = used * block
        stats["free_bytes"] = free * block
        total = used + free
        stats["free_fraction"] = (free / total) if total else 0.0
    return stats


def _table_counts(db_path: Path) -> tuple[int, int]:
    """(documents, chunks) row counts in a catalog, via a read-only connection."""
    duckdb = _duckdb()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        docs = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        con.close()
    return int(docs), int(chunks)


def _copy_rewrite(src: Path, dst: Path) -> None:
    """Rebuild ``src`` into a fresh ``dst`` via ``COPY FROM DATABASE``.

    ``LOAD vss`` is mandatory: without it the COPY of the HNSW index fails with
    ``unknown index type 'HNSW'``. The fresh destination gets a compact HNSW
    index and FTS snapshot rebuilt from the copied rows.
    """
    duckdb = _duckdb()
    con = duckdb.connect()
    try:
        con.execute("INSTALL vss; LOAD vss;")
        con.execute("INSTALL fts; LOAD fts;")
        con.execute("SET hnsw_enable_experimental_persistence=true;")
        con.execute(f"ATTACH '{src}' AS src (READ_ONLY)")
        con.execute(f"ATTACH '{dst}' AS dst")
        con.execute("COPY FROM DATABASE src TO dst")
        con.execute("DETACH src")
        con.execute("DETACH dst")
    finally:
        con.close()


def _verify(new_path: Path, *, expect_docs: int, expect_chunks: int) -> None:
    """Assert the rebuilt file is sound BEFORE it replaces the original.

    Checks: it opens; document/chunk counts match the source; the HNSW index and
    the FTS schema exist; and a sample vector query (over the *actual* stored
    dimension) returns rows when there are chunks. Raises :class:`CompactError`
    on any failure so the caller keeps the backup and aborts the swap.
    """
    duckdb = _duckdb()
    try:
        con = duckdb.connect(str(new_path), read_only=True)
    except Exception as e:  # noqa: BLE001
        raise CompactError(f"rebuilt catalog will not open: {e}") from e
    try:
        con.execute("INSTALL vss; LOAD vss;")
        con.execute("SET hnsw_enable_experimental_persistence=true;")
        docs, chunks = (
            con.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        )
        if docs != expect_docs or chunks != expect_chunks:
            raise CompactError(
                "rebuilt catalog row counts differ from source "
                f"(documents {docs} vs {expect_docs}, chunks {chunks} vs {expect_chunks})"
            )
        idx = {
            r[0]
            for r in con.execute(
                "SELECT index_name FROM duckdb_indexes() WHERE table_name = 'chunks'"
            ).fetchall()
        }
        if HNSW_INDEX not in idx:
            raise CompactError(f"rebuilt catalog is missing the HNSW index {HNSW_INDEX!r}")
        schemas = {
            r[0] for r in con.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
        }
        if FTS_SCHEMA not in schemas:
            raise CompactError(f"rebuilt catalog is missing the FTS index ({FTS_SCHEMA})")
        if chunks > 0:
            dim = con.execute("SELECT len(vector) FROM chunks LIMIT 1").fetchone()[0]
            zero = "[" + ",".join("0" for _ in range(int(dim))) + "]"
            rows = con.execute(
                f"SELECT chunk_id FROM chunks "
                f"ORDER BY array_distance(vector, {zero}::FLOAT[{int(dim)}]) LIMIT 1"
            ).fetchall()
            if not rows:
                raise CompactError("sample vector query returned no rows on the rebuilt catalog")
    finally:
        con.close()


def _stale_wal_paths(db_path: Path) -> list[Path]:
    """Any ``catalog.duckdb.wal*`` sidecars next to the catalog."""
    return sorted(db_path.parent.glob(db_path.name + ".wal*"))


def writer_active(db_path: Path) -> bool:
    """True if another process currently holds libkit's write lock.

    Probes the same ``<db>.writelock`` filelock libkit's writer takes for its
    whole life: a *non-blocking* acquire that succeeds proves no writer is
    connected (we release immediately); a timeout means one is.
    """
    import filelock

    lock_file = str(db_path.with_name(db_path.name + ".writelock"))
    probe = filelock.FileLock(lock_file, timeout=0)
    try:
        probe.acquire()
    except filelock.Timeout:
        return True
    else:
        with contextlib.suppress(Exception):
            probe.release()
        return False


def _acquire_writelock(db_path: Path, timeout: float):
    """Take libkit's ``<db>.writelock`` so no writer can start mid-compaction."""
    import filelock

    lock_file = str(db_path.with_name(db_path.name + ".writelock"))
    lock = filelock.FileLock(lock_file, timeout=timeout)
    try:
        lock.acquire()
    except filelock.Timeout as e:
        raise CompactError(
            f"a bibliographer writer holds {lock_file} — close it and retry "
            "(compact needs exclusive access)"
        ) from e
    return lock


def compact(
    home: Path,
    *,
    dry_run: bool = False,
    keep_backup: bool = False,
    lock_timeout: float = 30.0,
) -> dict[str, Any]:
    """Compact ``<home>/catalog.duckdb`` in place (via a verified rewrite + swap).

    Returns a result dict (the same payload the CLI prints under ``--json``):
    sizes before/after, bytes reclaimed, the backup path (unless removed), and —
    for ``dry_run`` — the bloat estimate from :func:`block_stats`. Idempotent:
    re-running an already-compact library simply reclaims little and still
    verifies + swaps cleanly.
    """
    db = home / "catalog.duckdb"
    if not db.exists():
        raise CompactError(f"no catalog at {db} — nothing to compact")

    size_before = file_size(db)
    result: dict[str, Any] = {
        "home": str(home),
        "catalog": str(db),
        "size_before": size_before,
        "size_before_h": human_size(size_before),
        "dry_run": dry_run,
    }

    if dry_run:
        result["writer_active"] = writer_active(db)
        try:
            stats = block_stats(db)
        except Exception as e:  # noqa: BLE001 — diagnostics must not crash a dry run
            stats = {"error": str(e)}
        result["block_stats"] = stats
        free_frac = stats.get("free_fraction")
        # The bloat signature: a big file with almost no free blocks (the bytes
        # are live HNSW index pages a VACUUM can't touch) → a rewrite reclaims.
        result["reclaimable_hint"] = (
            "low free-block fraction — bloat is live index pages; a rewrite "
            "(this command) is the only way to reclaim it"
            if isinstance(free_frac, float) and free_frac < 0.1
            else "free blocks present; some space may also be reclaimable, rewrite still compacts"
        )
        result["would_do"] = (
            "rebuild catalog.duckdb via COPY FROM DATABASE into a temp file, verify "
            "(counts + HNSW + FTS + sample query), back up the old file to "
            f"catalog.duckdb{BACKUP_SUFFIX}, move aside any stale WAL, swap, then "
            f"{'keep' if keep_backup else 'remove'} the backup"
        )
        return result

    if writer_active(db):
        raise CompactError(
            "a bibliographer writer is active (holds the write lock) — "
            "let it finish and retry; compact needs exclusive access"
        )

    lock = _acquire_writelock(db, lock_timeout)
    t0 = time.monotonic()
    tmp = db.with_name(f"catalog.compact-{datetime.now():%Y%m%d-%H%M%S}-{id(db) & 0xffff:x}.duckdb")
    backup = db.with_name(db.name + BACKUP_SUFFIX)
    try:
        # Re-acquiring the write lock above also waited out a transient writer;
        # counts come from the now-quiescent source.
        expect_docs, expect_chunks = _table_counts(db)
        result["documents"] = expect_docs
        result["chunks"] = expect_chunks

        # Build + verify the new file BEFORE touching the original.
        tmp.unlink(missing_ok=True)
        for w in _stale_wal_paths(tmp):
            w.unlink(missing_ok=True)
        _copy_rewrite(db, tmp)
        _verify(tmp, expect_docs=expect_docs, expect_chunks=expect_chunks)

        # Swap: park the old file (kept until success), clear any stale WAL that
        # would be mis-replayed onto the new file, then move the new file in.
        if backup.exists():
            backup.unlink()
        db.rename(backup)
        moved_wals: list[str] = []
        for w in _stale_wal_paths(db):  # old WALs still named catalog.duckdb.wal*
            aside = w.with_name(w.name + BACKUP_SUFFIX)
            aside.unlink(missing_ok=True)
            w.rename(aside)
            moved_wals.append(str(aside))
        tmp.rename(db)
        result["moved_wals"] = moved_wals

        size_after = file_size(db)
        result["size_after"] = size_after
        result["size_after_h"] = human_size(size_after)
        result["reclaimed"] = max(0, size_before - size_after)
        result["reclaimed_h"] = human_size(max(0, size_before - size_after))
        result["elapsed_s"] = round(time.monotonic() - t0, 2)

        if keep_backup:
            result["backup"] = str(backup)
        else:
            backup.unlink(missing_ok=True)
            for w in _stale_wal_paths(home / (db.name + BACKUP_SUFFIX)):
                w.unlink(missing_ok=True)
            # also drop the moved-aside WAL backups
            for w in moved_wals:
                Path(w).unlink(missing_ok=True)
            result["backup"] = None
        return result
    except Exception:
        # Verification/build failed (or swap raced): leave the original in place.
        # If we already renamed the original to backup, restore it.
        with contextlib.suppress(Exception):
            if not db.exists() and backup.exists():
                backup.rename(db)
        with contextlib.suppress(Exception):
            tmp.unlink(missing_ok=True)
            for w in _stale_wal_paths(tmp):
                w.unlink(missing_ok=True)
        raise
    finally:
        with contextlib.suppress(Exception):
            lock.release()
