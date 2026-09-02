"""Build and refresh the index.

Incremental by document sha: an unchanged file is skipped entirely, and a
changed file only re-embeds the chunks whose own text actually moved, because
`vectors` and `enrich_queue` are keyed by chunk sha rather than chunk id.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import db, ingest


def _upsert_source(conn: sqlite3.Connection, source: dict) -> int:
    conn.execute(
        "INSERT INTO sources(name, root, weight, enrich, added_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET root = excluded.root, weight = excluded.weight, "
        "  enrich = excluded.enrich",
        (source["name"], str(source["root"]), source["weight"],
         int(source.get("enrich", True)),
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    return conn.execute("SELECT id FROM sources WHERE name = ?", (source["name"],)).fetchone()["id"]


def _drop_document(conn: sqlite3.Connection, doc_id: int) -> None:
    ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))]
    conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(i,) for i in ids])
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def _write_document(conn: sqlite3.Connection, source_id: int, doc: dict) -> int:
    conn.execute(
        "INSERT INTO documents(source_id, rel_path, title, sha, mtime, doc_date, meta_json, superseded_by) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (source_id, doc["rel_path"], doc["title"], doc["sha"], doc["mtime"], doc["doc_date"],
         _json(doc["meta"] | {"aliases": doc["aliases"], "links": doc["links"]}), doc["superseded_by"]),
    )
    doc_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    for ordinal, (heading, line_start, text) in enumerate(doc["chunks"]):
        chunk_sha = ingest.sha(f"{doc['rel_path']}\0{heading}\0{text}")
        conn.execute(
            "INSERT INTO chunks(doc_id, ordinal, heading_path, line_start, text, sha) VALUES(?,?,?,?,?,?)",
            (doc_id, ordinal, heading, line_start, text, chunk_sha),
        )
        chunk_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        # chunks_fts is a virtual table: no foreign key, so nothing cascades into
        # it and SQLite reuses chunk ids. Clear the slot before claiming it.
        conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
        conn.execute(
            "INSERT INTO chunks_fts(rowid, text, title, heading_path) VALUES(?,?,?,?)",
            (chunk_id, text, doc["title"], heading),
        )

    for fact in doc["assertions"]:
        conn.execute(
            "INSERT INTO assertions(doc_id, line, subject, predicate, value, since, until, functional) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (doc_id, fact["line"], fact["subject"] or ingest.slug(doc["title"]),
             fact["predicate"], fact["value"], fact["since"], fact["until"], int(fact["functional"])),
        )
    return doc_id


def _json(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def refresh(conn: sqlite3.Connection, only: Path | None = None) -> dict:
    """Sync the index with disk. Returns a summary of what changed."""
    stats = {"sources": [], "added": 0, "updated": 0, "removed": 0, "unchanged": 0}

    for source in db.load_sources():
        if not source["root"].is_dir():
            stats["sources"].append({"name": source["name"], "root": str(source["root"]),
                                     "error": "root not found", "documents": 0})
            continue
        source_id = _upsert_source(conn, source)
        known = {r["rel_path"]: r for r in
                 conn.execute("SELECT id, rel_path, sha FROM documents WHERE source_id = ?", (source_id,))}
        seen = set()

        for path in ingest.walk(source["root"], source["exclude"]):
            if only is not None and path != only:
                rel = str(path.relative_to(source["root"]))
                if rel in known:
                    seen.add(rel)
                continue
            doc = ingest.read_document(source["root"], path)
            seen.add(doc["rel_path"])
            previous = known.get(doc["rel_path"])
            if previous and previous["sha"] == doc["sha"]:
                stats["unchanged"] += 1
                continue
            if previous:
                _drop_document(conn, previous["id"])
                stats["updated"] += 1
            else:
                stats["added"] += 1
            _write_document(conn, source_id, doc)

        if only is None:
            for rel_path, row in known.items():
                if rel_path not in seen:
                    _drop_document(conn, row["id"])
                    stats["removed"] += 1

        stats["sources"].append({
            "name": source["name"],
            "root": str(source["root"]),
            "documents": conn.execute(
                "SELECT COUNT(*) AS n FROM documents WHERE source_id = ?", (source_id,)).fetchone()["n"],
        })

    _prune_and_enqueue(conn)
    now = datetime.now(timezone.utc)
    db.set_meta(conn, "indexed_at", now.isoformat(timespec="seconds"))
    # BSD find cannot parse timestamps that GNU find accepts, so the staleness
    # hook compares against this marker file with `-newer` instead.
    (db.state_dir() / ".indexed").touch()
    conn.commit()
    return stats | status(conn)


def _prune_and_enqueue(conn: sqlite3.Connection) -> None:
    """Drop vectors and search rows for text that no longer exists; queue new text."""
    conn.execute("DELETE FROM chunks_fts WHERE rowid NOT IN (SELECT id FROM chunks)")
    conn.execute("DELETE FROM vectors WHERE sha NOT IN (SELECT sha FROM chunks)")
    conn.execute("DELETE FROM enrich_queue WHERE sha NOT IN (SELECT sha FROM chunks)")
    # A source with enrich=0 stays fully searchable but is never entity-extracted,
    # so bulk material like meeting transcripts cannot become a graph hub and
    # distort the personalised-PageRank rerank.
    conn.execute(
        "INSERT OR IGNORE INTO enrich_queue(sha, status, updated_at) "
        "SELECT DISTINCT c.sha, 'pending', ? FROM chunks c "
        "JOIN documents d ON d.id = c.doc_id "
        "JOIN sources s ON s.id = d.source_id "
        "WHERE s.enrich = 1",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )


def status(conn: sqlite3.Connection) -> dict:
    def one(sql: str, *args):
        return conn.execute(sql, args).fetchone()[0]

    return {
        "indexed_at": db.get_meta(conn, "indexed_at"),
        "documents": one("SELECT COUNT(*) FROM documents"),
        "chunks": one("SELECT COUNT(*) FROM chunks"),
        "distinct_chunk_texts": one("SELECT COUNT(DISTINCT sha) FROM chunks"),
        "embedded": one("SELECT COUNT(*) FROM vectors"),
        "entities": one("SELECT COUNT(*) FROM entities"),
        "relations": one("SELECT COUNT(*) FROM relations"),
        "edges": one("SELECT COUNT(*) FROM edges"),
        "communities": one("SELECT COUNT(*) FROM communities"),
        "assertions": one("SELECT COUNT(*) FROM assertions"),
        "enrich_pending": one("SELECT COUNT(*) FROM enrich_queue WHERE status = 'pending'"),
        "meetings_pending": one("SELECT COUNT(*) FROM meeting_queue WHERE status = 'pending'"),
        "by_source": [dict(r) for r in conn.execute(
            "SELECT s.name, s.weight, COUNT(DISTINCT d.id) AS documents, COUNT(c.id) AS chunks "
            "FROM sources s LEFT JOIN documents d ON d.source_id = s.id "
            "LEFT JOIN chunks c ON c.doc_id = d.id GROUP BY s.id ORDER BY s.name")],
    }
