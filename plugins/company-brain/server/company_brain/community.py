"""Community detection and community-level (global) retrieval.

Local search answers "what do we know about X". Global search answers "what
are the recurring themes" — questions whose answer lives in no single chunk.
Leiden partitions the entity graph, a subagent summarises each partition, and
those summaries become first-class retrievable objects.
"""

from __future__ import annotations

import hashlib
import sqlite3

import numpy as np

RESOLUTIONS = {0: 0.6, 1: 1.2}
MIN_MEMBERS = 3


def detect(conn: sqlite3.Connection) -> dict:
    """Partition the entity graph with Leiden at two resolutions."""
    import igraph as ig
    import leidenalg

    rows = conn.execute("SELECT src, dst, weight FROM relations").fetchall()
    if len(rows) < MIN_MEMBERS:
        return {"communities": 0, "note": "not enough relations yet — run /brain-enrich first"}

    nodes: dict[int, int] = {}
    def position(entity_id: int) -> int:
        return nodes.setdefault(entity_id, len(nodes))

    pairs = [(position(r["src"]), position(r["dst"])) for r in rows]
    weights = [r["weight"] for r in rows]
    back = {v: k for k, v in nodes.items()}

    graph = ig.Graph(n=len(nodes), edges=pairs, directed=False)
    graph.es["weight"] = weights

    conn.execute("DELETE FROM communities")
    made = 0
    for level, resolution in sorted(RESOLUTIONS.items()):
        partition = leidenalg.find_partition(
            graph, leidenalg.RBConfigurationVertexPartition,
            weights="weight", resolution_parameter=resolution, seed=7)
        for members in partition:
            if len(members) < MIN_MEMBERS:
                continue
            conn.execute("INSERT INTO communities(level, parent_id) VALUES(?, NULL)", (level,))
            community_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            conn.executemany(
                "INSERT OR IGNORE INTO community_members(community_id, entity_id) VALUES(?,?)",
                [(community_id, back[m]) for m in members])
            made += 1

    conn.execute("DELETE FROM community_summaries WHERE community_id NOT IN (SELECT id FROM communities)")
    conn.commit()
    return {"communities": made, "levels": sorted(RESOLUTIONS)}


def _member_sha(conn: sqlite3.Connection, community_id: int) -> str:
    members = [str(r["entity_id"]) for r in conn.execute(
        "SELECT entity_id FROM community_members WHERE community_id = ? ORDER BY entity_id",
        (community_id,))]
    return hashlib.sha256(",".join(members).encode()).hexdigest()


def pending(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Communities whose membership changed since their summary was written."""
    out = []
    for row in conn.execute("SELECT id, level FROM communities ORDER BY level, id"):
        current = _member_sha(conn, row["id"])
        existing = conn.execute(
            "SELECT sha FROM community_summaries WHERE community_id = ?", (row["id"],)).fetchone()
        if existing and existing["sha"] == current:
            continue
        out.append(context(conn, row["id"]) | {"sha": current})
        if len(out) >= limit:
            break
    return out


def context(conn: sqlite3.Connection, community_id: int) -> dict:
    """The material a summariser needs: who is in it, how they connect, evidence."""
    # Ordered by how much the corpus actually talks about them, not
    # alphabetically — the truncated head of this list is what callers see.
    entities = [dict(r) for r in conn.execute(
        "SELECT e.id, e.name, e.type, e.description, "
        "       (SELECT COUNT(*) FROM mentions m2 WHERE m2.entity_id = e.id) AS mentions "
        "FROM community_members m JOIN entities e ON e.id = m.entity_id "
        "WHERE m.community_id = ? ORDER BY mentions DESC, e.name LIMIT 60", (community_id,))]
    ids = [e["id"] for e in entities]
    if not ids:
        return {"community_id": community_id, "entities": [], "relations": [], "evidence": []}
    marks = ",".join("?" * len(ids))
    relations = [dict(r) for r in conn.execute(
        f"SELECT a.name AS src, b.name AS dst, r.type, r.description FROM relations r "
        f"JOIN entities a ON a.id = r.src JOIN entities b ON b.id = r.dst "
        f"WHERE r.src IN ({marks}) AND r.dst IN ({marks}) ORDER BY r.weight DESC LIMIT 80",
        tuple(ids) * 2)]
    evidence = [dict(r) for r in conn.execute(
        f"SELECT s.name AS source, d.rel_path, c.line_start, c.heading_path, "
        f"       substr(c.text, 1, 600) AS text, COUNT(*) AS hits "
        f"FROM mentions m JOIN chunks c ON c.id = m.chunk_id "
        f"JOIN documents d ON d.id = c.doc_id JOIN sources s ON s.id = d.source_id "
        f"WHERE m.entity_id IN ({marks}) GROUP BY c.id ORDER BY hits DESC LIMIT 18", tuple(ids))]
    level = conn.execute("SELECT level FROM communities WHERE id = ?", (community_id,)).fetchone()
    return {"community_id": community_id, "level": level["level"] if level else 0,
            "entities": entities, "relations": relations, "evidence": evidence}


def push_summaries(conn: sqlite3.Connection, summaries: list[dict]) -> dict:
    from . import dense

    written = 0
    texts, ids = [], []
    for item in summaries:
        community_id = item.get("community_id")
        if community_id is None:
            continue
        title = (item.get("title") or "Untitled community").strip()
        summary = (item.get("summary") or "").strip()
        if not summary:
            continue
        conn.execute(
            "INSERT INTO community_summaries(community_id, title, summary, rating, sha) "
            "VALUES(?,?,?,?,?) ON CONFLICT(community_id) DO UPDATE SET "
            "title = excluded.title, summary = excluded.summary, rating = excluded.rating, "
            "sha = excluded.sha, vec = NULL",
            (community_id, title, summary, float(item.get("rating", 5.0)),
             item.get("sha") or _member_sha(conn, community_id)))
        texts.append(title + "\n" + summary)
        ids.append(community_id)
        written += 1

    vectors = dense.embed(texts) if texts else []
    if vectors:
        conn.executemany("UPDATE community_summaries SET vec = ? WHERE community_id = ?",
                         [(np.asarray(v, dtype=np.float32).tobytes(), i) for v, i in zip(vectors, ids)])
    conn.commit()
    return {"written": written, "embedded": len(vectors or [])}


def search(conn: sqlite3.Connection, question: str, level: int | None = None,
           limit: int = 6) -> dict:
    """Rank community summaries for a thematic question."""
    from . import dense, lexical

    clause = "WHERE c.level = ?" if level is not None else ""
    args = (level,) if level is not None else ()
    rows = [dict(r) for r in conn.execute(
        f"SELECT s.community_id, s.title, s.summary, s.rating, s.vec, c.level "
        f"FROM community_summaries s JOIN communities c ON c.id = s.community_id {clause}", args)]
    if not rows:
        return {"question": question, "results": [],
                "note": "No community summaries yet. Run /brain-enrich --communities."}

    words = set(lexical.terms(question))
    query_vector = dense.embed([question])
    query = np.asarray(query_vector[0], dtype=np.float32) if query_vector else None

    scored = []
    for row in rows:
        blob = row.pop("vec")
        semantic = 0.0
        if query is not None and blob:
            stored = np.frombuffer(blob, dtype=np.float32)
            if stored.shape == query.shape:
                semantic = float(stored @ query)
        haystack = set(lexical.terms(row["title"] + " " + row["summary"]))
        overlap = len(words & haystack) / max(1, len(words))
        score = 0.6 * semantic + 0.25 * overlap + 0.15 * (row["rating"] / 10.0)
        scored.append((score, row, {"semantic": round(semantic, 4),
                                    "term_overlap": round(overlap, 4),
                                    "rating": row["rating"]}))

    scored.sort(key=lambda item: -item[0])
    results = []
    for score, row, signals in scored[:limit]:
        detail = context(conn, row["community_id"])
        results.append({
            "community_id": row["community_id"], "level": row["level"],
            "title": row["title"], "summary": row["summary"],
            "score": round(score, 4), "signals": signals,
            "entities": [e["name"] for e in detail["entities"][:15]],
            "citations": [{"source": e["source"], "path": e["rel_path"], "line": e["line_start"]}
                          for e in detail["evidence"][:8]],
        })
    return {"question": question, "level": level, "results": results}
