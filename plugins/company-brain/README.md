# Company Brain

GraphRAG retrieval over your markdown repositories, as a Claude Code plugin.

Markdown is the source of truth. Everything in `.company-brain/` is derived and
can be deleted and rebuilt.

**New here? Read [GUIDE.md](GUIDE.md)** — how to install, search, and feed it.
This file covers the internals.

## Install

```bash
claude plugin marketplace add joanseg/company-brain
claude plugin install company-brain@company-brain
```

Then, once, in Claude Code:

```
/brain-setup     # creates both Python environments, downloads the embedding model
/brain-index     # builds the index
/brain-enrich    # entity + relationship layer (subagents, no API spend)
```

## How retrieval works

```
question
 ├─ FTS5 bm25                  indexed, not a scan
 ├─ bge-m3 cosine              local, multilingual, GPU
 └─ entity alias match
        ↓ reciprocal rank fusion
        ↓ personalised PageRank over the graph
        ↓ 0.55·fused + 0.25·graph + 0.10·source + 0.10·freshness
   cited chunks: source · path · line
```

`search_themes` takes the other route: Leiden communities over the entity
graph, summarised once, then ranked as first-class objects. That is what
answers "what are our recurring risks" — a question no single passage contains.

## Architecture

| | |
|---|---|
| `server/` | MCP server and engine. Light venv — no torch, so it boots instantly. |
| `embed/` | Embedding worker. Heavy venv, launched as a persistent subprocess and kept warm. |
| `.company-brain/brain.db` | SQLite: documents, chunks, FTS5 index, vectors, entities, relations, edges, communities. Disposable. |
| `.company-brain/sources.json` | Which repos to read and how far to trust each. Keep this. |
| `server/company_brain/webui.py` + `assets/` | Read-only dashboard on a daemon thread. Loopback only, validates `Host`, emits no CORS headers. |

Everything expensive is keyed by content sha, so re-indexing only touches what
actually changed. Embeddings and extractions survive a file edit that did not
move them.

If the embedding environment is missing, every tool still answers using lexical
and graph retrieval, and says the dense signal is unavailable.

## Adding a repository

```
/brain-source add /path/to/repo <name> [weight]
/brain-index
```

Weight scales how far that source is trusted relative to the anchor repo (1.0).

## Commands

| | |
|---|---|
| `/brain-ask <question>` | Answer with citations |
| `/brain-add <what to remember>` | Capture into `memory/inbox/` and index it |
| `/brain-index` | Refresh from disk (incremental) |
| `/brain-enrich [n\|--communities]` | Build the entity and community layers |
| `/brain-source` | List or add knowledge sources |
| `/brain-dream` | Contradictions, stale facts, hubs, orphans — reports, never edits |
| `/brain-view` | Open the read-only dashboard in your browser |
| `/brain-setup` | One-time environment setup |
