# Company Brain

**Ask questions of your own markdown. Get answers with the file and line they came from.**

Point it at one repository or a dozen. It builds a local knowledge graph over
your notes, specs, meeting transcripts and working documents — then answers
from three signals at once: exact keyword match, meaning, and how entities
relate to each other.

Nothing is sent anywhere. The embedding model runs on your machine, the index
is a SQLite file in your repo, and the only thing that ever leaves is nothing.

```bash
claude plugin marketplace add joanseg/company-brain
claude plugin install company-brain@company-brain
```

Restart Claude Code, then:

```
/brain-setup     # once — Python environments and the embedding model
/brain-index     # read and embed your markdown
/brain-ask       # ask it something
```

---

## See it work

```
You:  /brain-ask what did we decide about the pricing model, and who owns it?

Claude: You moved from per-seat to usage-based in March, and Priya owns it.

        **Stated.** The decision is recorded in
        docs/decisions/2026-03-pricing.md:14 — "usage-based, billed monthly,
        effective Q2" — with `owner:: Priya since:2026-03` on line 22.

        **Worth flagging.** notes/meetings/2026-05-forecast.md:88 still models
        per-seat revenue. Either the forecast predates the decision or it was
        never updated; the brain can't tell you which.
```

Retrieval ranks evidence. It never summarises and presents the summary as a
source — every claim above points at a real line you can open.

## Which tool for which question

| You have | Use | Why |
|---|---|---|
| One named thing — a person, company, project | `entity` | Resolves nicknames and misspellings to one node, then shows related entities, the documents that mention it, and any typed facts |
| A question needing evidence | `/brain-ask` | Keyword + meaning + graph, fused and reranked. Returns passages with `path:line` |
| A theme or pattern | `search_themes` | Reasons over community summaries instead of passages — for answers no single document contains |

The third one is the interesting one. Leiden clustering over the entity graph
produces summaries of what each cluster is *about*, so you can ask "what are
our recurring risks?" and get an answer assembled from documents that never
mention each other.

## How retrieval works

```
question
 ├─ SQLite FTS5              exact names, indexed — not a scan
 ├─ bge-m3 embeddings        meaning, local, multilingual
 └─ entity alias match       seeds the graph
        ↓ reciprocal rank fusion
        ↓ personalised PageRank over the graph
   cited passages: source · path · line
```

The graph is the part that earns its keep: it knows two passages concern the
same project when they share no vocabulary at all — which is exactly what
similarity search cannot see.

**The full explanation** — chunking, BM25, embeddings, rank fusion, PageRank,
Leiden communities, and which command drives each part — is in
[the plugin README](plugins/company-brain/README.md#how-it-works), and in the
dashboard's **How it works** tab.

Everything expensive is keyed by content hash, so re-indexing only touches what
actually changed. Editing one file costs seconds, not a rebuild.

## Commands

| | |
|---|---|
| `/brain-ask <question>` | Answer with citations |
| `/brain-add <what to remember>` | Capture into your repo as markdown, then index it |
| `/brain-index` | Refresh from disk, incrementally |
| `/brain-enrich` | Build the entity and community layers |
| `/brain-source` | List or add repositories |
| `/brain-meetings` | List your Circleback meetings and sync the ones you pick. Needs a Circleback API key in the plugin's settings. Transcripts become searchable but never enter the knowledge graph; the summaries do |
| `/brain-dream` | Contradictions, ageing facts, orphans — reports, never edits |
| `/brain-view` | Open the dashboard |
| `/brain-setup` | One-time environment install |

## The dashboard

`/brain-view` serves a read-only page on localhost with three views — the
community map, the entity graph, and your corpus grouped by source and folder —
plus a panel of questions generated from your actual content, and a health strip
that names the command to run when something needs attention.

It is bound to loopback, validates the `Host` header, emits no CORS headers and
has no write routes. It runs only while your Claude Code session is alive.

## Multiple repositories

```
/brain-source add ~/code/handbook handbook 0.9
/brain-index
```

The number is trust weight. The repository you installed into sits at `1.0`;
anything added later normally sits below it, so a lower-weighted source has to
be a better match to outrank a curated one. Weight feeds directly into ranking.

`/brain-index` walks every configured repository, so changes anywhere are picked
up in one command.

## Capture

`/brain-add` writes a dated, titled markdown note into your repo — with tags, a
confidence level, and links to entities it recognises — then indexes it so the
next search finds it. Markdown stays the source of truth; the index is
disposable and rebuildable.

Facts that go stale are written as typed assertions: `owner:: Priya since:2026-03`.
When one changes, close the old one with `until:` rather than deleting it, and
`/brain-dream` will tell you when two contradict.

## Privacy

- The embedding model runs locally. No API key, no inference request, no upload.
- Entity extraction runs as subagents in your own Claude Code session.
- The index lives in `.company-brain/` inside your repo and is gitignored by
  default.
- The dashboard is loopback-only and read-only.

## Requirements, and honest costs

Claude Code and [`uv`](https://docs.astral.sh/uv/). Python is managed by `uv`;
no system Python needed. Developed on macOS with Apple Silicon.

- **`/brain-setup` downloads about 2 GB** — PyTorch, sentence-transformers and
  the bge-m3 model. Once.
- **The first index is slow.** Embedding is GPU-bound; a few hundred thousand
  words takes tens of minutes on Apple Silicon and longer on CPU. Every index
  after that is seconds.
- **Enrichment costs session context, not money.** It is resumable — stop any
  time, nothing is lost or repeated.
- Search works fully after indexing. Enrichment adds the graph and themes.

## Updating

```bash
claude plugin update company-brain@company-brain
```

Use the qualified `<plugin>@<marketplace>` name — the bare name does not
resolve. If you installed into a single repository rather than your user
account, add `--scope project`.

That pulls the latest release whenever a new tag is pushed. Restart Claude Code
to apply it — MCP servers only load at session start.

## Troubleshooting

**The commands don't appear.** Restart Claude Code. Plugins load at session
start, not on install.

**`/brain-ask` says the dense signal is unavailable.** The embedding
environment was never built, or the model download failed. Run `/brain-setup`.
Search still works on keyword and graph alone in the meantime.

**The dashboard won't open.** It runs only while the session is alive; if the
server failed to bind, restart the session. The port is `view_port` (default
`4717`) — change it with `/plugin configure company-brain@company-brain` if
something else is using it.

**The graph tab is empty.** Run `/brain-enrich` — the entity layer has to exist
before there is a graph to draw.

**Answers cite the wrong repository.** Check `/brain-source`. Weight is
relative trust, and a source added at the same weight as your anchor will
compete with it.

## Uninstall

```bash
claude plugin uninstall company-brain@company-brain
claude plugin marketplace remove company-brain
```

Then delete `.company-brain/` from any repository you indexed. Your markdown is
untouched — the plugin only ever wrote notes you asked it to write.

## Releasing

Bump `version` in `plugins/company-brain/.claude-plugin/plugin.json` **and** in
the marketplace entry, update `CHANGELOG.md`, then:

```bash
cd plugins/company-brain/server && uv run pytest -q
node --test tests/graph-physics.test.mjs
claude plugin validate plugins/company-brain --strict
claude plugin tag plugins/company-brain --push
```

The tag command validates that both versions agree before creating
`company-brain--v<version>`.

## Licence

MIT.
