# Company Brain

Ask questions of your own markdown and get answers with the file and line the
answer came from.

Point it at one repository or several. It indexes them into a local knowledge
graph — lexical search, semantic search and a graph of who-relates-to-what —
and answers from all three at once. Nothing leaves your machine.

```
claude plugin marketplace add joanseg/company-brain
claude plugin install company-brain@company-brain
```

Then, in Claude Code:

```
/brain-setup     # one-time: Python environments and the embedding model
/brain-index     # read and embed your markdown
/brain-ask       # ask it something
```

## What it does

**Answers with citations.** Every result is a real passage with `path:line`.
Retrieval ranks evidence; it never invents a summary and presents it as a
source.

**Three signals, not one.** Exact keyword match via SQLite FTS5, meaning-based
match via local `bge-m3` embeddings, and personalised PageRank over an entity
graph — fused by reciprocal rank fusion, then reranked. The graph is the part
that knows two passages are about the same project when they share no words.

**Multilingual.** The embedding model handles mixed-language corpora; a question
in Spanish finds the Spanish documents.

**Themes, not just passages.** Leiden clustering over the entity graph produces
community summaries, so you can ask *"what are our recurring risks?"* — a
question whose answer lives in no single document.

**A dashboard.** `/brain-view` serves a read-only page on localhost: the
community map, the entity graph, your corpus grouped by source, and suggested
questions generated from your actual content.

**It takes new knowledge back.** `/brain-add` writes a note into your repo as
markdown and indexes it immediately. Markdown stays the source of truth; the
index is disposable and rebuildable.

## Commands

| | |
|---|---|
| `/brain-ask <question>` | Answer with citations |
| `/brain-add <what to remember>` | Capture into your repo and index it |
| `/brain-index` | Refresh from disk, incrementally |
| `/brain-enrich` | Build the entity and community layers |
| `/brain-source` | List or add repositories |
| `/brain-dream` | Contradictions, stale facts, orphans |
| `/brain-view` | Open the dashboard |
| `/brain-setup` | One-time environment install |

Full guide: [plugins/company-brain/GUIDE.md](plugins/company-brain/GUIDE.md).
Architecture and internals: [plugins/company-brain/README.md](plugins/company-brain/README.md).

## Honest about the costs

- **`/brain-setup` downloads about 2 GB** — PyTorch, sentence-transformers, and
  the bge-m3 model. Once, then never again.
- **The first index is slow.** Embedding is GPU-bound; a few hundred thousand
  words takes tens of minutes on Apple Silicon, longer on CPU. Every index after
  that is seconds, because only changed content is re-embedded.
- **Enrichment costs session context, not money.** Entity extraction runs as
  subagents in your own Claude Code session. It is resumable — stop any time,
  nothing is lost or repeated.
- Search works fully after indexing. Enrichment adds the graph and themes.

## Requirements

`uv`, and Claude Code. Python is managed by `uv`; no system Python needed.
Tested on macOS with Apple Silicon.

## Releasing

Bump `version` in `plugins/company-brain/.claude-plugin/plugin.json` and in the
marketplace entry, update `CHANGELOG.md`, run the tests, then:

```bash
cd plugins/company-brain/server && uv run pytest -q
node --test tests/graph-physics.test.mjs
claude plugin validate plugins/company-brain --strict
claude plugin tag --push          # creates company-brain--v<version>
```

## Licence

MIT.
