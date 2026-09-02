"""Pull meetings from Circleback into the brain.

Nothing syncs on its own. `/brain-meetings` lists what Circleback has, the user
picks, and only those transcripts are fetched. Transcripts are raw material and
live in their own source that is never entity-extracted; the summary a subagent
writes from one is the knowledge, and it enters through `capture.add()` like any
hand-written note.

Circleback's own `notes`, `actionItems` and `insights` are deliberately unused —
the point is the brain's interpretation, not the vendor's.

The official CLI (`@circleback/cli`) is a thin JSON-RPC client over the same
endpoint, so calling it directly keeps a Python plugin free of a Node dependency.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import capture, db
from .ingest import slug

API_URL = "https://circleback.ai/api/mcp"
SETTINGS_URL = "https://circleback.ai/settings?tab=api-access"
TIMEOUT_SECONDS = 60

TRANSCRIPT_DIR = "meeting-transcripts"
TRANSCRIPT_SOURCE = "meeting-transcripts"
TRANSCRIPT_WEIGHT = 0.4

# A row the brain still holds a usable transcript for. 'missing' is deliberately
# absent: its file is gone, so the meeting must be offerable and re-fetchable
# rather than stranded in the queue forever.
HELD_STATUSES = ("pending", "leased", "done")


def _held(conn: sqlite3.Connection) -> set[str]:
    return {r["meeting_id"] for r in conn.execute(
        "SELECT meeting_id FROM meeting_queue WHERE status IN (?,?,?)", HELD_STATUSES)}


def _api_key() -> str:
    key = os.getenv("CIRCLEBACK_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "CIRCLEBACK_API_KEY is not set. Create a key at %s and add it to the "
            "plugin's settings." % SETTINGS_URL)
    return key


def _unwrap(result: dict) -> dict:
    """MCP returns either structured content or JSON inside a text block."""
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for item in result.get("content") or []:
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except (json.JSONDecodeError, KeyError):
                return {"text": item.get("text", "")}
    return result


def _body(payload: bytes, content_type: str) -> dict:
    text = payload.decode(errors="replace")
    if "text/event-stream" in content_type:
        # SSE allows one event's payload to span several consecutive `data:`
        # lines; they must be joined with newlines before parsing. Only the
        # first event is relevant here — stop at the blank line that ends it.
        data_lines = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif data_lines:
                break
        if not data_lines:
            raise ValueError("Circleback returned an event stream with no data frame.")
        text = "\n".join(data_lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Circleback returned an unreadable response (%s): %r" % (exc, text[:200])
        ) from exc


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """Extract Circleback's own message from an HTTP error body, if any."""
    raw = exc.read().decode(errors="replace")
    detail = raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        detail = parsed.get("error") or parsed.get("message") or raw
        if isinstance(detail, dict):
            detail = detail.get("message", raw)
    if exc.code in (401, 403):
        return "Circleback rejected the API key (HTTP %s): %s. Create a new key at %s." % (
            exc.code, detail, SETTINGS_URL)
    if exc.code == 429:
        return "Circleback rate-limited this request (HTTP 429): %s. Retry later." % detail
    return "Circleback returned HTTP %s: %s" % (exc.code, detail)


def _call(tool: str, args: dict, opener=None) -> dict:
    """One JSON-RPC `tools/call`. `opener` is injected by tests."""
    request = urllib.request.Request(
        API_URL,
        data=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {"intent": "Company Brain sync.", **args}},
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer %s" % _api_key(),
        },
    )
    try:
        with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT_SECONDS) as response:
            envelope = _body(response.read(), response.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as exc:
        raise ValueError(_http_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        raise ValueError("Could not reach Circleback: %s" % exc.reason) from exc
    if envelope.get("error"):
        raise ValueError(envelope["error"].get("message", "Circleback rejected the request."))
    return _unwrap(envelope.get("result") or {})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_sources() -> dict:
    """Register the transcripts source and keep the anchor from double-indexing it.

    The transcripts directory lives inside the anchor root, so the anchor would
    otherwise index every transcript a second time — at full weight and with
    enrichment on, undoing the whole point of a separate source. `ingest.walk()`
    matches single path segments, which is why the directory is top-level and
    named with one segment.
    """
    sources = db.load_sources()
    anchor = sources[0]
    registered = not any(s["name"] == TRANSCRIPT_SOURCE for s in sources)
    excluded = TRANSCRIPT_DIR not in anchor.get("exclude", [])

    if registered:
        sources.append({
            "name": TRANSCRIPT_SOURCE,
            "root": db.project_dir() / TRANSCRIPT_DIR,
            "weight": TRANSCRIPT_WEIGHT,
            "enrich": False,
            "exclude": [],
        })
    if excluded:
        anchor["exclude"] = list(anchor.get("exclude", [])) + [TRANSCRIPT_DIR]
    if registered or excluded:
        db.save_sources(sources)
    return {"registered": registered, "excluded": excluded}


MAX_PAGES = 10


def list_meetings(conn: sqlite3.Connection, days: int = 30, query: str | None = None,
                  opener=None) -> dict:
    """What Circleback has in the window, with what the brain already holds marked.

    Read-only on purpose: browsing must never write, so the user can look before
    choosing anything.

    Pages are walked until one comes back empty, because selection is this
    feature's whole control surface — a user cannot pick a meeting the listing
    never showed. Only the `meetings` list is read: the live envelope's total and
    paging field names are unverified, so none is assumed. `truncated` says the
    walk stopped at MAX_PAGES and the list may be short.
    """
    args: dict = {"startDate":
                  (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()}
    if query:
        args["searchTerm"] = query

    found: list[dict] = []
    truncated = True
    for page in range(MAX_PAGES):
        args["pageIndex"] = page
        payload = _call("SearchMeetings", args, opener=opener)
        page_meetings = payload.get("meetings") or []
        if not page_meetings:
            truncated = False
            break
        found.extend(page_meetings)

    held = _held(conn)
    rows = []
    for meeting in found:
        rows.append({
            "id": meeting.get("id", ""),
            "title": (meeting.get("name") or "").strip() or "Untitled meeting",
            "date": (meeting.get("createdAt") or "")[:10],
            "duration": meeting.get("duration") or 0,
            "attendees": [a.get("name") or a.get("email") or ""
                          for a in meeting.get("attendees") or []],
            "tags": list(meeting.get("tags") or []),
            "synced": meeting.get("id", "") in held,
        })
    return {"meetings": rows, "truncated": truncated}


def _transcript_markdown(meeting: dict) -> str:
    """Frontmatter carries `date:` so freshness reflects when the meeting happened,
    not when it was fetched. One `## Transcript` heading is enough — `heading_chunks`
    packs long sections to CHUNK_WORDS on its own.
    """
    title = (meeting.get("name") or "").strip() or "Untitled meeting"
    held = (meeting.get("createdAt") or "")[:10]
    attendees = [a.get("name") or a.get("email") or "" for a in meeting.get("attendees") or []]
    lines = [
        "---",
        "name: %s" % slug(title),
        "type: transcript",
        "date: %s" % held,
        "attendees: [%s]" % ", ".join(a for a in attendees if a),
        "tags: [meeting-transcript]",
        "---",
        "",
        "# %s" % title,
        "",
        "## Transcript",
        "",
    ]
    for turn in meeting.get("transcript") or []:
        speaker = (turn.get("speaker") or "Unknown").strip()
        text = (turn.get("text") or "").strip()
        if text:
            lines.append("%s: %s" % (speaker, text))
    lines += ["", "## Provenance", "- source:: circleback"]
    if meeting.get("url"):
        lines.append("- reference:: %s" % meeting["url"])
    return "\n".join(lines).rstrip() + "\n"


def _unique_name(root, stem: str, meeting_id: str) -> str:
    """Two meetings the same day with the same title are rare but real;
    never let a later one overwrite an earlier one on disk.
    """
    name = stem + ".md"
    if not (root / name).exists():
        return name
    base = "%s-%s" % (stem, slug(meeting_id)[:8] or "dup")
    name = base + ".md"
    ordinal = 2
    while (root / name).exists():
        name = "%s-%d.md" % (base, ordinal)
        ordinal += 1
    return name


def sync(conn: sqlite3.Connection, meeting_ids: list[str], opener=None) -> dict:
    """Fetch and store only the meetings the user chose."""
    result: dict = {"synced": [], "skipped": [], "paths": [], "not_returned": []}
    already_synced = _held(conn)
    wanted = [mid for mid in meeting_ids if mid not in already_synced]
    result["skipped"] = [mid for mid in meeting_ids if mid in already_synced]
    if not wanted:
        return result

    ensure_sources()
    root = db.project_dir() / TRANSCRIPT_DIR
    root.mkdir(parents=True, exist_ok=True)
    payload = _call("GetTranscriptsForMeetings", {"meetingIds": wanted}, opener=opener)

    for meeting in payload.get("meetings") or []:
        meeting_id = meeting.get("id", "")
        if not meeting_id or meeting_id in already_synced:
            continue
        title = (meeting.get("name") or "").strip() or "Untitled meeting"
        held_at = (meeting.get("createdAt") or "")[:10]
        stem = "%s-%s" % (held_at or "undated", slug(title)[:70] or "meeting")
        name = _unique_name(root, stem, meeting_id)
        (root / name).write_text(_transcript_markdown(meeting), encoding="utf-8")
        rel_path = "%s/%s" % (TRANSCRIPT_DIR, name)
        # Upsert, not insert: a meeting whose transcript was deleted is marked
        # 'missing' and is re-fetched here, so its row must be replaced.
        conn.execute(
            "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
            "VALUES(?,?,?,?,'pending',?) ON CONFLICT(meeting_id) DO UPDATE SET "
            "title = excluded.title, held_at = excluded.held_at, rel_path = excluded.rel_path, "
            "status = 'pending', updated_at = excluded.updated_at",
            (meeting_id, title, held_at, rel_path, _now()))
        # Commit per meeting — and remember it locally — so a later failure in
        # this same batch cannot roll back files already written to disk, and
        # a duplicate id within one response is a no-op rather than a crash.
        conn.commit()
        already_synced.add(meeting_id)
        result["synced"].append(meeting_id)
        result["paths"].append(rel_path)
    # Requested but absent from the response — a partial result, a rate limit
    # mid-batch, an id Circleback no longer knows. Named so the user can
    # re-select them rather than assuming the whole selection landed.
    written = set(result["synced"])
    result["not_returned"] = [mid for mid in wanted if mid not in written]
    return result


LEASE_MINUTES = 30


def reset_leases(conn: sqlite3.Connection) -> int:
    """Return abandoned leases to the queue, as `enrich.reset_leases` does."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(minutes=LEASE_MINUTES)).isoformat(timespec="seconds")
    cursor = conn.execute(
        "UPDATE meeting_queue SET status = 'pending' "
        "WHERE status = 'leased' AND updated_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount


def pull(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """Lease the next transcripts awaiting a summary.

    Held in an IMMEDIATE transaction so two callers cannot lease the same
    meeting and write two summaries of it. A row whose transcript file is gone
    or unreadable is marked 'missing' and left out of the batch, rather than
    handed to a subagent with nothing to summarise. 'missing' is not a held
    status, so the meeting is listed as unsynced again and `sync()` re-fetches
    it — a deleted transcript is recoverable, not a dead row.
    """
    reset_leases(conn)
    conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute(
        "SELECT meeting_id, title, held_at, rel_path FROM meeting_queue "
        "WHERE status = 'pending' ORDER BY held_at LIMIT ?", (limit,)).fetchall()
    if rows:
        conn.executemany(
            "UPDATE meeting_queue SET status = 'leased', updated_at = ? WHERE meeting_id = ?",
            [(_now(), r["meeting_id"]) for r in rows])
    conn.commit()

    root = db.project_dir()
    batch = []
    for row in rows:
        path = root / row["rel_path"]
        try:
            transcript = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            conn.execute("UPDATE meeting_queue SET status = 'missing', updated_at = ? "
                         "WHERE meeting_id = ?", (_now(), row["meeting_id"]))
            conn.commit()
            continue
        batch.append({
            "meeting_id": row["meeting_id"],
            "title": row["title"],
            "held_at": row["held_at"],
            "transcript": transcript,
        })
    return batch


def _skipped(meeting_id, reason: str) -> str:
    """One printable line per dropped result: which meeting, and why."""
    return "%s: %s" % (meeting_id or "<unresolved result>", reason)


def push(conn: sqlite3.Connection, results: list[dict]) -> dict:
    """Capture each summary as a note and close its queue row.

    Commits after each result, unlike `enrich.push()`'s single commit at the
    end. `enrich.push()` is pure SQL, so a mid-loop exception rolls back
    cleanly; here `capture.add()` writes a markdown file to disk, a side
    effect no rollback can undo — without a commit per meeting, a later
    result raising would revert an earlier meeting's row to 'leased' while
    its note stays on disk, and it would be re-leased and re-captured as a
    duplicate once the lease expires.

    `results` is free-form subagent output, so a malformed fact or a bad
    shape for one meeting is an ordinary input to route to `skipped`, not a
    reason to abort the rest of the batch.
    """
    stats: dict = {"captured": 0, "paths": [], "skipped": []}
    known = {r["meeting_id"]: dict(r) for r in conn.execute(
        "SELECT meeting_id, title, held_at, status FROM meeting_queue")}

    for result in results:
        meeting_id = None
        try:
            meeting_id = result.get("meeting_id")
            summary = (result.get("summary") or "").strip()
            row = known.get(meeting_id)
            if row is None:
                stats["skipped"].append(_skipped(meeting_id, "not in the meeting queue"))
                continue
            if row["status"] == "done":
                stats["skipped"].append(_skipped(meeting_id, "already captured"))
                continue
            if not summary:
                stats["skipped"].append(_skipped(meeting_id, "empty summary"))
                continue
            written = capture.add(
                conn, content=summary,
                title="%s — %s" % (row["title"], row["held_at"] or "meeting"),
                kind="meeting", tags=["meeting", "circleback"],
                related=list(result.get("related") or []),
                facts=list(result.get("facts") or []),
                confidence="observed")
            conn.execute("UPDATE meeting_queue SET status = 'done', updated_at = ? "
                         "WHERE meeting_id = ?", (_now(), meeting_id))
            conn.commit()
            row["status"] = "done"
            stats["captured"] += 1
            stats["paths"].append(written["relative"])
        except Exception as exc:
            # Named, not swallowed: a meeting that fails silently is re-leased
            # when its lease expires and fails identically forever, while the
            # skill reports a summary the user never received.
            stats["skipped"].append(
                _skipped(meeting_id, "%s: %s" % (type(exc).__name__, str(exc)[:200])))
    stats["pending"] = conn.execute(
        "SELECT COUNT(*) FROM meeting_queue WHERE status = 'pending'").fetchone()[0]
    return stats
