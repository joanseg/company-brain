# Meeting sync — Circleback into the company brain

**Date:** 2026-09-01
**Repo:** `joanseg/company-brain` (public)
**Branch:** `feat/meeting-sync` off `main`
**Ships as:** plugin version bump; users receive it via `/plugin update`

## Problem

Meetings are where most decisions are actually made, and none of them reach the
brain. Today knowledge only arrives by hand, through `/brain-add`.

The ask was "a webhook URL I can paste into my meeting tool". Circleback supports
that, but a webhook needs a public, always-on endpoint, and this plugin's only
server (`webui.py`) is loopback-pinned and dies with the Claude Code session.
Every webhook design therefore requires each user to own infrastructure.

Circleback also exposes the same data by pull, which needs none. That is what
this spec builds.

## Decisions

### D1 — Transport: direct JSON-RPC, not the npm CLI

`@circleback/cli@0.3.1` is a thin client: it POSTs `tools/call` to
`https://circleback.ai/api/mcp` with `Authorization: Bearer $CIRCLEBACK_API_KEY`
and calls `SearchMeetings` and `GetTranscriptsForMeetings`. Nothing more.

company-brain is a Python plugin. Depending on the CLI would make every user of a
public plugin run `npm install -g @circleback/cli` before they could sync. The two
calls are ~30 lines of stdlib `urllib.request`, so the plugin calls the endpoint
itself and adds no dependency to `server/pyproject.toml`.

This deviates from the CLI route approved during brainstorming. It is the same
route — pull, API key, same endpoint, same tools — minus the Node hop. The
adapter is isolated behind one function so swapping to `subprocess` calling
`cb --json` is a contained change if preferred.

### D2 — Pull, not webhook

Rejected: a plugin-hosted receiver behind a tunnel (URL dies with the session,
which is exactly when meetings end) and a per-user hosted relay (every user of a
public plugin deploying and maintaining a service). Pull never misses a meeting,
holds no secret in transit, verifies no signature, and backfills history a webhook
could never supply.

Cost, stated plainly: not real-time. A meeting lands next time the user runs
`/brain-meetings` or opens a session.

### D3 — Transcripts are indexed and searchable

Chosen by the user against a recommendation to keep them out of the index. The
risk is real: `indexer._prune_and_enqueue()` enqueues every chunk for entity
extraction, so transcripts would generate `mentions` rows and become graph hubs,
distorting the personalised-PageRank rerank that `search_evidence` depends on.

Three mitigations, in descending order of effect:

1. Transcripts live in their own source with `enrich: false`, so they are never
   entity-extracted and cannot become graph hubs. This requires the one change to
   existing code (see D3a) and is the mitigation that matters.
2. `search.MAX_PER_DOC = 2` already caps any single transcript at two chunks per
   result set. No change needed.
3. Source `weight: 0.4`. Weak on its own — `search.W_SOURCE` is only `0.10` of the
   score — but free.

Net effect: transcripts are fully lexical- and dense-searchable verbatim, and
contribute nothing to the graph.

### D3a — `enrich` flag on a source

`sources` gains `enrich INTEGER NOT NULL DEFAULT 1`, added via the existing
`db._migrate()` additive-column mechanism so existing databases keep working
unchanged. `sources.json` gains an optional `"enrich": false`.

`indexer._prune_and_enqueue()` changes from:

```sql
INSERT OR IGNORE INTO enrich_queue(sha, status, updated_at)
SELECT DISTINCT sha, 'pending', ? FROM chunks
```

to a join that filters on `s.enrich = 1`. This is the only edit to existing
behaviour in the whole feature.

### D4 — The summary is written by Claude, not by Python

The user's requirement: the transcript is the raw material; the brain reads it,
decides what the actions are, and writes back a comprehensive summary.

`enrich.py` already establishes this pattern and it is reused rather than
reinvented: a leased queue, `pull` hands work to subagents in the current session,
`push` writes the result back. No API spend, resumable, idempotent.

Circleback's own `notes` and `actionItems` fields are deliberately **not** used.
The user asked for the brain's interpretation, not the vendor's.

### D5 — Per-user config via `userConfig`

`plugin.json` already has a `userConfig` block wired into `.mcp.json` through
`${user_config.*}`. That is the plugin's existing per-user configuration surface
and is what "set up by each user" means here. No new mechanism.

### D6 — Opt-in by tag, defaulting to nothing

`meeting_tags` defaults to empty, and empty means sync nothing. A public plugin
that silently pulls every meeting a user has ever had into a git-committed
repository on first run is not an acceptable default. The user opts in per tag.

## Architecture

```
Circleback /api/mcp
        │  SearchMeetings + GetTranscriptsForMeetings
        ▼
   meetings.sync()
        │
        ├──► meeting-transcripts/*.md     own source, weight 0.4, enrich false
        │            └──► indexed: FTS5 + dense, searchable verbatim, never a graph hub
        │
        └──► meeting_queue (pending)
                     │
              /brain-meetings
                     │  meetings.pull() leases a batch
                     ▼
              subagent reads one transcript
                     │  returns summary + action items + related entities
                     ▼
              meetings.push() ──► capture.add() ──► memory/inbox/*.md
                                        weight 1.0, enriched, graph-linked
```

Transcript stays raw. Summary is derived knowledge and enters through the same
door as `/brain-add`, so it is indistinguishable downstream from a hand-written
note.

## Components

### `server/company_brain/meetings.py` (new)

- `fetch(since: str | None, tags: list[str]) -> list[dict]` — the only code that
  knows Circleback exists. POSTs JSON-RPC `tools/call` to
  `https://circleback.ai/api/mcp`. Handles both `application/json` and
  `text/event-stream` responses, because the endpoint may return either. Reads
  `CIRCLEBACK_API_KEY`; raises a message naming
  `https://circleback.ai/settings?tab=api-access` when unset.
- `sync(conn) -> dict` — calls `fetch`, writes each transcript to the transcripts
  source, inserts a `meeting_queue` row, advances the cursor. The cursor advances
  only after a transcript file is written, so a crash re-pulls rather than skips.
- `pull(conn, limit) -> list[dict]` — leases pending meetings inside a
  `BEGIN IMMEDIATE` with a 30-minute expiry, mirroring `enrich.pull` exactly.
- `push(conn, results) -> dict` — calls `capture.add(kind="meeting", ...)` for each
  summary, closes the queue row.

Cursor stored via `db.set_meta(conn, "circleback_cursor", iso8601)`.

### `db.py` changes

Beyond the schema below: `load_sources()` carries an `enrich` key (default `True`),
`save_sources()` persists it, and `_migrate()` adds the `sources.enrich` column.

### Schema (`db.py`)

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

New table, so `CREATE TABLE IF NOT EXISTS` is sufficient. The `sources.enrich`
column needs `_migrate()`.

### Source registration (one-time, idempotent)

The transcripts source root sits **inside** the anchor source root, which is `.`.
Left alone the anchor would index every transcript a second time — at weight 1.0
and with enrichment on — silently defeating D3a. So registration is two writes to
`sources.json`, not one, and `sync()` performs both before its first fetch:

1. Append the transcripts source: `{"name": "meeting-transcripts", "root":
   "meeting-transcripts", "weight": 0.4, "enrich": false}`.
2. Append `meeting-transcripts` to the **anchor** source's `exclude` list.

Both are idempotent and skipped when already present.

**Why a top-level single-segment directory, not `memory/transcripts`:**
`ingest.walk()` filters with `set(path.relative_to(root).parts[:-1]) & blocked` —
it matches individual path *segments*, never a multi-segment relative path. An
exclude of `"memory/transcripts"` would silently never match, and the anchor would
index every transcript anyway. `meeting-transcripts` is one segment, so it matches,
and it is distinctive enough that excluding it repo-wide costs nothing. Changing
`walk()` to understand path prefixes was rejected: it alters matching for every
existing source to serve one new one.

`db.save_sources()` must be extended to persist the `enrich` key, which it
currently drops, and `indexer._upsert_source()` must write the column on the
`INSERT` and the `ON CONFLICT` update, which it currently does not.

### Transcript file shape

`meeting-transcripts/YYYY-MM-DD-<slug>.md`, frontmatter carrying `type: transcript`,
`created`, `attendees`, and a `reference::` back to the Circleback meeting URL.
Written directly rather than through `capture.add()`, deliberately: `capture`
runs `_inferred_links()`, whose own comment warns against creating noisy hubs, and
a transcript mentions nearly every entity.

### MCP tools (`mcp_server.py`)

`meetings_sync`, `meetings_pull`, `meetings_push` — same shapes as the existing
`enrich_pull` / `enrich_push`. `status` gains `meetings_pending`.

### Skill and command

`skills/brain-meetings/SKILL.md` mirrors `brain-enrich`: pull a batch, one subagent
per meeting, each returns a comprehensive summary, action items as
`- action:: <text>` structured facts, and related entities. `push` captures them,
then reindex.

`commands/brain-meetings.md` is the entry point. It prints each summary back in
session as well as storing it.

### Config

`plugin.json` `userConfig` gains `circleback_api_key` and `meeting_tags`, wired
through `.mcp.json` as `CIRCLEBACK_API_KEY` and `COMPANY_BRAIN_MEETING_TAGS`.

### SessionStart hook

`hooks/staleness.sh` gains a pending-meeting count, guarded so a database
predating this feature reports nothing. Report, never act — matching the existing
behaviour of that script.

## Error handling

| Condition | Behaviour |
|---|---|
| `CIRCLEBACK_API_KEY` unset | Named error pointing at the settings URL. No partial write. |
| 401 / 403 | Surface Circleback's message; cursor unchanged. |
| 429 or network failure | Abort the sync, leave the cursor where it was, report how many meetings were written before the failure. |
| Subagent returns unusable JSON | That meeting's lease expires and returns to `pending`; others in the batch still commit. |
| Transcript write fails | Cursor not advanced; the meeting is re-pulled next run. |

## Testing

`server/tests/test_meetings.py`, following `conftest.py`'s temp-dir pattern:

- `fetch` parses both JSON and SSE response bodies, against captured fixtures.
- Transcript file shape: path, frontmatter, `reference::`.
- Queue transitions: `pending → leased → done`; expired leases return to `pending`.
- Cursor advances on success, holds on failure.
- **Regression that matters:** chunks belonging to an `enrich: false` source never
  appear in `enrich_queue` after `indexer.refresh()`.
- Existing databases migrate: a `sources` table without `enrich` gains it and
  defaults to enriched.
- Registration is idempotent: running `sync()` twice leaves exactly one
  transcripts source and one `meeting-transcripts` exclude entry.
- **The double-index regression:** after registration, a file under
  `meeting-transcripts/` produces documents for the `meeting-transcripts` source
  only, never for the anchor source. This is the test that would have caught the
  segment-matching bug in `ingest.walk()`.

No network in tests; the HTTP call is injected.

## Non-goals

- Webhooks, tunnels, relays, and any always-on listener.
- Circleback's `notes`, `actionItems` and `insights` fields — superseded by D4.
- Recording or audio. `recordingUrl` expires in 24 hours and is not stored.
- Meeting tools other than Circleback. The `fetch()` boundary is where a second
  adapter would attach; none is built.

## Open items for build time

1. Exact response envelope of `SearchMeetings` and `GetTranscriptsForMeetings`,
   confirmed against a live key. Field names in this spec follow Circleback's
   published webhook payload and must be verified, not assumed.
2. Whether notes the user writes themselves come back distinctly from Circleback's
   generated `notes`. If they do, they belong in the transcript file as raw
   material; if not, no loss.
