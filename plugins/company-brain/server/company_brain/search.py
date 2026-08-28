"""Hybrid retrieval: lexical + dense fused by RRF, reranked by the graph.

No single signal is allowed to stand alone. Lexical finds exact names, dense
finds paraphrase and cross-language matches, and the graph is the only part
that knows two passages are about the same project — which is exactly what
similarity search cannot see.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from . import dense, graph, lexical

RRF_K = 60
W_FUSED = 0.55
W_GRAPH = 0.25
W_SOURCE = 0.10
W_FRESH = 0.10
SUPERSEDED_PENALTY = 0.45
MAX_PER_DOC = 2

LOW_QUALITY = ("template", "placeholder", "testfile", "test-file")


def _ranks(pairs: list[tuple[int, float]]) -> dict[int, int]:
    return {chunk_id: rank for rank, (chunk_id, _) in enumerate(pairs, 1)}


def _freshness(doc_date: str | None) -> float:
    if not doc_date:
        return 0.3
    try:
        age = (date.today() - date.fromisoformat(doc_date[:10])).days
    except ValueError:
        return 0.3
    return 1.0 / (1.0 + max(0, age) / 1825.0)


def _entity_seeds(conn: sqlite3.Connection, words: list[str]) -> dict[str, float]:
    """Entities whose alias appears in the question, as PPR restart mass."""
    if not words:
        return {}
    joined = " ".join(words)
    seeds: dict[str, float] = {}
    for row in conn.execute(
            "SELECT e.id, a.alias FROM entity_aliases a JOIN entities e ON e.id = a.entity_id"):
        alias = row["alias"]
        if len(alias) > 2 and alias.replace("-", " ") in joined:
            seeds["e:%d" % row["id"]] = 1.0
    return seeds


def _hydrate(conn: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT c.id, c.text, c.heading_path, c.line_start, d.rel_path, d.title, d.doc_date, "
        f"d.superseded_by, s.name AS source, s.weight AS source_weight "
        f"FROM chunks c JOIN documents d ON d.id = c.doc_id JOIN sources s ON s.id = d.source_id "
        f"WHERE c.id IN ({placeholders})", tuple(ids)).fetchall()
    return {r["id"]: dict(r) for r in rows}


def _quality(row: dict) -> float:
    name = (row["rel_path"] + " " + row["title"]).lower()
    if any(flag in name for flag in LOW_QUALITY):
        return 0.15
    return float(row["source_weight"])


def run(conn: sqlite3.Connection, question: str, limit: int = 8,
        mode: str = "local", explain: bool = False) -> dict:
    words = lexical.terms(question)
    lex = lexical.search(conn, question, 200)
    den = dense.search(conn, question, 200)

    lex_rank, den_rank = _ranks(lex), _ranks(den)
    fused: dict[int, float] = {}
    for chunk_id, rank in lex_rank.items():
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    for chunk_id, rank in den_rank.items():
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    if not fused:
        return {"question": question, "mode": mode, "results": [],
                "signals": _signal_report(den, conn)}

    peak = max(fused.values()) or 1.0
    fused = {k: v / peak for k, v in fused.items()}

    seeds = {"c:%d" % cid: score for cid, score in
             sorted(fused.items(), key=lambda p: -p[1])[:20]}
    seeds.update(_entity_seeds(conn, words))
    propagation = graph.personalised(conn, seeds)

    rows = _hydrate(conn, list(fused))
    scored = []
    for chunk_id, base in fused.items():
        row = rows.get(chunk_id)
        if not row:
            continue
        ppr = propagation.get("c:%d" % chunk_id, 0.0)
        fresh = _freshness(row["doc_date"])
        quality = _quality(row)
        total = W_FUSED * base + W_GRAPH * ppr + W_SOURCE * quality + W_FRESH * fresh
        if row["superseded_by"]:
            total *= SUPERSEDED_PENALTY
        scored.append((total, chunk_id, row, {
            "fused": round(base, 4), "graph": round(ppr, 4),
            "source_quality": round(quality, 3), "freshness": round(fresh, 3)}))

    scored.sort(key=lambda item: -item[0])

    results = []
    per_doc: dict[str, int] = {}
    seen_headings: set[tuple[str, str]] = set()
    seen_areas: set[str] = set()
    for total, chunk_id, row, signals in scored:
        key = row["source"] + ":" + row["rel_path"]
        heading_key = (key, row["heading_path"])
        if per_doc.get(key, 0) >= MAX_PER_DOC or heading_key in seen_headings:
            continue
        area = row["source"] + ":" + (row["rel_path"].split("/")[0] if "/" in row["rel_path"] else "root")
        if mode == "global" and len(results) < 5 and area in seen_areas:
            continue
        per_doc[key] = per_doc.get(key, 0) + 1
        seen_headings.add(heading_key)
        seen_areas.add(area)
        item = {
            "source": row["source"],
            "path": row["rel_path"],
            "line": row["line_start"],
            "title": row["title"],
            "heading": row["heading_path"],
            "date": row["doc_date"],
            "superseded": bool(row["superseded_by"]),
            "score": round(total, 5),
            "text": row["text"],
        }
        if explain:
            item["signals"] = signals
        results.append(item)
        if len(results) >= limit:
            break

    return {"question": question, "mode": mode, "results": results,
            "signals": _signal_report(den, conn)}


def _signal_report(den: list, conn: sqlite3.Connection) -> dict:
    report = {"lexical": True, "dense": bool(den), "graph": graph.load(conn) is not None}
    if not den:
        report["dense_note"] = dense.unavailable_reason() or (
            "no vectors stored yet — run /brain-index after /brain-setup")
    return report
