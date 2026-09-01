"""Pull meetings from Circleback into the brain.

Nothing syncs on its own. `/brain-meetings` lists what Circleback has, the user
picks, and only those transcripts are fetched. Transcripts are raw material and
live in their own source that is never entity-extracted; the summary a subagent
writes from one is the knowledge, and it enters through `capture.add()` like any
hand-written note.

Circleback's own `notes` and `actionItems` are deliberately unused — the point is
the brain's interpretation, not the vendor's.

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

API_URL = "https://circleback.ai/api/mcp"
SETTINGS_URL = "https://circleback.ai/settings?tab=api-access"
TIMEOUT_SECONDS = 60

TRANSCRIPT_DIR = "meeting-transcripts"
TRANSCRIPT_SOURCE = "meeting-transcripts"
TRANSCRIPT_WEIGHT = 0.4


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


def list_meetings(conn: sqlite3.Connection, days: int = 30, query: str | None = None,
                 opener=None) -> list[dict]:
    """What Circleback has in the window, with what the brain already holds marked.

    Read-only on purpose: browsing must never write, so the user can look before
    choosing anything.
    """
    args: dict = {"pageIndex": 0,
                  "startDate": (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()}
    if query:
        args["searchTerm"] = query
    payload = _call("SearchMeetings", args, opener=opener)

    held = {r["meeting_id"] for r in conn.execute("SELECT meeting_id FROM meeting_queue")}
    rows = []
    for meeting in payload.get("meetings") or []:
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
    return rows
