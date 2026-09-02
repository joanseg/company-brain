# Using the Company Brain

A graph over every markdown file you point it at. Ask it questions, feed it new
facts, and it answers with the file and line the answer came from.

## Getting it running

Four steps, once, in order.

```bash
# 1. install it (run from the repo you want the brain anchored to)
claude plugin marketplace add joanseg/company-brain
claude plugin install company-brain@company-brain
# then restart Claude Code — MCP tools only load at session start
```

```
/brain-setup                  # 2. build both Python environments (~2GB, once)
/brain-index                  # 3. read and embed the corpus
/brain-enrich                 # 4. build the entity layer
/brain-enrich --communities   #    then the community summaries
```

Search works after step 3. Step 4 adds the graph and the cross-document
summaries. It runs as subagents rather than paid API calls, so it costs context
and time instead of money — and it is resumable, because the queue is keyed by
content hash. Stop it whenever; nothing is lost and nothing is processed twice.

## Asking it things

Pick the right entry point; it matters more than phrasing the question well.

| You have | Use | Why |
|---|---|---|
| One named thing | `entity` | Resolves nicknames and misspellings to one node, shows related entities, documents and typed facts |
| A question needing evidence | `/brain-ask <question>` | Keyword + meaning + graph, fused and reranked. Returns passages with `path:line` |
| A theme or pattern | `search_themes` | Ranks community summaries instead of passages — for answers no single passage contains |

Spanish and English work in the same query; the embedding model is multilingual.

## Feeding it

Both paths write markdown into the repo. Markdown is the truth; the index is
disposable.

**A fact, from chat.** `/brain-add <what to remember>` — or just say *remember
that…* in conversation. Creates a dated, titled note in `memory/inbox/` with
tags, a confidence level and links to entities it recognises, then indexes it
immediately so the next search finds it. It never records as *stated* anything
you did not actually say.

Facts that go stale are written as typed assertions — `owner:: Priya
since:2026-03`. When one changes, close the old one with `until:` rather than
deleting it.

**A whole repo.**

```
/brain-source add ~/Documents/some-repo notes 0.7
/brain-index
```

The number is trust weight, feeding directly into ranking. The anchor repo is
`1.0`; everything else should normally sit below it. Version-history folders,
`node_modules` and dotfiles are skipped automatically.

**A meeting.** `/brain-meetings` — list your Circleback meetings and sync the
ones you pick. Needs a Circleback API key in the plugin's settings. Transcripts
become searchable but never enter the knowledge graph; the summaries do.

Nothing syncs automatically — you pick each meeting from the list. The summary
itself is written by a subagent in your own session, reading the raw
transcript; Circleback's own AI notes and action items are never used.

## Keeping it honest

| When | Run | What it does |
|---|---|---|
| After editing files | `/brain-index` | Seconds; only touches what changed. A session-start hook tells you when files have moved ahead of the index |
| Now and then | `/brain-dream` | Contradictions, ageing assertions, duplicated claims, superseded and orphan documents. Reports only — never edits |
| After a big enrich | `merge_entities` | Folds duplicate entities, but only where two independent extractions each called the other an alias. Run with `apply: false` first |

## Reference

**Commands** — `/brain-ask` · `/brain-add` · `/brain-index` · `/brain-enrich` ·
`/brain-source` · `/brain-meetings` · `/brain-dream` · `/brain-view` ·
`/brain-setup`

**Tools** — `search_evidence` · `search_themes` · `entity` · `neighbours` ·
`remember` · `status` · `reindex` · `sources` · `add_source` · `maintenance` ·
`merge_entities`

## Seeing what's in there

`/brain-view` opens a read-only dashboard on localhost with three
views — the community summaries as a card grid, the entity graph, and the
corpus grouped by source and folder — plus a panel of suggested questions
generated from your actual content.

It runs only while the Claude Code session is alive, is bound to loopback, and
never writes anything. The port comes from the `view_port` setting (default
4717); change it with `/plugin configure company-brain`.

## Where things live

Everything derived sits in `.company-brain/` — database, vectors, graph. It is
gitignored and rebuildable; deleting it costs an afternoon of re-indexing, never
any knowledge. The one file worth keeping is `sources.json`, the list of repos
and their weights.

Architecture and internals: [README.md](README.md).
