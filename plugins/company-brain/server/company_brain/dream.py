"""Maintenance report over the knowledge base.

Salvaged from the previous `brain.py dream`: it reports, it never edits. Two
open values for a functional predicate is a contradiction the tool names
rather than resolves, because picking one silently is how a memory system
starts lying.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date

STALE_MONTHS = 12


def _months_since(value: str) -> int | None:
    parts = value.split("-")
    try:
        when = date(int(parts[0]), int(parts[1]) if len(parts) > 1 else 1,
                    int(parts[2]) if len(parts) > 2 else 1)
    except (ValueError, IndexError):
        return None
    today = date.today()
    return (today.year - when.year) * 12 + (today.month - when.month)


def report(conn: sqlite3.Connection) -> dict:
    rows = [dict(r) for r in conn.execute(
        "SELECT a.*, d.rel_path, s.name AS source FROM assertions a "
        "JOIN documents d ON d.id = a.doc_id JOIN sources s ON s.id = d.source_id")]

    open_functional = defaultdict(list)
    duplicates = defaultdict(list)
    for row in rows:
        if row["functional"] and not row["until"]:
            open_functional[(row["subject"], row["predicate"])].append(row)
        duplicates[(row["subject"], row["predicate"], row["value"].lower())].append(row)

    contradictions = [
        {"subject": subject, "predicate": predicate,
         "values": [{"value": r["value"], "since": r["since"],
                     "at": "%s:%d" % (r["rel_path"], r["line"])} for r in group]}
        for (subject, predicate), group in sorted(open_functional.items()) if len(group) > 1
    ]

    duplicated = [
        {"subject": key[0], "predicate": key[1], "value": key[2],
         "at": ["%s:%d" % (r["rel_path"], r["line"]) for r in group]}
        for key, group in sorted(duplicates.items())
        if len({r["rel_path"] for r in group}) > 1
    ]

    stale = []
    for row in rows:
        if not (row["functional"] and not row["until"] and row["since"]):
            continue
        age = _months_since(row["since"])
        if age is not None and age >= STALE_MONTHS:
            stale.append({"subject": row["subject"], "predicate": row["predicate"],
                          "value": row["value"], "since": row["since"], "age_months": age,
                          "at": "%s:%d" % (row["rel_path"], row["line"])})
    stale.sort(key=lambda item: -item["age_months"])

    hubs = [dict(r) for r in conn.execute(
        "SELECT e.name, e.type, COUNT(DISTINCT m.chunk_id) AS mentions, "
        "  (SELECT COUNT(*) FROM relations r WHERE r.src = e.id OR r.dst = e.id) AS relations "
        "FROM entities e JOIN mentions m ON m.entity_id = e.id "
        "GROUP BY e.id ORDER BY mentions DESC LIMIT 12")]

    # enrich = 0 sources are never extracted from, so their documents are not
    # orphans; without the filter they would crowd the capped list out.
    orphans = [dict(r) for r in conn.execute(
        "SELECT s.name AS source, d.rel_path, d.title FROM documents d "
        "JOIN sources s ON s.id = d.source_id "
        "WHERE s.enrich = 1 AND NOT EXISTS ("
        "  SELECT 1 FROM chunks c JOIN mentions m ON m.chunk_id = c.id WHERE c.doc_id = d.id) "
        "ORDER BY s.name, d.rel_path LIMIT 40")]

    superseded = [dict(r) for r in conn.execute(
        "SELECT s.name AS source, d.rel_path, d.superseded_by FROM documents d "
        "JOIN sources s ON s.id = d.source_id WHERE d.superseded_by IS NOT NULL")]

    thin = [dict(r) for r in conn.execute(
        "SELECT s.name AS source, d.rel_path, SUM(LENGTH(c.text) - LENGTH(REPLACE(c.text,' ',''))) AS words "
        "FROM documents d JOIN sources s ON s.id = d.source_id "
        "LEFT JOIN chunks c ON c.doc_id = d.id GROUP BY d.id HAVING words IS NULL OR words < 60 "
        "ORDER BY words LIMIT 25")]

    return {
        "assertions": len(rows),
        "contradictions": contradictions,
        "duplicate_facts": duplicated,
        "ageing_facts": stale[:25],
        "hubs": hubs,
        "documents_without_entities": orphans,
        "superseded": superseded,
        "thin_documents": thin,
        "note": "Reported, never edited. Close an outdated functional fact with `until:` "
                "rather than deleting it.",
    }
