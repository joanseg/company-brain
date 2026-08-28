---
name: brain-enrich
description: Build the company brain's entity and community layers by fanning out extraction subagents. Use when the user runs /brain-enrich, asks to enrich or deepen the brain, or when brain status reports outstanding enrich_pending work.
---

# Enriching the brain

Invoked as `/brain-enrich`. Arguments:

- none — run phase 1 for as many rounds as the queue needs, reporting after each
- a number — run that many rounds of phase 1, then stop and report
- `--communities` — run phase 2 instead

This spends session context rather than API budget, so check in with the user
after every few rounds on a large queue.

The lexical, semantic and structural layers are built by the indexer. The
entity and community layers are built here, by subagents in this session, so
enrichment costs no API spend.

The queue is keyed by chunk sha. Work is resumable and idempotent: a chunk
whose text has not changed is never extracted twice, and an interrupted run
loses nothing.

## Phase 1 — entities and relations

Repeat until `pending` reaches zero or the user stops you. Report progress
after each round.

1. Call `enrich_pull` with `limit` = batch size × number of agents you intend
   to run (default batch 15, 4 agents in parallel → `limit: 60`). If `count`
   is 0, phase 1 is done.
2. Split the returned `batch` into groups of ~15 and dispatch one subagent per
   group **in a single message** so they run concurrently. Give each agent the
   full chunk records and this contract:

   > For each chunk, extract the entities it actually names and the
   > relationships it actually states. Return ONLY a JSON array, one object
   > per chunk:
   > `[{"sha": "<the chunk's sha, copied exactly>",
   >    "entities": [{"name": "...", "type": "person|organisation|product|place|concept|event|decision", "description": "one factual sentence grounded in this chunk", "aliases": ["..."]}],
   >    "relations": [{"src": "<entity name>", "dst": "<entity name>", "type": "short-verb-phrase", "description": "what the chunk says about this link"}]}]`
   >
   > Rules: use the name as written in the text. Do not invent entities the
   > chunk does not name, do not infer relationships it does not state, and do
   > not carry knowledge in from outside the chunk. Skip boilerplate,
   > navigation and template scaffolding. A chunk with nothing worth
   > extracting returns empty arrays — that is a valid answer. Both `src` and
   > `dst` must appear in that chunk's own `entities` list.

3. Validate what comes back: it must parse as JSON, every `sha` must be one
   you handed out, and every relation endpoint must exist in that chunk's
   entities. Drop anything that fails rather than pushing it.
4. Call `enrich_push` with the concatenated array as a JSON string.
5. Report `entities`, `relations` and remaining `pending`, then loop.

The first full pass over a large corpus is many rounds. Retrieval already
works without it — offer to stop after any round and resume later.

## Phase 1.5 — resolve duplicate entities

Run this once phase 1 pending reaches 0, before detecting communities.

Extraction names entities as the text spells them, so one company arrives under
several rows — an abbreviation and its expansion, a short name and a legal
name, plus speech-to-text variants of a person's name. Left alone,
they split the graph's centrality and make `entity` show only part of what is
known.

1. `merge_entities` with `apply: false` and show the user the plan.
2. `merge_entities` with `apply: true` once they approve.

It only folds pairs where **each side was independently declared an alias of
the other**. A one-directional claim is usually a part-of relationship — this
is what keeps `WalletConnect Pay` from being swallowed by `WalletConnect`.

The canonical row is the most-mentioned one, renamed to the fullest spelling;
every variant survives as a searchable alias. Check the resulting names — the
longest spelling is occasionally the garbled one, and is worth correcting by
hand.

## Phase 2 — communities

Only once phase 1 pending is 0, or the user asks for it explicitly.

1. `communities_detect` — Leiden partitions the entity graph at two levels.
   If it reports too few relations, phase 1 has not run enough yet.
2. `communities_pending` — returns communities needing a summary, each with
   its entities, relations and source evidence.
3. Dispatch one subagent per community (in parallel, batched) with this
   contract:

   > You are given one community from a company knowledge graph: its
   > entities, the relations between them, and evidence passages. Write:
   > `{"community_id": <id>, "sha": "<sha copied exactly>",
   >   "title": "<5-8 word name for what this cluster is about>",
   >   "summary": "<150-250 words: what this cluster covers, the key claims
   >     the evidence supports, decisions and their owners, open questions and
   >     disagreements. Ground every claim in the evidence given. Do not add
   >     outside knowledge.>",
   >   "rating": <0-10, how important this cluster is to understanding the company>}`

4. `communities_push` with the JSON array.

After phase 2, `search_themes` can answer company-wide questions.

## Re-running

Both phases are safe to re-run. After a `reindex` that changed files, only the
new or changed chunks appear in the queue, and only communities whose
membership actually moved need re-summarising.
