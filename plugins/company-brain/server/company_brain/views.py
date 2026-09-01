"""Payload builders for the local dashboard.

Kept separate from the HTTP layer so every shape is testable without
binding a socket. Every function is read-only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_empty(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def _empty() -> dict:
    return {"empty": True, "next": "/brain-index", "generated_at": _stamp()}


def _stale_files() -> int:
    """Count source .md files touched since the last index run.

    Mirrors `hooks/staleness.sh`: compares mtimes against the `.indexed`
    marker the indexer touches on every refresh, walking the same roots
    with the same excludes so this never counts a file the indexer itself
    would skip. Cheap — one rglob per source root, no DB or LLM work.
    """
    from . import db, ingest

    marker = db.state_dir() / ".indexed"
    if not marker.exists():
        return 0
    marker_mtime = marker.stat().st_mtime
    count = 0
    for source in db.load_sources():
        root = source["root"]
        if not root.is_dir():
            continue
        for path in ingest.walk(root, source["exclude"]):
            try:
                if path.stat().st_mtime > marker_mtime:
                    count += 1
            except OSError:
                continue
    return count


def overview(conn: sqlite3.Connection) -> dict:
    if _is_empty(conn):
        return _empty()

    def one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    counts = {
        "documents": one("SELECT COUNT(*) FROM documents"),
        "chunks": one("SELECT COUNT(*) FROM chunks"),
        "embedded": one("SELECT COUNT(*) FROM vectors"),
        "entities": one("SELECT COUNT(*) FROM entities"),
        "relations": one("SELECT COUNT(*) FROM relations"),
        "communities": one("SELECT COUNT(*) FROM communities"),
    }
    sources = [dict(r) for r in conn.execute(
        "SELECT s.name, s.weight, s.root, "
        "  COUNT(DISTINCT d.id) AS documents, COUNT(c.id) AS chunks "
        "FROM sources s LEFT JOIN documents d ON d.source_id = s.id "
        "LEFT JOIN chunks c ON c.doc_id = d.id GROUP BY s.id ORDER BY s.name")]
    health = {
        "enrich_pending": one("SELECT COUNT(*) FROM enrich_queue WHERE status = 'pending'"),
        "unembedded": one("SELECT COUNT(DISTINCT sha) FROM chunks "
                          "WHERE sha NOT IN (SELECT sha FROM vectors)"),
        # A document in a source with enrich off can never have mentions — that
        # is what the flag is for. Counting those would raise a red flag no
        # amount of /brain-enrich could ever clear.
        "documents_without_entities": one(
            "SELECT COUNT(*) FROM documents d JOIN sources s ON s.id = d.source_id "
            "WHERE s.enrich = 1 AND NOT EXISTS ("
            "  SELECT 1 FROM chunks c JOIN mentions m ON m.chunk_id = c.id WHERE c.doc_id = d.id)"),
        "summaries_missing": one(
            "SELECT COUNT(*) FROM communities c WHERE NOT EXISTS ("
            "  SELECT 1 FROM community_summaries s WHERE s.community_id = c.id)"),
        "stale_files": _stale_files(),
    }
    return {"empty": False, "generated_at": _stamp(),
            "counts": counts, "sources": sources, "health": health}


def communities(conn: sqlite3.Connection) -> dict:
    if _is_empty(conn):
        return _empty()
    from . import community as community_module

    rows = [dict(r) for r in conn.execute(
        "SELECT c.id, c.level, s.title, s.summary, s.rating "
        "FROM communities c JOIN community_summaries s ON s.community_id = c.id "
        "ORDER BY s.rating DESC, c.id")]
    for row in rows:
        row["members"] = [r["name"] for r in conn.execute(
            "SELECT e.name, COUNT(m.chunk_id) AS n FROM community_members cm "
            "JOIN entities e ON e.id = cm.entity_id "
            "LEFT JOIN mentions m ON m.entity_id = e.id "
            "WHERE cm.community_id = ? GROUP BY e.id ORDER BY n DESC, e.name LIMIT 20",
            (row["id"],))]
        row["member_count"] = conn.execute(
            "SELECT COUNT(*) FROM community_members WHERE community_id = ?",
            (row["id"],)).fetchone()[0]
        # Dominant source = where this community's members are most talked about.
        top = conn.execute(
            "SELECT src.name, COUNT(*) AS n FROM community_members cm "
            "JOIN mentions m ON m.entity_id = cm.entity_id "
            "JOIN chunks ch ON ch.id = m.chunk_id "
            "JOIN documents d ON d.id = ch.doc_id "
            "JOIN sources src ON src.id = d.source_id "
            "WHERE cm.community_id = ? GROUP BY src.id ORDER BY n DESC LIMIT 1",
            (row["id"],)).fetchone()
        row["dominant_source"] = top["name"] if top else None
        evidence = community_module.context(conn, row["id"])["evidence"][:8]
        row["citations"] = [
            {"source": e["source"], "path": e["rel_path"], "line": e["line_start"]}
            for e in evidence
        ]
    return {"empty": False, "generated_at": _stamp(), "communities": rows,
            "next": "/brain-enrich --communities" if not rows else None}


def corpus(conn: sqlite3.Connection) -> dict:
    if _is_empty(conn):
        return _empty()
    documents = []
    for row in conn.execute(
            "SELECT d.rel_path, d.title, d.doc_date, s.name AS source, "
            "  (SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.id) AS chunks, "
            "  EXISTS(SELECT 1 FROM chunks c JOIN mentions m ON m.chunk_id = c.id "
            "         WHERE c.doc_id = d.id) AS has_entities "
            "FROM documents d JOIN sources s ON s.id = d.source_id "
            "ORDER BY s.name, d.rel_path"):
        path = row["rel_path"]
        documents.append({
            "path": path, "title": row["title"], "date": row["doc_date"],
            "source": row["source"], "chunks": row["chunks"],
            "folder": path.split("/")[0] if "/" in path else "(root)",
            "has_entities": bool(row["has_entities"]),
        })
    return {"empty": False, "generated_at": _stamp(), "documents": documents}


def graph(conn: sqlite3.Connection, limit: int = 400) -> dict:
    if _is_empty(conn):
        return _empty()
    limit = max(1, min(int(limit), 2000))
    nodes = [dict(r) for r in conn.execute(
        "SELECT e.id, e.name, e.type, COUNT(m.chunk_id) AS mentions "
        "FROM entities e LEFT JOIN mentions m ON m.entity_id = e.id "
        "GROUP BY e.id ORDER BY mentions DESC, e.name LIMIT ?", (limit,))]
    total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    keep = {n["id"] for n in nodes}
    edges = []
    for row in conn.execute("SELECT src, dst, type FROM relations"):
        # Both endpoints must survive the cut; a dangling edge breaks the renderer.
        if row["src"] in keep and row["dst"] in keep and row["src"] != row["dst"]:
            edges.append({"src": row["src"], "dst": row["dst"], "type": row["type"]})

    return {"empty": False, "generated_at": _stamp(), "nodes": nodes, "edges": edges,
            "limit": limit, "total_entities": total, "truncated": total > len(nodes)}


COMMANDS = [
    {"command": "/brain-ask", "does": "Answer a question with citations"},
    {"command": "/brain-add", "does": "Write something down and index it"},
    {"command": "/brain-index", "does": "Refresh from disk, incrementally"},
    {"command": "/brain-enrich", "does": "Build the entity and community layers"},
    {"command": "/brain-source", "does": "List or add repositories"},
    {"command": "/brain-dream", "does": "Contradictions, stale facts, orphans"},
    {"command": "/brain-view", "does": "Open this dashboard"},
]

HOW_TO = [
    {"tool": "entity", "when": "You have one named thing — a person, company, project"},
    {"tool": "search_evidence", "when": "You want cited passages about a specific question"},
    {"tool": "search_themes", "when": "You want a pattern no single passage contains"},
]


def suggestions(conn: sqlite3.Connection) -> dict:
    if _is_empty(conn):
        return _empty()
    questions = []
    for row in conn.execute(
            "SELECT e.name, COUNT(m.chunk_id) AS n FROM entities e "
            "JOIN mentions m ON m.entity_id = e.id "
            "GROUP BY e.id ORDER BY n DESC LIMIT 6"):
        questions.append({"text": "What do we know about %s?" % row["name"], "kind": "entity"})
    for row in conn.execute(
            "SELECT title FROM community_summaries ORDER BY rating DESC LIMIT 4"):
        questions.append({"text": "Summarise what we know about %s." % row["title"],
                          "kind": "themes"})
    for row in conn.execute(
            "SELECT s.name FROM sources s ORDER BY s.weight DESC LIMIT 2"):
        questions.append({"text": "What are the open questions in %s?" % row["name"],
                          "kind": "evidence"})
    return {"empty": False, "generated_at": _stamp(), "questions": questions,
            "commands": COMMANDS, "how_to": HOW_TO}
