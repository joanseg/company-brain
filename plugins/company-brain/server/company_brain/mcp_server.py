"""MCP surface for the company brain.

Every tool returns structured data with provenance. Retrieval ranks evidence;
it never claims to be the answer — the reasoning stays with the model.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import capture, community, db, dense, dream, enrich, graph, indexer, meetings, search, webui

server = MCPServer(
    name="brain",
    version="0.1.0",
    instructions=(
        "Company knowledge retrieval over the configured markdown repositories. "
        "Use `search_evidence` for a named entity, decision, person or document; use "
        "`search_themes` for patterns that span many documents. Always "
        "cite the returned path and line, and keep stated evidence separate from "
        "your own inference."
    ),
)


@contextmanager
def _conn():
    """A connection per call: sqlite3 connections are not thread-safe, and
    `with sqlite3.connect(...)` commits but never closes."""
    conn = db.connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _parse(payload: str, field: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("%s must be valid JSON: %s" % (field, exc)) from exc


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------

@server.tool(
    description="Retrieve cited evidence for a specific question. mode='local' for a named "
                "entity, person, partner, project or decision; mode='global' to spread results "
                "across strategic areas. Returns source chunks with path and line.")
def search_evidence(question: str, mode: str = "local", limit: int = 8,
                    explain: bool = False) -> dict:
    with _conn() as conn:
        return search.run(conn, question, max(1, min(limit, 30)), mode, explain)


@server.tool(
    description="Answer a thematic, company-wide question from community summaries rather than "
                "individual passages — recurring risks, patterns, how areas relate. Requires "
                "community summaries (run /brain-enrich --communities). Returns ranked community "
                "claims with citations back to source files.")
def search_themes(question: str, level: int | None = None, limit: int = 6) -> dict:
    with _conn() as conn:
        return community.search(conn, question, level, max(1, min(limit, 20)))


@server.tool(
    description="Everything the graph knows about one entity: its type, description, aliases, "
                "related entities, the documents that mention it, and any typed assertions "
                "about it.")
def entity(name: str) -> dict:
    with _conn() as conn:
        return enrich.entity_report(conn, name)


@server.tool(
    description="Graph neighbourhood of a node id (for example 'e:12' for an entity or 'c:345' "
                "for a chunk). Use after `entity` to explore how something connects.")
def neighbours(node: str, limit: int = 30) -> dict:
    with _conn() as conn:
        return {"node": node, "neighbours": [
            {"node": other, "kind": kind, "weight": weight}
            for other, kind, weight in graph.neighbours(conn, node, limit)]}


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

@server.tool(
    description="Capture new knowledge as a markdown note in the anchor repo and index it "
                "immediately. Use when the user asks to remember, record or file something. "
                "facts use `predicate:: value` or `subject|predicate:: value`. Never invent "
                "typed facts the user did not state.")
def remember(content: str, title: str | None = None, kind: str = "note",
             tags: list[str] | None = None, aliases: list[str] | None = None,
             related: list[str] | None = None, facts: list[str] | None = None,
             confidence: str = "stated", source_url: str | None = None,
             source: str | None = None, folder: str | None = None) -> dict:
    with _conn() as conn:
        written = capture.add(conn, content, title, source, folder, kind, tags,
                              aliases, related, facts, confidence, source_url)
        stats = indexer.refresh(conn)
        result = dense.backfill(conn)
        graph.rebuild(conn)
    return written | {"indexed": {"added": stats["added"], "updated": stats["updated"],
                                  "embedded": result.get("embedded", 0)}}


# ---------------------------------------------------------------------------
# index lifecycle
# ---------------------------------------------------------------------------

@server.tool(description="Index health: document and chunk counts per source, embedding "
                         "coverage, graph size, and how much enrichment is outstanding.")
def status() -> dict:
    with _conn() as conn:
        report = indexer.status(conn)
        report["dense_available"] = dense.available()
        if not report["dense_available"]:
            report["dense_note"] = dense.unavailable_reason()
        return report


@server.tool(description="Refresh the index from disk. Incremental: unchanged files are skipped "
                         "and only new or changed text is re-embedded.")
def reindex(embed: bool = True) -> dict:
    with _conn() as conn:
        stats = indexer.refresh(conn)
        if embed:
            stats["dense"] = dense.backfill(conn)
        stats["edges"] = graph.rebuild(conn)
        return stats


@server.tool(description="List the repositories the brain reads, with their retrieval weights.")
def sources() -> dict:
    return {"sources": [{"name": s["name"], "root": str(s["root"]), "weight": s["weight"],
                         "exclude": s["exclude"]} for s in db.load_sources()]}


@server.tool(description="Add another repository or folder as a knowledge source. Weight scales "
                         "how much its evidence is trusted relative to the anchor repo (1.0).")
def add_source(path: str, name: str, weight: float = 0.8,
               exclude: list[str] | None = None) -> dict:
    from pathlib import Path

    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Not a directory: %s" % root)
    existing = db.load_sources()
    if any(s["name"] == name for s in existing):
        raise ValueError("A source named %r already exists." % name)
    existing.append({"name": name, "root": root, "weight": float(weight),
                     "exclude": list(exclude or [])})
    db.save_sources(existing)
    return {"added": name, "root": str(root), "weight": weight,
            "next": "Run reindex to pull it in."}


@server.tool(description="Maintenance report: contradictions on functional predicates, duplicate "
                         "facts, ageing assertions, graph hubs, documents no entity touches, and "
                         "superseded or thin documents. Reports only; never edits.")
def maintenance() -> dict:
    with _conn() as conn:
        return dream.report(conn)


# ---------------------------------------------------------------------------
# enrichment — driven by the /brain-enrich skill, not by this server
# ---------------------------------------------------------------------------

@server.tool(description="Lease the next batch of chunks awaiting entity extraction. Used by the "
                         "brain-enrich skill; returns chunk text plus the sha to send back.")
def enrich_pull(limit: int = 15) -> dict:
    with _conn() as conn:
        batch = enrich.pull(conn, max(1, min(limit, 60)))
        return {"batch": batch, "count": len(batch),
                "pending": conn.execute(
                    "SELECT COUNT(*) FROM enrich_queue WHERE status = 'pending'").fetchone()[0]}


@server.tool(description="Write extracted entities and relations back. `results` is a JSON array "
                         'of {"sha","entities":[{"name","type","description","aliases"}],'
                         '"relations":[{"src","dst","type","description"}]}.')
def enrich_push(results: str) -> dict:
    payload = _parse(results, "results")
    if isinstance(payload, dict):
        payload = payload.get("results", [])
    with _conn() as conn:
        stats = enrich.push(conn, payload)
        graph.rebuild(conn)
        return stats


# ---------------------------------------------------------------------------
# meetings — driven by the /brain-meetings skill, not by this server
# ---------------------------------------------------------------------------

@server.tool(description="List Circleback meetings in a date window, marking the ones the brain "
                         "already holds. Read-only: syncs nothing. Show the user this list and "
                         "let them choose before calling meetings_sync.")
def meetings_list(days: int = 30, query: str | None = None) -> dict:
    with _conn() as conn:
        rows = meetings.list_meetings(conn, max(1, min(days, 365)), query)
        return {"meetings": rows, "count": len(rows),
                "unsynced": sum(1 for r in rows if not r["synced"])}


@server.tool(description="Fetch transcripts for the meeting ids the user chose and queue them for "
                         "summarising. `meeting_ids` is a JSON array of ids from meetings_list. "
                         "Never call this without an explicit choice from the user.")
def meetings_sync(meeting_ids: str) -> dict:
    payload = _parse(meeting_ids, "meeting_ids")
    if isinstance(payload, dict):
        payload = payload.get("meeting_ids", [])
    with _conn() as conn:
        result = meetings.sync(conn, [str(i) for i in payload])
        result["next"] = "Run reindex, then summarise with the brain-meetings skill."
        return result


@server.tool(description="Lease the next batch of synced transcripts awaiting a summary. Used by "
                         "the brain-meetings skill; returns the full transcript text.")
def meetings_pull(limit: int = 5) -> dict:
    with _conn() as conn:
        batch = meetings.pull(conn, max(1, min(limit, 20)))
        return {"batch": batch, "count": len(batch),
                "pending": conn.execute(
                    "SELECT COUNT(*) FROM meeting_queue WHERE status = 'pending'").fetchone()[0]}


@server.tool(description="Write meeting summaries back into the brain. `results` is a JSON array "
                         'of {"meeting_id","summary","facts":["predicate:: value"],'
                         '"related":["entity"]}. Each becomes a captured note.')
def meetings_push(results: str) -> dict:
    payload = _parse(results, "results")
    if isinstance(payload, dict):
        payload = payload.get("results", [])
    with _conn() as conn:
        return meetings.push(conn, payload)


@server.tool(description="Fold duplicate entities together. Only merges pairs where each side was "
                         "independently declared an alias of the other, so a product named after its "
                         "company is left alone. Pass apply=false first to see the plan. Run after "
                         "enrichment and before detecting communities.")
def merge_entities(apply: bool = False) -> dict:
    with _conn() as conn:
        result = enrich.merge_duplicates(conn, apply)
        if apply:
            graph.rebuild(conn)
        return result


@server.tool(description="Partition the entity graph into communities with Leiden. Run after "
                         "enrichment and before summarising communities.")
def communities_detect() -> dict:
    with _conn() as conn:
        return community.detect(conn)


@server.tool(description="Communities that still need a summary, with the entities, relations "
                         "and source evidence needed to write one.")
def communities_pending(limit: int = 20) -> dict:
    with _conn() as conn:
        batch = community.pending(conn, max(1, min(limit, 50)))
        return {"batch": batch, "count": len(batch)}


@server.tool(description="Store community summaries. `summaries` is a JSON array of "
                         '{"community_id","sha","title","summary","rating"} where rating is 0-10 '
                         "importance.")
def communities_push(summaries: str) -> dict:
    payload = _parse(summaries, "summaries")
    if isinstance(payload, dict):
        payload = payload.get("summaries", [])
    with _conn() as conn:
        return community.push_summaries(conn, payload)


@server.tool(description="Open the Company Brain dashboard in the user's default browser. "
                         "Returns the URL it tried to open. The dashboard is read-only and "
                         "runs only while this Claude Code session is alive.")
def open_view() -> dict:
    import subprocess
    import sys

    address = webui.url()
    if not address:
        raise ValueError("dashboard server is not running — restart the Claude Code session")
    command = ("open", address) if sys.platform == "darwin" else (
        ("cmd", "/c", "start", "", address) if sys.platform == "win32" else ("xdg-open", address))
    try:
        subprocess.Popen(command, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"url": address, "opened": True}
    except OSError as exc:
        return {"url": address, "opened": False, "error": str(exc)}


def main() -> None:
    webui.start()      # daemon thread; failure is non-fatal, open_view reports it
    server.run("stdio")


if __name__ == "__main__":
    main()
