"""Citation-graph analytics for bibliographer (pure, offline).

This module turns the per-paper *outgoing reference lists* stored on records
(``metadata['references']`` — OpenAlex work ids like ``"W123"``, populated by
``bib refs``) into the three citation features:

* :func:`gap_candidates` — external works your library cites a lot but doesn't
  contain (candidates to add).
* :func:`coupling_clusters` — papers grouped into topic areas by *bibliographic
  coupling* (citing shared references).
* :func:`isolation_report` — papers that share (almost) no references with the
  rest of the library and have no intra-library citation edges (likely off-topic
  / added by mistake).

Everything here is a **pure function of the records passed in** — no network, no
libkit — so it is unit-tested directly. The CLI (``bib gaps``/``cluster``/
``outliers``) supplies the I/O: loading records and, for ``gaps``, looking up
human labels for the top candidate works from OpenAlex.

Node identity is the **OpenAlex work id**. A library paper's own id is
``metadata['openalex_id']`` (or ``metadata['metrics']['openalex_id']``); that is
how a reference pointing *back into* the library is recognised. Papers without
an OpenAlex id can still couple via shared references, but can't be the *target*
of an intra-library edge — run ``bib refresh``/``bib refs`` to fill ids first.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def self_id(rec: dict[str, Any]) -> str | None:
    """A record's own OpenAlex work id (top-level, or from the metrics block)."""
    return rec.get("openalex_id") or (rec.get("metrics") or {}).get("openalex_id")


def references_of(rec: dict[str, Any]) -> set[str]:
    """The set of OpenAlex work ids a record cites (de-duped, empties dropped)."""
    return {str(r) for r in (rec.get("references") or []) if r}


def _with_citekeys(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r.get("citekey")]


def _node_maps(records: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """``(citekey -> own work id, own work id -> citekey)`` for in-library nodes."""
    node_of: dict[str, str] = {}
    citekey_of: dict[str, str] = {}
    for r in records:
        wid = self_id(r)
        if wid:
            node_of[r["citekey"]] = wid
            citekey_of.setdefault(wid, r["citekey"])
    return node_of, citekey_of


def has_reference_data(records: Iterable[dict[str, Any]]) -> bool:
    """True if any record carries a fetched reference list (``bib refs`` has run)."""
    return any("references" in r for r in records)


# --------------------------------------------------------------------------- #
# feature 1: gap candidates ("you cite this a lot but don't have it")
# --------------------------------------------------------------------------- #
def gap_candidates(
    records: Iterable[dict[str, Any]],
    *,
    min_citing: int = 2,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """External works cited by the most library papers but not *in* the library.

    Counts, over every paper's reference list, how many distinct library papers
    cite each external work; keeps those cited by at least ``min_citing`` papers
    and not already present (matched on OpenAlex id). Ranked by citing-count
    (desc), then work id for determinism. Pure — labelling the winners with
    titles is the caller's job.

    Each entry: ``{work_id, citing_count, citing_citekeys}``.
    """
    recs = _with_citekeys(records)
    _, citekey_of = _node_maps(recs)
    in_library = set(citekey_of)

    citers: dict[str, set[str]] = defaultdict(set)
    for r in recs:
        for wid in references_of(r):
            if wid not in in_library:
                citers[wid].add(r["citekey"])

    out = [
        {"work_id": wid, "citing_count": len(cks), "citing_citekeys": sorted(cks)}
        for wid, cks in citers.items()
        if len(cks) >= min_citing
    ]
    out.sort(key=lambda c: (-c["citing_count"], c["work_id"]))
    return out[:limit] if limit else out


# --------------------------------------------------------------------------- #
# bibliographic coupling (shared references) — the basis for clustering/outliers
# --------------------------------------------------------------------------- #
def _coupling_pairs(recs: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """``(ck_a, ck_b) -> shared-reference count`` for every coupled pair.

    Built via an inverted index (work id -> citing papers) rather than an
    O(papers²) scan: only works cited by >1 library paper contribute, and most
    external works are cited by just one. Keys are ordered ``a < b``.
    """
    work_to_papers: dict[str, list[str]] = defaultdict(list)
    for r in recs:
        for wid in references_of(r):
            work_to_papers[wid].append(r["citekey"])

    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for cks in work_to_papers.values():
        if len(cks) < 2:
            continue
        cks = sorted(set(cks))
        for i in range(len(cks)):
            for j in range(i + 1, len(cks)):
                pairs[(cks[i], cks[j])] += 1
    return pairs


# --------------------------------------------------------------------------- #
# feature 2: topic clustering by bibliographic coupling
# --------------------------------------------------------------------------- #
def coupling_clusters(
    records: Iterable[dict[str, Any]],
    *,
    min_shared: int = 2,
) -> dict[str, list]:
    """Group papers into topic areas via bibliographic coupling.

    Papers sharing at least ``min_shared`` references are linked; connected
    components of size ≥ 2 are clusters. Returns
    ``{"clusters": [[citekey, …], …], "unclustered": [citekey, …]}`` with
    clusters sorted by size (desc) then first citekey, members sorted, and every
    list deterministic — so ``cluster:N`` tag assignment is stable across runs.
    """
    recs = _with_citekeys(records)
    all_cks = {r["citekey"] for r in recs}
    pairs = _coupling_pairs(recs)

    # union-find over the citekeys joined by a strong-enough coupling edge
    parent: dict[str, str] = {ck: ck for ck in all_cks}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for (a, b), shared in pairs.items():
        if shared >= min_shared:
            union(a, b)

    groups: dict[str, list[str]] = defaultdict(list)
    for ck in all_cks:
        groups[find(ck)].append(ck)

    clusters = [sorted(g) for g in groups.values() if len(g) >= 2]
    clusters.sort(key=lambda g: (-len(g), g[0]))
    clustered = {ck for g in clusters for ck in g}
    unclustered = sorted(all_cks - clustered)
    return {"clusters": clusters, "unclustered": unclustered}


# --------------------------------------------------------------------------- #
# feature 3: off-topic / mistaken-inclusion detection
# --------------------------------------------------------------------------- #
def isolation_report(
    records: Iterable[dict[str, Any]],
    *,
    min_shared: int = 2,
) -> list[dict[str, Any]]:
    """Per-paper citation-isolation signals, most-isolated first.

    For each paper: ``max_coupling`` (most shared references with any other
    paper), ``out_edges`` (its references that point at another library paper),
    ``in_edges`` (other library papers that cite it), and ``intra_edges`` =
    out+in. ``isolated`` flags a paper that has reference data but couples below
    ``min_shared`` with everything *and* has no intra-library edge — the
    off-topic / added-by-mistake candidates. Papers with no fetched references
    are reported with ``has_references=False`` and never flagged (unknown, not
    isolated).

    Sorted: isolated first, then by ``max_coupling`` asc, then ``intra_edges``
    asc, then citekey.
    """
    recs = _with_citekeys(records)
    node_of, citekey_of = _node_maps(recs)
    in_library_ids = set(citekey_of)
    pairs = _coupling_pairs(recs)

    max_coupling: dict[str, int] = defaultdict(int)
    for (a, b), shared in pairs.items():
        if shared > max_coupling[a]:
            max_coupling[a] = shared
        if shared > max_coupling[b]:
            max_coupling[b] = shared

    # incoming edges: how many OTHER papers cite each library paper's own id
    in_edges: dict[str, int] = defaultdict(int)
    for r in recs:
        ck = r["citekey"]
        for wid in references_of(r):
            target = citekey_of.get(wid)
            if target and target != ck:
                in_edges[target] += 1

    report: list[dict[str, Any]] = []
    for r in recs:
        ck = r["citekey"]
        refs = references_of(r)
        has_refs = "references" in r
        own = node_of.get(ck)
        out_edges = len({w for w in refs if w in in_library_ids and w != own})
        intra = out_edges + in_edges.get(ck, 0)
        mc = max_coupling.get(ck, 0)
        isolated = has_refs and mc < min_shared and intra == 0
        report.append({
            "citekey": ck,
            "title": r.get("title"),
            "has_references": has_refs,
            "reference_count": len(refs),
            "max_coupling": mc,
            "out_edges": out_edges,
            "in_edges": in_edges.get(ck, 0),
            "intra_edges": intra,
            "isolated": isolated,
        })

    report.sort(key=lambda e: (not e["isolated"], e["max_coupling"], e["intra_edges"], e["citekey"]))
    return report
