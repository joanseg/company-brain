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

Circleback also exposes the same data by pull, which needs none. That is what this
spec builds.

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
route — pull, API key, same endpoint, same tools — minus the Node hop. The adapter
is isolated behind two functions so swapping to `subprocess` calling `cb --json` is
a contained change if preferred.

### D2 — Pull, not webhook

Rejected: a plugin-hosted receiver behind a tunnel (URL dies with the session,
which is exactly when meetings end) and a per-user hosted relay (every user of a
public plugin deploying and maintaining a service). Pull never misses a meeting,
holds no secret in transit, verifies no signature, and backfills history a webhook
could never supply.

Cost, stated plainly: not real-time. Nothing enters the brain until the user asks
for it. Under D6 that is the intended behaviour rather than a limitation.

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

One key only: `circleback_api_key`. There is no tag filter, no sync window, and no
schedule to configure — D6 removes the need for all three.

### D6 — Nothing syncs without being chosen

`/brain-meetings` never syncs on its own. It lists what Circleback has and asks
which meetings to take. Selection is the whole control surface.

This replaces an earlier design where a configured tag list drove an automatic
sync. Explicit choice is better on three counts: a public plugin never pulls a
meeting the user did not ask for, there is no default that is wrong for somebody,
and the user sees what they are about to ingest before it lands in a
git-committed repository.

It also deletes machinery. With no automatic sync there is no watermark to
maintain: `meeting_queue` is keyed by `meeting_id`, so "already synced" is simply
whether a row exists. No cursor, no `circleback_cursor` meta key, no
advance-on-success/hold-on-failure logic, and no class of bug where a crash skips
a meeting forever.

## Architecture

```
                    /brain-meetings
                          │
                          ▼
              meetings.list(days, query)          SearchMeetings
                          │
                          ▼
        numbered table, already-synced rows marked
                          │
                    user picks: "1,3,7-9" / "all" / "none"
                          │
                          ▼
              meetings.sync(conn, ids)            GetTranscriptsForMeetings
                          │
        ├──► meeting-transcripts/*.md     own source, weight 0.4, enrich false
        │            └──► indexed: FTS5 + dense, searchable verbatim, never a graph hub
        │
        └──► meeting_queue (pending)
                     │
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

- `_call(tool, args) -> dict` — the only code that knows Circleback exists. POSTs
  JSON-RPC `tools/call` to `https://circleback.ai/api/mcp`. Handles both
  `application/json` and `text/event-stream` responses, because the endpoint may
  return either. Reads `CIRCLEBACK_API_KEY`; raises a message naming
  `https://circleback.ai/settings?tab=api-access` when unset.
- `list(conn, days=30, query=None) -> list[dict]` — `SearchMeetings` over a date
  window, returning id, title, date, duration, attendees, tags, and a `synced`
  flag set from `meeting_queue`. Read-only: writes nothing, so the user can browse
  freely.
- `sync(conn, meeting_ids) -> dict` — `GetTranscriptsForMeetings` for the chosen
  ids only. Writes each transcript to the transcripts source and inserts a
  `meeting_queue` row. Ids already present are skipped, so re-selecting a synced
  meeting is a no-op rather than a duplicate.
- `pull(conn, limit) -> list[dict]` — leases pending meetings inside a
  `BEGIN IMMEDIATE` with a 30-minute expiry, mirroring `enrich.pull` exactly.
- `push(conn, results) -> dict` — calls `capture.add(kind="meeting", ...)` for each
  summary, closes the queue row.

### `db.py` changes

`load_sources()` carries an `enrich` key (default `True`), `save_sources()`
persists it, and `_migrate()` adds the `sources.enrich` column.

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
`sources.json`, not one, and `sync()` performs both before its first write:

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
Written directly rather than through `capture.add()`, deliberately: `capture` runs
`_inferred_links()`, whose own comment warns against creating noisy hubs, and a
transcript mentions nearly every entity.

### MCP tools (`mcp_server.py`)

`meetings_list`, `meetings_sync`, `meetings_pull`, `meetings_push`. The last two
have the same shapes as the existing `enrich_pull` / `enrich_push`. `status` gains
`meetings_pending`.

### Skill and command

`commands/brain-meetings.md` drives the two-phase flow:

1. Call `meetings_list`. Print a numbered table — date, title, duration,
   attendees, tags — with already-synced rows marked and not offered again.
2. Ask which to sync. Accept `1,3,7-9`, `all`, `none`, or a date/name substring.
   A free-text numbered selection rather than `AskUserQuestion`, because a
   30-day window routinely exceeds that tool's four-option limit.
3. Call `meetings_sync` with the chosen ids.
4. Hand off to `skills/brain-meetings/SKILL.md`, which mirrors `brain-enrich`:
   pull a batch, one subagent per meeting, each returning a comprehensive summary,
   action items as `- action:: <text>` structured facts, and related entities.
   `push` captures them, then reindex.
5. Print each summary back in session as well as storing it.

Selecting nothing is a supported outcome and leaves no trace.

### SessionStart hook

`hooks/staleness.sh` gains a count of transcripts fetched but not yet summarised,
guarded so a database predating this feature reports nothing. Report, never act —
matching the existing behaviour of that script. It does **not** mention unsynced
Circleback meetings: that would require a network call in a session hook.

## Error handling

| Condition | Behaviour |
|---|---|
| `CIRCLEBACK_API_KEY` unset | Named error pointing at the settings URL. `list` and `sync` both refuse; nothing partial. |
| 401 / 403 | Surface Circleback's message unchanged. |
| 429 or network failure during `list` | Report and stop. Nothing was written, so there is nothing to unwind. |
| 429 or network failure during `sync` | Meetings already written keep their queue rows and stay valid; report which ids did not make it so the user can re-select them. |
| Meeting id already in `meeting_queue` | Skipped silently; re-selecting is a no-op, never a duplicate file. |
| Subagent returns unusable JSON | That meeting's lease expires and returns to `pending`; others in the batch still commit. |
| Transcript write fails | No queue row is inserted, so the meeting simply reappears in the next listing as unsynced. |

## Testing

`server/tests/test_meetings.py`, following `conftest.py`'s temp-dir pattern:

- `_call` parses both JSON and SSE response bodies, against captured fixtures.
- `list` marks meetings already in `meeting_queue` as synced.
- Selection parsing: `1,3,7-9`, `all`, `none`, out-of-range and malformed input.
- `sync` of an id already queued writes no second file and no second row.
- Transcript file shape: path, frontmatter, `reference::`.
- Queue transitions: `pending → leased → done`; expired leases return to `pending`.
- **The double-index regression:** after registration, a file under
  `meeting-transcripts/` produces documents for the `meeting-transcripts` source
  only, never for the anchor source. This is the test that would have caught the
  segment-matching bug in `ingest.walk()`.
- Registration is idempotent: running `sync()` twice leaves exactly one
  transcripts source and one `meeting-transcripts` exclude entry.
- Existing databases migrate: a `sources` table without `enrich` gains it and
  defaults to enriched.

No network in tests; the HTTP call is injected.

## Non-goals

- Webhooks, tunnels, relays, and any always-on listener.
- Automatic or scheduled syncing of any kind.
- Circleback's `notes`, `actionItems` and `insights` fields — superseded by D4.
- Recording or audio. `recordingUrl` expires in 24 hours and is not stored.
- Meeting tools other than Circleback. The `_call()` boundary is where a second
  adapter would attach; none is built.

## Open items for build time

1. Exact response envelope of `SearchMeetings` and `GetTranscriptsForMeetings`,
   confirmed against a live key. Field names in this spec follow Circleback's
   published webhook payload and must be verified, not assumed.
2. Whether notes the user writes themselves come back distinctly from Circleback's
   generated `notes`. If they do, they belong in the transcript file as raw
   material; if not, no loss.
