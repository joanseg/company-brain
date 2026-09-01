# Circleback Meeting Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/brain-meetings`, which lists the user's Circleback meetings, syncs only the ones they pick, and has subagents write comprehensive summaries into the company brain.

**Architecture:** Pull-only. A new `meetings.py` calls Circleback's JSON-RPC endpoint directly with stdlib HTTP. Chosen transcripts are written as markdown into their own low-weight source that is excluded from entity extraction, so they are searchable but never become graph hubs. Summaries are produced by subagents in the current session (the `enrich.py` leased-queue pattern) and land via `capture.add()`, identical to a hand-written `/brain-add` note.

**Tech Stack:** Python 3.11+, stdlib `urllib.request` and `json` (no new dependencies), SQLite, pytest, MCP (`mcp>=1.2`).

**Spec:** `docs/superpowers/specs/2026-09-01-meeting-sync-design.md`

## Global Constraints

- **Repo/branch:** `joanseg/company-brain`, branch `feat/meeting-sync`. All paths below are relative to `plugins/company-brain/`.
- **Run tests with:** `uv run --project server --group dev pytest server/tests/ -q` executed from `plugins/company-brain/`.
- **No new runtime dependencies.** `server/pyproject.toml` dependencies stay exactly `mcp>=1.2`, `numpy>=2.0`, `python-igraph>=0.11`, `leidenalg>=0.10`. Use stdlib only.
- **No network in tests.** Every function that talks to Circleback takes an `opener` parameter defaulting to `urllib.request.urlopen`; tests inject a fake.
- **Endpoint:** `https://circleback.ai/api/mcp`. **Settings URL:** `https://circleback.ai/settings?tab=api-access`. **Env var:** `CIRCLEBACK_API_KEY`. **Key prefix:** `cb_`.
- **Circleback tools called:** `SearchMeetings`, `GetTranscriptsForMeetings`. Every `tools/call` params object must include an `intent` string — the CLI always sends one.
- **Transcript directory and source name:** both `meeting-transcripts`, a single path segment at the anchor repo root. This is load-bearing: `ingest.walk()` matches individual path *segments*, so a multi-segment exclude would silently never match.
- **Selection parsing has no Python test** — resolving `1,3,7-9` / `all` / `none`
  is the skill's job (Task 8), not the server's. The spec lists it under Testing;
  it is covered by the skill contract instead. Do not add a parser to `meetings.py`.
- **Style:** match the codebase — module docstring explaining *why*, `from __future__ import annotations`, sparse comments that justify decisions rather than restate code, short functions.
- **Never use** Circleback's `notes`, `actionItems` or `insights` fields. The brain writes its own interpretation.

---

### Task 1: `enrich` flag on sources

The one change to existing behaviour. Without it, transcripts get entity-extracted and become graph hubs, defeating the whole mitigation.

**Files:**
- Modify: `server/company_brain/db.py` (SCHEMA `sources` table ~line 30, `_migrate` ~line 180, `load_sources` ~line 225, `save_sources` ~line 252)
- Modify: `server/company_brain/indexer.py` (`_upsert_source` lines 17-24, `_prune_and_enqueue` lines 130-138)
- Test: `server/tests/test_sources_enrich.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `db.load_sources()` entries gain `"enrich": bool` (default `True`). `db.save_sources(sources: list[dict])` persists it. Column `sources.enrich INTEGER NOT NULL DEFAULT 1`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_sources_enrich.py`:

```python
"""A source can opt out of entity extraction while staying fully searchable."""
import json

from company_brain import db, indexer


def test_load_sources_defaults_enrich_to_true(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    sources = db.load_sources()
    assert sources[0]["enrich"] is True


def test_save_and_load_round_trips_enrich_false(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    sources = db.load_sources()
    sources.append({"name": "transcripts", "root": tmp_path / "t",
                    "weight": 0.4, "exclude": [], "enrich": False})
    db.save_sources(sources)

    written = json.loads(db.sources_path().read_text())["sources"]
    assert written[1]["enrich"] is False
    assert db.load_sources()[1]["enrich"] is False


def test_migrate_adds_enrich_column_to_existing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    conn = db.connect()
    conn.execute("DROP TABLE sources")
    conn.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, "
                 "root TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0, added_at TEXT NOT NULL)")
    conn.execute("INSERT INTO sources(name, root, weight, added_at) "
                 "VALUES('old', '/tmp/old', 1.0, '2026-01-01')")
    conn.commit()
    conn.close()

    conn = db.connect()
    assert conn.execute("SELECT enrich FROM sources WHERE name = 'old'").fetchone()[0] == 1
    conn.close()


def test_enqueue_skips_chunks_from_a_non_enriching_source(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    quiet = tmp_path / "quiet"
    quiet.mkdir()
    (quiet / "note.md").write_text("# Quiet\n\nBody text that would otherwise be extracted.\n")
    loud = tmp_path / "loud"
    loud.mkdir()
    (loud / "note.md").write_text("# Loud\n\nBody text that should be extracted.\n")

    # connect() first: it creates the state directory that save_sources writes into.
    conn = db.connect()
    db.save_sources([
        {"name": "loud", "root": loud, "weight": 1.0, "exclude": [], "enrich": True},
        {"name": "quiet", "root": quiet, "weight": 0.4, "exclude": [], "enrich": False},
    ])
    indexer.refresh(conn)

    queued = {r[0] for r in conn.execute(
        "SELECT s.name FROM enrich_queue q JOIN chunks c ON c.sha = q.sha "
        "JOIN documents d ON d.id = c.doc_id JOIN sources s ON s.id = d.source_id")}
    assert queued == {"loud"}

    searchable = {r[0] for r in conn.execute(
        "SELECT s.name FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "JOIN sources s ON s.id = d.source_id")}
    assert searchable == {"loud", "quiet"}
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd plugins/company-brain
uv run --project server --group dev pytest server/tests/test_sources_enrich.py -q
```

Expected: FAIL — `KeyError: 'enrich'` and `sqlite3.OperationalError: no such column: enrich`.

- [ ] **Step 3: Add the column to the schema**

In `server/company_brain/db.py`, in the `SCHEMA` string, change the `sources` table to:

```sql
CREATE TABLE IF NOT EXISTS sources (
    id       INTEGER PRIMARY KEY,
    name     TEXT UNIQUE NOT NULL,
    root     TEXT NOT NULL,
    weight   REAL NOT NULL DEFAULT 1.0,
    enrich   INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL
);
```

- [ ] **Step 4: Migrate existing databases**

In `db._migrate()`, extend the existing tuple so it reads:

```python
    for table, column, decl in (("community_summaries", "vec", "BLOB"),
                                ("sources", "enrich", "INTEGER NOT NULL DEFAULT 1")):
```

- [ ] **Step 5: Carry `enrich` through load and save**

In `db.load_sources()`, add the key to the appended dict:

```python
        resolved.append({
            "name": entry["name"],
            "root": root,
            "weight": float(entry.get("weight", 1.0)),
            "enrich": bool(entry.get("enrich", True)),
            "exclude": DEFAULT_EXCLUDES + list(entry.get("exclude", [])),
        })
```

In `db.save_sources()`, add it to the serialised dict:

```python
        {
            "name": s["name"],
            "root": str(s["root"]),
            "weight": s["weight"],
            "enrich": bool(s.get("enrich", True)),
            "exclude": [e for e in s.get("exclude", []) if e not in DEFAULT_EXCLUDES],
        }
```

- [ ] **Step 6: Persist the column and filter the queue**

In `server/company_brain/indexer.py`, replace `_upsert_source` with:

```python
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
```

Then in `_prune_and_enqueue`, replace the final `INSERT` with:

```python
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
```

- [ ] **Step 7: Run the new tests and the whole suite**

```bash
uv run --project server --group dev pytest server/tests/test_sources_enrich.py -q
uv run --project server --group dev pytest server/tests/ -q
```

Expected: all pass, including the 14 pre-existing tests.

- [ ] **Step 8: Commit**

```bash
git add server/company_brain/db.py server/company_brain/indexer.py server/tests/test_sources_enrich.py
git commit -m "feat: let a source opt out of entity extraction"
```

---

### Task 2: Circleback transport and per-user config

**Files:**
- Create: `server/company_brain/meetings.py`
- Modify: `.claude-plugin/plugin.json` (`userConfig` block)
- Modify: `.mcp.json` (`env` block)
- Test: `server/tests/test_meetings_transport.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `meetings.API_URL: str`, `meetings.SETTINGS_URL: str`, `meetings.TRANSCRIPT_DIR: str`, `meetings.TRANSCRIPT_SOURCE: str`, `meetings.TRANSCRIPT_WEIGHT: float`
  - `meetings._api_key() -> str` — raises `ValueError` when unset
  - `meetings._call(tool: str, args: dict, opener=None) -> dict` — `opener(request)` returns a file-like with `.read()` and `.headers`

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_meetings_transport.py`:

```python
"""The Circleback transport: auth, JSON and SSE envelopes, error messages."""
import io
import json

import pytest

from company_brain import meetings


class FakeResponse(io.BytesIO):
    def __init__(self, body: str, content_type: str = "application/json"):
        super().__init__(body.encode())
        self.headers = {"Content-Type": content_type}


def _rpc(payload: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": payload})


def test_missing_api_key_names_the_settings_page(monkeypatch):
    monkeypatch.delenv("CIRCLEBACK_API_KEY", raising=False)
    with pytest.raises(ValueError) as excinfo:
        meetings._api_key()
    assert meetings.SETTINGS_URL in str(excinfo.value)


def test_call_sends_bearer_token_and_intent(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    captured = {}

    def opener(request, timeout=None):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data)
        return FakeResponse(_rpc({"structuredContent": {"meetings": []}}))

    meetings._call("SearchMeetings", {"startDate": "2026-08-01"}, opener=opener)

    assert captured["url"] == meetings.API_URL
    assert captured["auth"] == "Bearer cb_test"
    assert captured["body"]["method"] == "tools/call"
    assert captured["body"]["params"]["name"] == "SearchMeetings"
    assert captured["body"]["params"]["arguments"]["intent"]


def test_call_unwraps_structured_content(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    body = _rpc({"structuredContent": {"meetings": [{"id": "m1"}]}})
    result = meetings._call("SearchMeetings", {}, opener=lambda r, timeout=None: FakeResponse(body))
    assert result == {"meetings": [{"id": "m1"}]}


def test_call_unwraps_json_inside_text_content(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    inner = json.dumps({"meetings": [{"id": "m2"}]})
    body = _rpc({"content": [{"type": "text", "text": inner}]})
    result = meetings._call("SearchMeetings", {}, opener=lambda r, timeout=None: FakeResponse(body))
    assert result == {"meetings": [{"id": "m2"}]}


def test_call_parses_a_server_sent_events_response(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    body = "event: message\ndata: %s\n\n" % _rpc({"structuredContent": {"meetings": []}})
    result = meetings._call(
        "SearchMeetings", {},
        opener=lambda r, timeout=None: FakeResponse(body, "text/event-stream"))
    assert result == {"meetings": []}


def test_call_raises_the_json_rpc_error_message(monkeypatch):
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "error": {"code": -32000, "message": "Permission denied."}})
    with pytest.raises(ValueError) as excinfo:
        meetings._call("SearchMeetings", {},
                       opener=lambda r, timeout=None: FakeResponse(body))
    assert "Permission denied." in str(excinfo.value)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_transport.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'company_brain.meetings'`.

- [ ] **Step 3: Write the module**

Create `server/company_brain/meetings.py`:

```python
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
import urllib.request

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
    if "text/event-stream" in content_type:
        for line in payload.decode().splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ValueError("Circleback returned an event stream with no data frame.")
    return json.loads(payload.decode())


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
    with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT_SECONDS) as response:
        envelope = _body(response.read(), response.headers.get("Content-Type", ""))
    if envelope.get("error"):
        raise ValueError(envelope["error"].get("message", "Circleback rejected the request."))
    return _unwrap(envelope.get("result") or {})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_transport.py -q
```

Expected: 6 passed.

- [ ] **Step 5: Expose the key as per-user config**

In `.claude-plugin/plugin.json`, add to the `userConfig` object (alongside `embedding_model`, `enrich_batch_size`, `view_port`):

```json
    "circleback_api_key": {
      "type": "string",
      "title": "Circleback API key",
      "description": "Create one at https://circleback.ai/settings?tab=api-access. Required only for /brain-meetings; the rest of the brain works without it.",
      "default": ""
    }
```

In `.mcp.json`, add to the `env` object:

```json
        "CIRCLEBACK_API_KEY": "${user_config.circleback_api_key}"
```

- [ ] **Step 6: Commit**

```bash
git add server/company_brain/meetings.py server/tests/test_meetings_transport.py .claude-plugin/plugin.json .mcp.json
git commit -m "feat: Circleback JSON-RPC transport and API key config"
```

---

### Task 3: The meeting queue and listing

**Files:**
- Modify: `server/company_brain/db.py` (add `meeting_queue` to `SCHEMA`)
- Modify: `server/company_brain/meetings.py`
- Test: `server/tests/test_meetings_list.py`

**Interfaces:**
- Consumes: `meetings._call`, `meetings.API_URL` (Task 2).
- Produces: `meetings.list_meetings(conn, days: int = 30, query: str | None = None, opener=None) -> list[dict]` returning dicts with keys `id`, `title`, `date`, `duration`, `attendees` (list of str), `tags` (list of str), `synced` (bool). Table `meeting_queue`.

> Named `list_meetings`, not `list` — a module-level `list` would shadow the builtin inside its own module.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_meetings_list.py`:

```python
"""Listing shows what Circleback has and marks what the brain already holds."""
from company_brain import db, meetings

PAYLOAD = {"meetings": [
    {"id": "m1", "name": "Pricing review", "createdAt": "2026-08-20T10:00:00Z",
     "duration": 1800, "tags": ["commercial"],
     "attendees": [{"name": "Joan", "email": "joan@example.com"}]},
    {"id": "m2", "name": "Standup", "createdAt": "2026-08-21T09:00:00Z",
     "duration": 600, "tags": [], "attendees": []},
]}


def test_list_maps_circleback_fields(empty_conn, monkeypatch):
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: PAYLOAD)
    rows = meetings.list_meetings(empty_conn)

    assert [r["id"] for r in rows] == ["m1", "m2"]
    assert rows[0]["title"] == "Pricing review"
    assert rows[0]["date"] == "2026-08-20"
    assert rows[0]["attendees"] == ["Joan"]
    assert rows[0]["tags"] == ["commercial"]
    assert rows[0]["synced"] is False


def test_list_marks_meetings_already_in_the_queue(empty_conn, monkeypatch):
    empty_conn.execute(
        "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
        "VALUES('m1', 'Pricing review', '2026-08-20', 'meeting-transcripts/a.md', 'done', '2026-08-20')")
    empty_conn.commit()
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: PAYLOAD)

    rows = {r["id"]: r["synced"] for r in meetings.list_meetings(empty_conn)}
    assert rows == {"m1": True, "m2": False}


def test_list_passes_a_start_date_and_search_term(empty_conn, monkeypatch):
    seen = {}

    def fake_call(tool, args, opener=None):
        seen["tool"] = tool
        seen["args"] = args
        return {"meetings": []}

    monkeypatch.setattr(meetings, "_call", fake_call)
    meetings.list_meetings(empty_conn, days=7, query="pricing")

    assert seen["tool"] == "SearchMeetings"
    assert seen["args"]["searchTerm"] == "pricing"
    assert "startDate" in seen["args"]


def test_list_tolerates_a_meeting_with_no_name(empty_conn, monkeypatch):
    monkeypatch.setattr(meetings, "_call",
                        lambda tool, args, opener=None: {"meetings": [{"id": "m3"}]})
    rows = meetings.list_meetings(empty_conn)
    assert rows[0]["title"] == "Untitled meeting"
    assert rows[0]["date"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_list.py -q
```

Expected: FAIL — `sqlite3.OperationalError: no such table: meeting_queue` and `AttributeError: module 'company_brain.meetings' has no attribute 'list_meetings'`.

- [ ] **Step 3: Add the table**

In `server/company_brain/db.py`, append to the `SCHEMA` string:

```sql
CREATE TABLE IF NOT EXISTS meeting_queue (
    meeting_id TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    held_at    TEXT,
    rel_path   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meeting_status ON meeting_queue(status);
```

- [ ] **Step 4: Implement the listing**

Add to `server/company_brain/meetings.py` — extend the imports at the top to `from datetime import datetime, timedelta, timezone` and add `import sqlite3`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_list.py -q
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add server/company_brain/db.py server/company_brain/meetings.py server/tests/test_meetings_list.py
git commit -m "feat: list Circleback meetings with already-synced marked"
```

---

### Task 4: Source registration

Two writes, not one. The transcripts directory sits inside the anchor source root (`.`), so without the exclude the anchor indexes every transcript a second time — at weight 1.0, with enrichment on — silently undoing Task 1.

**Files:**
- Modify: `server/company_brain/meetings.py`
- Test: `server/tests/test_meetings_registration.py`

**Interfaces:**
- Consumes: `db.load_sources`, `db.save_sources` (Task 1), `meetings.TRANSCRIPT_DIR`, `TRANSCRIPT_SOURCE`, `TRANSCRIPT_WEIGHT` (Task 2).
- Produces: `meetings.ensure_sources() -> dict` with keys `registered: bool`, `excluded: bool` (each `True` when this call made the change).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_meetings_registration.py`:

```python
"""Registering the transcripts source, and keeping the anchor out of it."""
import json

from company_brain import db, indexer, meetings


def test_registration_adds_source_and_anchor_exclude(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    result = meetings.ensure_sources()

    assert result == {"registered": True, "excluded": True}
    config = json.loads(db.sources_path().read_text())["sources"]
    transcripts = [s for s in config if s["name"] == meetings.TRANSCRIPT_SOURCE]
    assert len(transcripts) == 1
    assert transcripts[0]["enrich"] is False
    assert transcripts[0]["weight"] == meetings.TRANSCRIPT_WEIGHT
    assert meetings.TRANSCRIPT_DIR in config[0]["exclude"]


def test_registration_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    meetings.ensure_sources()
    assert meetings.ensure_sources() == {"registered": False, "excluded": False}

    config = json.loads(db.sources_path().read_text())["sources"]
    assert [s["name"] for s in config].count(meetings.TRANSCRIPT_SOURCE) == 1
    assert config[0]["exclude"].count(meetings.TRANSCRIPT_DIR) == 1


def test_transcripts_are_indexed_once_not_twice(tmp_path, monkeypatch):
    """The double-index regression. A two-segment exclude would fail this."""
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    meetings.ensure_sources()

    folder = tmp_path / meetings.TRANSCRIPT_DIR
    folder.mkdir(exist_ok=True)
    (folder / "2026-08-20-pricing.md").write_text("# Pricing\n\n## Transcript\n\nJoan: hello.\n")

    conn = db.connect()
    indexer.refresh(conn)
    owners = [r[0] for r in conn.execute(
        "SELECT s.name FROM documents d JOIN sources s ON s.id = d.source_id "
        "WHERE d.rel_path LIKE '%pricing%'")]
    assert owners == [meetings.TRANSCRIPT_SOURCE]
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_registration.py -q
```

Expected: FAIL with `AttributeError: module 'company_brain.meetings' has no attribute 'ensure_sources'`.

- [ ] **Step 3: Implement registration**

Add to `server/company_brain/meetings.py` (add `from . import db` to the imports):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_registration.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Run the whole suite**

```bash
uv run --project server --group dev pytest server/tests/ -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/company_brain/meetings.py server/tests/test_meetings_registration.py
git commit -m "feat: register the transcripts source without double-indexing it"
```

---

### Task 5: Syncing chosen transcripts

**Files:**
- Modify: `server/company_brain/meetings.py`
- Test: `server/tests/test_meetings_sync.py`

**Interfaces:**
- Consumes: `meetings._call`, `meetings.ensure_sources`, `ingest.slug`.
- Produces: `meetings.sync(conn, meeting_ids: list[str], opener=None) -> dict` with keys `synced` (list of ids), `skipped` (list of ids already held), `paths` (list of rel_path str). Writes `meeting_queue` rows with `status='pending'`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_meetings_sync.py`:

```python
"""Fetching chosen transcripts, writing them, and queueing them for summarising."""
from company_brain import db, meetings

TRANSCRIPTS = {"meetings": [{
    "id": "m1",
    "name": "Pricing review",
    "createdAt": "2026-08-20T10:00:00Z",
    "url": "https://circleback.ai/meetings/m1",
    "attendees": [{"name": "Joan"}, {"name": "David"}],
    "transcript": [
        {"speaker": "Joan", "text": "We should raise the floor price.", "timestamp": 12},
        {"speaker": "David", "text": "Agreed, from March.", "timestamp": 20},
    ],
}]}


def _conn(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    return db.connect()


def test_sync_writes_a_transcript_and_queues_it(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)

    result = meetings.sync(conn, ["m1"])

    assert result["synced"] == ["m1"]
    assert result["skipped"] == []
    written = tmp_path / result["paths"][0]
    assert written.exists()

    text = written.read_text()
    assert "type: transcript" in text
    assert "date: 2026-08-20" in text
    assert "Joan: We should raise the floor price." in text
    assert "https://circleback.ai/meetings/m1" in text

    row = conn.execute("SELECT * FROM meeting_queue WHERE meeting_id = 'm1'").fetchone()
    assert row["status"] == "pending"
    assert row["title"] == "Pricing review"
    conn.close()


def test_sync_names_the_file_by_date_and_title(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)
    result = meetings.sync(conn, ["m1"])
    assert result["paths"][0] == "meeting-transcripts/2026-08-20-pricing-review.md"
    conn.close()


def test_resyncing_a_held_meeting_is_a_no_op(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)
    meetings.sync(conn, ["m1"])

    second = meetings.sync(conn, ["m1"])
    assert second["synced"] == []
    assert second["skipped"] == ["m1"]

    folder = tmp_path / meetings.TRANSCRIPT_DIR
    assert len(list(folder.glob("*.md"))) == 1
    assert conn.execute("SELECT COUNT(*) FROM meeting_queue").fetchone()[0] == 1
    conn.close()


def test_sync_requests_only_the_chosen_ids(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    seen = {}

    def fake_call(tool, args, opener=None):
        seen["tool"] = tool
        seen["ids"] = args["meetingIds"]
        return TRANSCRIPTS

    monkeypatch.setattr(meetings, "_call", fake_call)
    meetings.sync(conn, ["m1"])

    assert seen["tool"] == "GetTranscriptsForMeetings"
    assert seen["ids"] == ["m1"]
    conn.close()


def test_sync_of_nothing_touches_nothing(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call",
                        lambda tool, args, opener=None: _must_not_call())
    result = meetings.sync(conn, [])
    assert result == {"synced": [], "skipped": [], "paths": []}
    conn.close()


def _must_not_call():
    raise AssertionError("_call must not run when no meetings are chosen")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_sync.py -q
```

Expected: FAIL with `AttributeError: module 'company_brain.meetings' has no attribute 'sync'`.

- [ ] **Step 3: Implement transcript rendering and sync**

Add to `server/company_brain/meetings.py` (add `from .ingest import slug` to the imports):

```python
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


def sync(conn: sqlite3.Connection, meeting_ids: list[str], opener=None) -> dict:
    """Fetch and store only the meetings the user chose."""
    result: dict = {"synced": [], "skipped": [], "paths": []}
    held = {r["meeting_id"] for r in conn.execute("SELECT meeting_id FROM meeting_queue")}
    wanted = [mid for mid in meeting_ids if mid not in held]
    result["skipped"] = [mid for mid in meeting_ids if mid in held]
    if not wanted:
        return result

    ensure_sources()
    root = db.project_dir() / TRANSCRIPT_DIR
    root.mkdir(parents=True, exist_ok=True)
    payload = _call("GetTranscriptsForMeetings", {"meetingIds": wanted}, opener=opener)

    for meeting in payload.get("meetings") or []:
        meeting_id = meeting.get("id", "")
        if not meeting_id or meeting_id in held:
            continue
        title = (meeting.get("name") or "").strip() or "Untitled meeting"
        held_at = (meeting.get("createdAt") or "")[:10]
        name = "%s-%s.md" % (held_at or "undated", slug(title)[:70] or "meeting")
        if (root / name).exists():
            # Two meetings the same day with the same name are rare but real;
            # never let the second overwrite the first.
            name = name[:-3] + "-" + slug(meeting_id)[:8] + ".md"
        (root / name).write_text(_transcript_markdown(meeting), encoding="utf-8")
        rel_path = "%s/%s" % (TRANSCRIPT_DIR, name)
        conn.execute(
            "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
            "VALUES(?,?,?,?,'pending',?)",
            (meeting_id, title, held_at, rel_path, _now()))
        result["synced"].append(meeting_id)
        result["paths"].append(rel_path)
    conn.commit()
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_sync.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add server/company_brain/meetings.py server/tests/test_meetings_sync.py
git commit -m "feat: sync chosen Circleback transcripts into their own source"
```

---

### Task 6: The summarising queue

**Files:**
- Modify: `server/company_brain/meetings.py`
- Test: `server/tests/test_meetings_queue.py`

**Interfaces:**
- Consumes: `capture.add` (existing), `meeting_queue` (Task 3).
- Produces:
  - `meetings.LEASE_MINUTES: int = 30`
  - `meetings.reset_leases(conn) -> int`
  - `meetings.pull(conn, limit: int = 5) -> list[dict]` — dicts with `meeting_id`, `title`, `held_at`, `transcript` (full file text)
  - `meetings.push(conn, results: list[dict]) -> dict` — each result `{"meeting_id", "summary", "facts": [str], "related": [str]}`; returns `{"captured": int, "paths": [str], "skipped": [str], "pending": int}`

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_meetings_queue.py`:

```python
"""Leasing transcripts to subagents and capturing what they write back."""
from datetime import datetime, timedelta, timezone

from company_brain import db, meetings


def _seed(tmp_path, monkeypatch, status="pending"):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    conn = db.connect()
    meetings.ensure_sources()
    folder = tmp_path / meetings.TRANSCRIPT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-08-20-pricing.md").write_text("# Pricing\n\n## Transcript\n\nJoan: raise it.\n")
    conn.execute(
        "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
        "VALUES('m1','Pricing review','2026-08-20','meeting-transcripts/2026-08-20-pricing.md',?,?)",
        (status, meetings._now()))
    conn.commit()
    return conn


def test_pull_leases_and_returns_the_transcript_text(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    batch = meetings.pull(conn)

    assert len(batch) == 1
    assert batch[0]["meeting_id"] == "m1"
    assert batch[0]["title"] == "Pricing review"
    assert "Joan: raise it." in batch[0]["transcript"]
    assert conn.execute(
        "SELECT status FROM meeting_queue WHERE meeting_id='m1'").fetchone()[0] == "leased"
    conn.close()


def test_pull_does_not_return_an_already_leased_meeting(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch, status="leased")
    assert meetings.pull(conn) == []
    conn.close()


def test_expired_leases_return_to_pending(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch, status="leased")
    stale = (datetime.now(timezone.utc)
             - timedelta(minutes=meetings.LEASE_MINUTES + 1)).isoformat(timespec="seconds")
    conn.execute("UPDATE meeting_queue SET updated_at = ? WHERE meeting_id='m1'", (stale,))
    conn.commit()

    assert meetings.reset_leases(conn) == 1
    assert len(meetings.pull(conn)) == 1
    conn.close()


def test_push_captures_the_summary_and_closes_the_row(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    meetings.pull(conn)

    stats = meetings.push(conn, [{
        "meeting_id": "m1",
        "summary": "The team agreed to raise the floor price from March.",
        "facts": ["action:: David to publish the new price list"],
        "related": ["pricing"],
    }])

    assert stats["captured"] == 1
    assert stats["pending"] == 0
    note = (tmp_path / stats["paths"][0]).read_text()
    assert "raise the floor price" in note
    assert "type: meeting" in note
    assert "action:: David to publish the new price list" in note
    assert conn.execute(
        "SELECT status FROM meeting_queue WHERE meeting_id='m1'").fetchone()[0] == "done"
    conn.close()


def test_push_skips_an_unknown_meeting_id(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    stats = meetings.push(conn, [{"meeting_id": "nope", "summary": "x", "facts": [], "related": []}])
    assert stats["captured"] == 0
    assert stats["skipped"] == ["nope"]
    conn.close()


def test_push_leaves_the_row_pending_when_the_summary_is_empty(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    meetings.pull(conn)
    stats = meetings.push(conn, [{"meeting_id": "m1", "summary": "   ", "facts": [], "related": []}])

    assert stats["captured"] == 0
    assert stats["skipped"] == ["m1"]
    assert conn.execute(
        "SELECT status FROM meeting_queue WHERE meeting_id='m1'").fetchone()[0] == "leased"
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_queue.py -q
```

Expected: FAIL with `AttributeError: module 'company_brain.meetings' has no attribute 'pull'`.

- [ ] **Step 3: Implement the queue**

Add to `server/company_brain/meetings.py` (add `from . import capture` to the imports):

```python
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
    meeting and write two summaries of it.
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
        batch.append({
            "meeting_id": row["meeting_id"],
            "title": row["title"],
            "held_at": row["held_at"],
            "transcript": path.read_text(encoding="utf-8") if path.exists() else "",
        })
    return batch


def push(conn: sqlite3.Connection, results: list[dict]) -> dict:
    """Capture each summary as a note and close its queue row."""
    stats: dict = {"captured": 0, "paths": [], "skipped": []}
    known = {r["meeting_id"]: r for r in conn.execute(
        "SELECT meeting_id, title, held_at FROM meeting_queue")}

    for result in results:
        meeting_id = result.get("meeting_id")
        summary = (result.get("summary") or "").strip()
        if meeting_id not in known or not summary:
            stats["skipped"].append(meeting_id)
            continue
        row = known[meeting_id]
        written = capture.add(
            conn, content=summary,
            title="%s — %s" % (row["title"], row["held_at"] or "meeting"),
            kind="meeting", tags=["meeting", "circleback"],
            related=list(result.get("related") or []),
            facts=list(result.get("facts") or []),
            confidence="observed")
        conn.execute("UPDATE meeting_queue SET status = 'done', updated_at = ? "
                     "WHERE meeting_id = ?", (_now(), meeting_id))
        stats["captured"] += 1
        stats["paths"].append(written["relative"])
    conn.commit()
    stats["pending"] = conn.execute(
        "SELECT COUNT(*) FROM meeting_queue WHERE status = 'pending'").fetchone()[0]
    return stats
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_queue.py -q
```

Expected: 6 passed.

- [ ] **Step 5: Run the whole suite**

```bash
uv run --project server --group dev pytest server/tests/ -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add server/company_brain/meetings.py server/tests/test_meetings_queue.py
git commit -m "feat: lease transcripts to subagents and capture their summaries"
```

---

### Task 7: MCP tools

**Files:**
- Modify: `server/company_brain/mcp_server.py` (import list line 17; new tools after `enrich_push`; `status` tool)
- Modify: `server/company_brain/indexer.py` (`status()` around line 150)
- Test: `server/tests/test_meetings_status.py`

**Interfaces:**
- Consumes: everything from Tasks 3-6.
- Produces: MCP tools `meetings_list`, `meetings_sync`, `meetings_pull`, `meetings_push`. `indexer.status()` gains key `meetings_pending`.

- [ ] **Step 1: Write the failing test**

Create `server/tests/test_meetings_status.py`:

```python
"""Pending meetings surface in status alongside pending enrichment."""
from company_brain import db, indexer, meetings


def test_status_counts_pending_meetings(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    conn = db.connect()
    conn.execute(
        "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
        "VALUES('m1','A','2026-08-20','meeting-transcripts/a.md','pending',?)", (meetings._now(),))
    conn.execute(
        "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
        "VALUES('m2','B','2026-08-21','meeting-transcripts/b.md','done',?)", (meetings._now(),))
    conn.commit()

    assert indexer.status(conn)["meetings_pending"] == 1
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_status.py -q
```

Expected: FAIL with `KeyError: 'meetings_pending'`.

- [ ] **Step 3: Add the count to status**

In `server/company_brain/indexer.py`, inside the dict returned by `status()`, next to the existing `enrich_pending` line, add:

```python
        "meetings_pending": one("SELECT COUNT(*) FROM meeting_queue WHERE status = 'pending'"),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run --project server --group dev pytest server/tests/test_meetings_status.py -q
```

Expected: PASS.

- [ ] **Step 5: Add the MCP tools**

In `server/company_brain/mcp_server.py`, add `meetings` to the existing `from . import ...` line, then add after the `enrich_push` tool:

```python
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
```

- [ ] **Step 6: Verify the server still imports and the suite passes**

```bash
uv run --project server python -c "from company_brain import mcp_server; print('ok')"
uv run --project server --group dev pytest server/tests/ -q
```

Expected: `ok`, then all tests pass.

- [ ] **Step 7: Commit**

```bash
git add server/company_brain/mcp_server.py server/company_brain/indexer.py server/tests/test_meetings_status.py
git commit -m "feat: expose meetings tools over MCP"
```

---

### Task 8: Command and skill

**Files:**
- Create: `commands/brain-meetings.md`
- Create: `skills/brain-meetings/SKILL.md`
- Modify: `hooks/staleness.sh`

**Interfaces:**
- Consumes: MCP tools from Task 7.
- Produces: the `/brain-meetings` user flow. No Python.

- [ ] **Step 1: Write the command**

Create `commands/brain-meetings.md`:

```markdown
---
description: List your Circleback meetings and sync the ones you choose into the brain
argument-hint: "[days] [search term]"
---

Bring meetings into the company brain: **$ARGUMENTS**

Use the `brain-meetings` skill. Nothing syncs without the user choosing it.

Arguments, all optional: a number sets the date window in days (default 30),
any remaining words are a search term.

If `CIRCLEBACK_API_KEY` is not configured, say so and point at
https://circleback.ai/settings?tab=api-access — do not attempt the sync.
```

- [ ] **Step 2: Write the skill**

Create `skills/brain-meetings/SKILL.md`:

```markdown
---
name: brain-meetings
description: List Circleback meetings, sync the ones the user picks, and write comprehensive summaries into the company brain. Use when the user runs /brain-meetings, asks to pull in meeting notes or transcripts, or when brain status reports outstanding meetings_pending work.
---

# Bringing meetings into the brain

Invoked as `/brain-meetings`. Two phases: choose, then summarise.

Nothing is ever synced automatically. The user's selection is the only thing
that puts a meeting in the brain, because transcripts are searchable and this
brain is usually a git repository.

## Phase 1 — choose

1. Call `meetings_list` with `days` (default 30) and any search term the user
   gave. If `count` is 0, say so and stop.
2. Print a numbered table: number, date, title, duration in minutes, attendees,
   tags. Mark meetings whose `synced` is true as already held and do not offer
   them.
3. Ask which to sync. Accept `1,3,7-9`, `all`, `none`, or a name or date
   fragment. Use a printed table and a free-text reply rather than
   AskUserQuestion — a 30-day window routinely exceeds its four-option limit.
4. Resolve the selection to ids and read them back to the user before syncing.
   `none` is a valid answer: confirm and stop, leaving no trace.
5. Call `meetings_sync` with the chosen ids as a JSON array.
6. Call `reindex` so the transcripts become searchable.

## Phase 2 — summarise

Repeat until `pending` reaches 0.

1. Call `meetings_pull` (default limit 5). If `count` is 0, phase 2 is done.
2. Dispatch one subagent per meeting **in a single message** so they run
   concurrently. Give each the full transcript and this contract:

   > You are given one meeting transcript. Write a comprehensive summary of what
   > actually happened in it. Return ONLY a JSON object:
   > `{"meeting_id": "<copied exactly>",
   >   "summary": "<250-500 words in markdown: what was discussed, what was
   >     decided and by whom, what was disagreed on, what is still open. Use
   >     headings if it helps. Ground every claim in the transcript.>",
   >   "facts": ["action:: <who> to <what>, by <when if stated>"],
   >   "related": ["<entity named in the meeting>"]}`
   >
   > Rules: every action item goes in `facts` as `action:: ...`, one per commitment
   > actually made — not implied. Use `decision:: ...` for decisions reached.
   > Name people as the transcript names them. Do not invent attendees, dates or
   > commitments, and do not bring in knowledge from outside the transcript. A
   > meeting with no actions returns an empty `facts` array — that is a valid
   > answer.

3. Validate: it must parse as JSON and every `meeting_id` must be one you handed
   out. Drop anything that fails rather than pushing it.
4. Call `meetings_push` with the concatenated array as a JSON string.
5. **Show the user each summary** as well as storing it, then report the file
   paths and remaining `pending`.
6. Call `reindex` once the queue is empty so the new notes are searchable.

## Notes

Circleback's own AI notes and action items are deliberately not used — the
summary is the brain's own reading of the transcript.

Transcripts live in `meeting-transcripts/` as their own low-weight source that is
never entity-extracted, so they are searchable verbatim without distorting the
graph. The summaries are ordinary captured notes and carry full weight.

Re-running is safe: a meeting already held is skipped, and an interrupted
summarising run returns its leases to the queue after 30 minutes.
```

- [ ] **Step 3: Add the pending count to the session hook**

In `hooks/staleness.sh`, insert before the final `exit 0`:

```bash
# Meetings fetched but not yet summarised. Guarded: databases predating this
# feature have no meeting_queue, and this script reports rather than acts.
meetings=$(sqlite3 "$STATE/brain.db" \
  "SELECT COUNT(*) FROM meeting_queue WHERE status = 'pending';" 2>/dev/null || echo 0)
if [ "${meetings:-0}" -gt 0 ]; then
  echo "company-brain: ${meetings} synced meeting(s) awaiting a summary — run /brain-meetings"
fi
```

- [ ] **Step 4: Verify the hook is still valid shell and safe on an old database**

```bash
bash -n hooks/staleness.sh && echo "syntax ok"
CLAUDE_PROJECT_DIR=$(mktemp -d) bash hooks/staleness.sh && echo "clean exit with no brain"
```

Expected: `syntax ok`, then `clean exit with no brain` with no error output.

- [ ] **Step 5: Commit**

```bash
git add commands/brain-meetings.md skills/brain-meetings/SKILL.md hooks/staleness.sh
git commit -m "feat: /brain-meetings command and skill"
```

---

### Task 9: Documentation and release

**Files:**
- Modify: `README.md` (repo root and `plugins/company-brain/README.md`)
- Modify: `plugins/company-brain/GUIDE.md`
- Modify: `CHANGELOG.md`
- Modify: `.claude-plugin/marketplace.json` (root, `version`)
- Modify: `plugins/company-brain/.claude-plugin/plugin.json` (`version`)

**Interfaces:**
- Consumes: the finished feature.
- Produces: version `0.4.0` in both manifests. They must match or the plugin will not update cleanly.

- [ ] **Step 1: Bump both versions to 0.4.0**

```bash
cd ~/.claude/plugins/marketplaces/company-brain
sed -i '' 's/"version": "0.3.0"/"version": "0.4.0"/' .claude-plugin/marketplace.json
sed -i '' 's/"version": "0.3.0"/"version": "0.4.0"/' plugins/company-brain/.claude-plugin/plugin.json
grep -h '"version"' .claude-plugin/marketplace.json plugins/company-brain/.claude-plugin/plugin.json
```

Expected: both print `"version": "0.4.0"`.

- [ ] **Step 2: Add the changelog entry**

At the top of `CHANGELOG.md`, under a new `## 0.4.0` heading:

```markdown
## 0.4.0

- `/brain-meetings` lists your Circleback meetings and syncs the ones you pick.
  Nothing syncs automatically.
- Transcripts land in `meeting-transcripts/` as their own source: searchable
  verbatim, weighted below curated material, and never entity-extracted, so
  they cannot distort the knowledge graph.
- Summaries are written by subagents in your session and captured as ordinary
  notes, so they carry full weight and full graph links.
- Sources can now set `"enrich": false` to stay searchable without being
  entity-extracted.
- Set your key under Circleback API key in the plugin's settings.
```

- [ ] **Step 3: Document the command**

Add this line to the command list in both `README.md` files and in `GUIDE.md`,
matching each file's existing one-line-per-command format:

```markdown
- `/brain-meetings` — list your Circleback meetings and sync the ones you pick.
  Needs a Circleback API key in the plugin's settings. Transcripts become
  searchable but never enter the knowledge graph; the summaries do.
```

In `GUIDE.md`, also add a short paragraph after that line explaining that
nothing syncs automatically and that the summary is written by subagents in the
session rather than taken from Circleback's own notes.

- [ ] **Step 4: Run the whole suite one last time**

```bash
cd plugins/company-brain
uv run --project server --group dev pytest server/tests/ -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: document /brain-meetings and release 0.4.0"
```

- [ ] **Step 6: Stop and hand back**

Do **not** push and do **not** merge. The repo is public and `main` already has
four unpushed commits; publishing is the user's decision. Report: tasks completed,
test counts, and that `feat/meeting-sync` is ready for review.
