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
   gave. If `count` is 0, say so and stop. If `truncated` is true the listing
   stopped at its page bound — tell the user the list may be short and offer to
   narrow it with a search term or a smaller window.
2. Print a numbered table: number, date, title, duration in minutes, attendees,
   tags. Number only the meetings still open to sync; list any whose `synced`
   is true separately, marked as already held, with no number of their own —
   so every number in the table is one the user can actually pick.
3. Ask which to sync. Accept `1,3,7-9`, `all`, `none`, or a name or date
   fragment. Use a printed table and a free-text reply rather than
   AskUserQuestion — a 30-day window routinely exceeds its four-option limit.
4. Resolve the selection to ids and read them back to the user before syncing.
   If a fragment or any part of the selection matches more than one row, or
   matches none, list what it matched and ask again rather than guessing —
   never resolve an ambiguous selection on the user's behalf. `none` is a
   valid answer: confirm and stop, leaving no trace.
5. Call `meetings_sync` with the chosen ids as a JSON string.
6. Call `reindex` so the transcripts become searchable.

## Phase 2 — summarise

Repeat until `pending` reaches 0.

1. Call `meetings_pull` (default limit 5). If `count` is 0, phase 2 is done.
2. Dispatch one subagent per meeting **in a single message** so they run
   concurrently. Give each the transcript carried by its item in the `batch`
   `meetings_pull` returned, and this contract:

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
   > commitments, and do not bring in knowledge from outside the transcript. Name
   > entities in `related` as the transcript names them too — do not invent ones
   > it doesn't mention. A meeting with no actions returns an empty `facts`
   > array — that is a valid answer.

3. Validate: it must parse as JSON and every `meeting_id` must be one you handed
   out. Drop anything that fails rather than pushing it.
4. Call `meetings_push` with the concatenated array as a JSON string.
5. **Show the user each summary** as well as storing it, then report the file
   paths. Report every entry in `skipped` verbatim as well — each names a
   meeting and why it was dropped. A skipped meeting has no summary, so never
   count it as one: its lease returns to the queue after 30 minutes, and one
   that fails the same way twice needs the user, not another retry.
6. Call `reindex` once the queue is empty so the new notes are searchable.

## Notes

Circleback's own AI notes and action items are deliberately not used — the
summary is the brain's own reading of the transcript.

Transcripts live in `meeting-transcripts/` as their own low-weight source that is
never entity-extracted, so they are searchable verbatim without distorting the
graph. The summaries are ordinary captured notes and carry full weight.

Re-running is safe: a meeting already held is skipped, and an interrupted
summarising run returns its leases to the queue after 30 minutes.
