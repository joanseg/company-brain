"""The retrieval graph and personalised PageRank over it.

Node ids are namespaced strings: `c:<chunk>`, `d:<document>`, `t:<topic>`,
`e:<entity>`. Edges are rebuilt from the relational tables after every index,
then flattened into CSR arrays so propagation is a handful of numpy ops.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

ALPHA = 0.82
TURNS = 7
KNN = 4
KNN_FLOOR = 0.50

_cache: tuple[str, dict, np.ndarray, np.ndarray, np.ndarray] | None = None


def invalidate() -> None:
    global _cache
    _cache = None


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def rebuild(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM edges")
    add = []

    rows = conn.execute(
        "SELECT c.id, c.doc_id, c.ordinal, d.rel_path, s.name AS source "
        "FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "JOIN sources s ON s.id = d.source_id ORDER BY c.doc_id, c.ordinal").fetchall()

    previous_doc, previous_chunk = None, None
    for row in rows:
        chunk, doc = "c:%d" % row["id"], "d:%d" % row["doc_id"]
        add.append((chunk, doc, "in_doc", 1.0))
        if row["doc_id"] == previous_doc and previous_chunk:
            add.append((previous_chunk, chunk, "next", 0.6))
        previous_doc, previous_chunk = row["doc_id"], chunk

        parts = Path(row["rel_path"]).parts[:-1]
        for depth in range(min(len(parts), 3)):
            topic = "t:%s:%s" % (row["source"], "/".join(parts[:depth + 1]))
            add.append((doc, topic, "topic", 0.5))

    for row in conn.execute("SELECT chunk_id, entity_id, weight FROM mentions"):
        add.append(("c:%d" % row["chunk_id"], "e:%d" % row["entity_id"], "mentions", row["weight"]))

    for row in conn.execute("SELECT src, dst, weight FROM relations"):
        add.append(("e:%d" % row["src"], "e:%d" % row["dst"], "relates", row["weight"]))

    add.extend(_semantic_edges(conn))

    conn.executemany(
        "INSERT OR REPLACE INTO edges(src, dst, kind, weight) VALUES(?,?,?,?)", add)
    conn.commit()
    invalidate()
    # `add` carries duplicates that the primary key collapses; report what landed.
    return conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]


def _semantic_edges(conn: sqlite3.Connection) -> list[tuple]:
    """Sparse k-NN layer. It is what lets the graph see that two passages are
    about the same thing when they share no vocabulary."""
    from . import dense

    store = dense.matrix(conn)
    if store is None:
        return []
    shas, array = store
    if len(shas) < 2:
        return []
    chunk_of: dict[str, int] = {}
    for row in conn.execute("SELECT id, sha FROM chunks"):
        chunk_of.setdefault(row["sha"], row["id"])

    out = []
    for start in range(0, len(shas), 512):
        block = array[start:start + 512]
        similarity = block @ array.T
        for offset in range(block.shape[0]):
            row_index = start + offset
            similarity[offset, row_index] = -np.inf
            top = np.argpartition(similarity[offset], -KNN)[-KNN:]
            for other in top:
                score = float(similarity[offset, other])
                if score < KNN_FLOOR:
                    continue
                left, right = chunk_of.get(shas[row_index]), chunk_of.get(shas[int(other)])
                if left and right and left != right:
                    out.append(("c:%d" % left, "c:%d" % right, "similar", score))
    return out


# ---------------------------------------------------------------------------
# propagation
# ---------------------------------------------------------------------------

def load(conn: sqlite3.Connection) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray] | None:
    """CSR view of the undirected edge set, cached until the index changes."""
    global _cache
    from . import db

    stamp = str(db.get_meta(conn, "indexed_at")) + ":" + str(
        conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
    if _cache and _cache[0] == stamp:
        return _cache[1], _cache[2], _cache[3], _cache[4]

    rows = conn.execute("SELECT src, dst, weight FROM edges").fetchall()
    if not rows:
        return None

    index: dict[str, int] = {}
    def node(name: str) -> int:
        return index.setdefault(name, len(index))

    pairs = []
    for row in rows:
        left, right, weight = node(row["src"]), node(row["dst"]), row["weight"]
        pairs.append((left, right, weight))
        pairs.append((right, left, weight))

    size = len(index)
    sources = np.fromiter((p[0] for p in pairs), dtype=np.int64, count=len(pairs))
    targets = np.fromiter((p[1] for p in pairs), dtype=np.int64, count=len(pairs))
    weights = np.fromiter((p[2] for p in pairs), dtype=np.float32, count=len(pairs))

    order = np.argsort(sources, kind="stable")
    targets, weights = targets[order], weights[order]
    counts = np.bincount(sources, minlength=size)
    indptr = np.zeros(size + 1, dtype=np.int64)
    np.cumsum(counts, out=indptr[1:])

    totals = np.add.reduceat(weights, indptr[:-1]) if len(weights) else np.zeros(size, dtype=np.float32)
    totals[counts == 0] = 1.0
    weights = weights / np.repeat(np.where(totals == 0, 1.0, totals), counts)

    _cache = (stamp, index, indptr, targets, weights)
    return index, indptr, targets, weights


def personalised(conn: sqlite3.Connection, seeds: dict[str, float]) -> dict[str, float]:
    """Personalised PageRank restarted on the seed distribution."""
    graph = load(conn)
    if graph is None or not seeds:
        return {}
    index, indptr, targets, weights = graph

    restart = np.zeros(len(index), dtype=np.float32)
    for name, value in seeds.items():
        position = index.get(name)
        if position is not None:
            restart[position] += value
    total = restart.sum()
    if total <= 0:
        return {}
    restart /= total

    score = restart.copy()
    for _ in range(TURNS):
        contribution = np.repeat(score, np.diff(indptr)) * weights
        spread = np.zeros_like(score)
        np.add.at(spread, targets, contribution)
        score = (1 - ALPHA) * restart + ALPHA * spread

    peak = float(score.max()) or 1.0
    return {name: float(score[position]) / peak
            for name, position in index.items() if score[position] > 0}


def neighbours(conn: sqlite3.Connection, node: str, limit: int = 40) -> list[tuple[str, str, float]]:
    rows = conn.execute(
        "SELECT dst AS other, kind, weight FROM edges WHERE src = ? "
        "UNION SELECT src AS other, kind, weight FROM edges WHERE dst = ? "
        "ORDER BY weight DESC LIMIT ?", (node, node, limit)).fetchall()
    return [(r["other"], r["kind"], r["weight"]) for r in rows]
