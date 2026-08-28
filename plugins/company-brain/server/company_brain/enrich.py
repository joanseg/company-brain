"""Entity and relationship extraction, driven by Claude Code subagents.

No API spend: `/brain-enrich` pulls a batch of un-extracted chunks, fans them
out to subagents in the current session, and pushes structured JSON back. The
queue is keyed by chunk sha, so the work is resumable and idempotent — a chunk
whose text has not changed is never extracted twice.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .ingest import slug

LEASE_MINUTES = 30
ENTITY_TYPES = {"person", "organisation", "product", "place", "concept", "event", "decision"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reset_leases(conn: sqlite3.Connection) -> int:
    """Return abandoned leases to the queue."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LEASE_MINUTES)).isoformat(timespec="seconds")
    cursor = conn.execute(
        "UPDATE enrich_queue SET status = 'pending' WHERE status = 'leased' AND updated_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount


def pull(conn: sqlite3.Connection, limit: int = 15) -> list[dict]:
    """Lease the next batch of chunks awaiting extraction.

    Held in an IMMEDIATE transaction so two callers pulling at once cannot
    lease the same chunks and duplicate each other's work.
    """
    reset_leases(conn)
    conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute(
        "SELECT q.sha, c.text, c.heading_path, d.title, d.rel_path, s.name AS source "
        "FROM enrich_queue q "
        "JOIN chunks c ON c.sha = q.sha "
        "JOIN documents d ON d.id = c.doc_id "
        "JOIN sources s ON s.id = d.source_id "
        "WHERE q.status = 'pending' GROUP BY q.sha LIMIT ?", (limit,)).fetchall()
    shas = [r["sha"] for r in rows]
    if shas:
        conn.executemany("UPDATE enrich_queue SET status = 'leased', updated_at = ? WHERE sha = ?",
                         [(_now(), s) for s in shas])
    conn.commit()
    return [{"sha": r["sha"], "source": r["source"], "path": r["rel_path"],
             "title": r["title"], "heading": r["heading_path"], "text": r["text"]} for r in rows]


def _entity_id(conn: sqlite3.Connection, name: str, kind: str, description: str) -> int | None:
    norm = slug(name)
    if len(norm) < 2:
        return None
    kind = kind if kind in ENTITY_TYPES else "concept"
    conn.execute(
        "INSERT INTO entities(norm, name, type, description) VALUES(?,?,?,?) "
        "ON CONFLICT(norm) DO UPDATE SET "
        "  description = CASE WHEN length(excluded.description) > length(entities.description) "
        "                     THEN excluded.description ELSE entities.description END",
        (norm, name.strip(), kind, (description or "").strip()))
    entity_id = conn.execute("SELECT id FROM entities WHERE norm = ?", (norm,)).fetchone()["id"]
    conn.execute("INSERT OR IGNORE INTO entity_aliases(entity_id, alias) VALUES(?,?)", (entity_id, norm))
    return entity_id


def push(conn: sqlite3.Connection, results: list[dict]) -> dict:
    """Write back one batch of extractions and close out those queue rows."""
    stats = {"chunks": 0, "entities": 0, "relations": 0, "skipped": []}
    for result in results:
        sha = result.get("sha")
        if not sha:
            continue
        chunk_ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE sha = ?", (sha,))]
        if not chunk_ids:
            stats["skipped"].append(sha)
            continue

        by_name: dict[str, int] = {}
        for entity in result.get("entities") or []:
            name = (entity.get("name") or "").strip()
            if not name:
                continue
            entity_id = _entity_id(conn, name, (entity.get("type") or "concept").lower(),
                                   entity.get("description", ""))
            if entity_id is None:
                continue
            by_name[slug(name)] = entity_id
            for alias in entity.get("aliases") or []:
                if len(slug(alias)) > 2:
                    conn.execute("INSERT OR IGNORE INTO entity_aliases(entity_id, alias) VALUES(?,?)",
                                 (entity_id, slug(alias)))
            conn.executemany("INSERT OR IGNORE INTO mentions(chunk_id, entity_id, weight) VALUES(?,?,1.0)",
                             [(cid, entity_id) for cid in chunk_ids])
            stats["entities"] += 1

        for relation in result.get("relations") or []:
            src = by_name.get(slug(relation.get("src", ""))) or _lookup(conn, relation.get("src", ""))
            dst = by_name.get(slug(relation.get("dst", ""))) or _lookup(conn, relation.get("dst", ""))
            if not src or not dst or src == dst:
                continue
            conn.execute(
                "INSERT INTO relations(src, dst, type, description, weight, evidence_chunk) "
                "VALUES(?,?,?,?,1.0,?) ON CONFLICT(src, dst, type) DO UPDATE SET "
                "  weight = relations.weight + 0.5, "
                "  description = CASE WHEN length(excluded.description) > length(relations.description) "
                "                     THEN excluded.description ELSE relations.description END",
                (src, dst, slug(relation.get("type") or "related") or "related",
                 (relation.get("description") or "").strip(), chunk_ids[0]))
            stats["relations"] += 1

        conn.execute("UPDATE enrich_queue SET status = 'done', updated_at = ? WHERE sha = ?", (_now(), sha))
        stats["chunks"] += 1

    conn.commit()
    stats["pending"] = conn.execute(
        "SELECT COUNT(*) FROM enrich_queue WHERE status = 'pending'").fetchone()[0]
    return stats


def _lookup(conn: sqlite3.Connection, name: str) -> int | None:
    norm = slug(name)
    if len(norm) < 2:
        return None
    row = conn.execute(
        "SELECT entity_id FROM entity_aliases WHERE alias = ? LIMIT 1", (norm,)).fetchone()
    return row["entity_id"] if row else None


def entity_report(conn: sqlite3.Connection, name: str) -> dict | None:
    """Everything the graph knows about one entity — v1's `who`, widened."""
    norm = slug(name)
    row = conn.execute(
        "SELECT e.* FROM entities e LEFT JOIN entity_aliases a ON a.entity_id = e.id "
        "WHERE e.norm = ? OR a.alias = ? LIMIT 1", (norm, norm)).fetchone()
    if not row:
        near = [r["norm"] for r in conn.execute(
            "SELECT norm FROM entities WHERE norm LIKE ? LIMIT 8", ("%" + norm + "%",))]
        return {"found": False, "query": name, "did_you_mean": near}

    entity_id = row["id"]
    related = [dict(r) for r in conn.execute(
        "SELECT e.name, e.type, r.type AS relation, r.description, r.weight FROM relations r "
        "JOIN entities e ON e.id = CASE WHEN r.src = ? THEN r.dst ELSE r.src END "
        "WHERE r.src = ? OR r.dst = ? ORDER BY r.weight DESC LIMIT 30",
        (entity_id, entity_id, entity_id))]
    documents = [dict(r) for r in conn.execute(
        "SELECT s.name AS source, d.rel_path, d.title, d.doc_date, COUNT(*) AS mentions "
        "FROM mentions m JOIN chunks c ON c.id = m.chunk_id "
        "JOIN documents d ON d.id = c.doc_id JOIN sources s ON s.id = d.source_id "
        "WHERE m.entity_id = ? GROUP BY d.id ORDER BY mentions DESC LIMIT 20", (entity_id,))]
    assertions = [dict(r) for r in conn.execute(
        "SELECT a.predicate, a.value, a.since, a.until, d.rel_path, a.line "
        "FROM assertions a JOIN documents d ON d.id = a.doc_id WHERE a.subject = ?", (row["norm"],))]
    return {
        "found": True, "name": row["name"], "type": row["type"],
        "description": row["description"],
        "aliases": [r["alias"] for r in conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ?", (entity_id,))],
        "related": related, "documents": documents, "assertions": assertions,
    }


# ---------------------------------------------------------------------------
# entity resolution
# ---------------------------------------------------------------------------

def _mutual_pairs(conn: sqlite3.Connection) -> list[tuple]:
    """Entity pairs where each side was independently declared an alias of the
    other. One-directional claims are excluded on purpose: they are usually a
    part-of relationship (a product named after its company), not a synonym."""
    return [tuple(r) for r in conn.execute(
        "SELECT e1.id, e1.name, e2.id, e2.name, "
        "  (SELECT COUNT(*) FROM mentions m WHERE m.entity_id = e1.id) AS m1, "
        "  (SELECT COUNT(*) FROM mentions m WHERE m.entity_id = e2.id) AS m2 "
        "FROM entities e1 "
        "JOIN entity_aliases a1 ON a1.alias = e1.norm AND a1.entity_id != e1.id "
        "JOIN entities e2 ON e2.id = a1.entity_id "
        "JOIN entity_aliases a2 ON a2.alias = e2.norm AND a2.entity_id = e1.id "
        "WHERE e1.id < e2.id")]


def merge_duplicates(conn: sqlite3.Connection, apply: bool = False) -> dict:
    """Fold mutually-aliased entities together, keeping the better-attested name."""
    pairs = _mutual_pairs(conn)

    # Union-find so a chain (short name / full name / a misspelling of it)
    # collapses to one node.
    parent: dict[int, int] = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    weight, label = {}, {}
    for id1, name1, id2, name2, m1, m2 in pairs:
        weight[id1], weight[id2] = m1, m2
        label[id1], label[id2] = name1, name2
        union(id1, id2)

    groups: dict[int, list[int]] = {}
    for node in parent:
        groups.setdefault(find(node), []).append(node)

    plan = []
    for members in groups.values():
        # Keep the most-mentioned; on a tie prefer the longer, more explicit name.
        # Keep the row the corpus talks about most (it carries the graph mass),
        # but display the fullest spelling — every variant stays an alias, so
        # retrieval is unaffected either way.
        keeper = max(members, key=lambda i: weight.get(i, 0))
        display = max((label[i] for i in members), key=len)
        losers = [i for i in members if i != keeper]
        if losers:
            plan.append({"keep": display, "keep_id": keeper, "was": label[keeper],
                         "merge": [label[i] for i in losers], "ids": losers})

    if apply:
        for item in plan:
            keeper = item["keep_id"]
            conn.execute("UPDATE entities SET name = ? WHERE id = ?", (item["keep"], keeper))
            for loser in item["ids"]:
                conn.execute("UPDATE OR IGNORE mentions SET entity_id = ? WHERE entity_id = ?", (keeper, loser))
                conn.execute("UPDATE OR IGNORE entity_aliases SET entity_id = ? WHERE entity_id = ?", (keeper, loser))
                conn.execute("UPDATE OR IGNORE relations SET src = ? WHERE src = ?", (keeper, loser))
                conn.execute("UPDATE OR IGNORE relations SET dst = ? WHERE dst = ?", (keeper, loser))
                conn.execute("UPDATE OR IGNORE community_members SET entity_id = ? WHERE entity_id = ?", (keeper, loser))
                conn.execute("DELETE FROM entities WHERE id = ?", (loser,))
        conn.execute("DELETE FROM relations WHERE src = dst")
        conn.commit()

    return {"applied": apply, "groups": len(plan),
            "entities_removed": sum(len(p["ids"]) for p in plan), "plan": plan}
