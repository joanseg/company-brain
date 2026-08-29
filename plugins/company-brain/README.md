# Company Brain

GraphRAG retrieval over your markdown repositories, as a Claude Code plugin.

Markdown is the source of truth. Everything in `.company-brain/` is derived and
can be deleted and rebuilt.

**Just want to use it?** [GUIDE.md](GUIDE.md) covers install, searching and
capture. This file explains the mechanics. The same explanation with diagrams
lives in the dashboard — `/brain-view`, then the **How it works** tab.

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

---

# How it works

## Why one search method isn't enough

Ask *"how do merchants get their money faster?"* when the document that answers
it says *"settlement latency compresses working capital"*. Not one word matches.
Keyword search returns nothing. Search a common word instead and you get four
hundred hits. Too few, then too many.

The brain never relies on one method. It looks for your question in three
structurally different ways at once, then combines the results.

## Step 1 — documents become cards

A whole document is the wrong unit. A 12,000-word paper might answer your
question in one paragraph; returning the file hands you a haystack.

So each document is cut into chunks of about 240 words. Every chunk records its
document, the heading it sat under, and the line it starts at — which is what
makes a citation like `strategy/uk.md:365` possible at all.

```
  # Settlement                     ← cut at heading boundaries first,
  ...................                so a chunk is about one thing
  ├─ chunk 1  240 words  line 12
  │      ░░░░░  36-word overlap    ← a sentence on a cut survives whole
  ├─ chunk 2  240 words  line 208    on one side of it
  │      ░░░░░
  └─ chunk 3  240 words  line 402

  ## Working capital               ← new heading, new chunk
```

Chunks shorter than 18 words are dropped as scaffolding.

> `/brain-index` does the cutting — walks every configured repository and
> rebuilds chunks for anything whose text changed.
> `/brain-source` decides which repositories get walked.

## Step 2 — three ways to find a chunk

### One: the exact words

Which chunks literally contain your words? This is **BM25** over SQLite's FTS5
index. Two ideas do the work: more mentions beat fewer, and a rare word counts
for far more than a common one. Matching a company's registered name is strong
evidence; matching "the" is none.

Unbeatable for names, codes and exact phrases. Useless for a paraphrase. It is
an index lookup, not a scan, so corpus size barely affects latency.

### Two: the meaning

**bge-m3** reads a chunk and turns it into 1,024 numbers — a position in a space
with 1,024 directions. The model is trained so that passages meaning similar
things land near each other, with no shared vocabulary required.

Your question becomes 1,024 numbers the same way, and retrieval is: which
positions sit closest? That is how the working-capital passage answers the
merchants question.

Because bge-m3 is multilingual, a Spanish document lands near an English
question about the same thing. There is no translation step anywhere.

> `/brain-setup` downloads the model. Until it has run, this signal is
> unavailable and every tool says so rather than failing.
> `/brain-index` computes the vectors — the slow part of a first index.

### Three: the connections

The third method ignores both your words and your meaning. It looks at what your
documents are *about* and how those things relate: a graph of entities — people,
companies, products, decisions — joined by the relationships your notes state.

This catches what the other two structurally cannot. One passage says "the
regulatory application"; another says "the consultant's day rate". Nothing links
them by words or by meaning. The graph knows both attach to the same pilot.

> `/brain-enrich` builds this graph. Without it the brain still answers on the
> first two signals — you just lose the third.
> `entity` queries the graph directly, for questions that name one thing.

## Step 3 — combining three answers into one

Three methods produce three ranked lists whose scores are in unrelated units:
BM25 returns 14.2, cosine returns 0.81, graph mass returns 0.06. Averaging them
is meaningless.

So the scores are discarded and only the *rankings* vote. This is **reciprocal
rank fusion**, and it behaves like a ranked-choice election — each method votes
for a chunk with strength `1 / (60 + rank)`.

```
              rank 1   rank 2   rank 3
  lexical      B        A        D          A is 2nd, 2nd, 1st  → wins
  semantic     C        A        E          B is 1st for one method only
  graph        A        B        F

  score = 1/(60+rank), summed across methods
```

A chunk ranked well by two methods beats a chunk ranked first by only one —
which is correct, because agreement between independent methods is stronger
evidence than one method's enthusiasm.

The fused vote is then reranked:

```
  0.55 · fused rank
+ 0.25 · graph mass          how brightly the graph lights this chunk
+ 0.10 · source weight       how far you trust the repository it came from
+ 0.10 · freshness           how recent the document is
  ─────
  × 0.45 if the document is marked superseded
  max 2 chunks per document  → a spread of evidence, not one file five times
```

> `/brain-ask` runs this entire pipeline.
> `/brain-source` sets the trust weight feeding that 10% slice.

## Step 4 — how the graph moves an answer

Picture entities and chunks as dots joined by lines. Your question drops light on
the dots it obviously touches: the chunks the first two searches liked, plus any
entity you named. That light spreads outward along the connections, dimmer at
each hop, for seven rounds.

```
   question
      │
      ▼
   ●━━━━━━▶ ●━━━━━▶ ●          hop 1: 0.82   hop 2: 0.67   hop 3: 0.55
  seed     1 hop   2 hops
  1.00

  a chunk two hops out still gets a lift — found through what it
  connects to, not through what it says
```

This is **personalised PageRank** — the algorithm that ranked the early web,
except the starting light is placed by your question rather than spread evenly.
Damping is 0.82 over 7 iterations on a CSR adjacency matrix.

> `/brain-view` draws this graph on the **Graph** tab — nodes sized by mention
> count; click one to isolate its neighbourhood.

## Where the entity graph comes from

Something has to read the chunks and write down what is in them. Batches are
handed to Claude with a strict contract: list the entities this passage actually
names and the relationships it actually states — invent nothing, carry nothing in
from outside, and both ends of a relationship must appear in that chunk's own
entity list.

Two design choices make this practical:

- **It runs as subagents in your own session**, so it costs time and context
  rather than API spend.
- **Every chunk is keyed by a sha of its text**, so an unchanged chunk is never
  re-read. The queue leases rows under `BEGIN IMMEDIATE`; stop the job halfway
  and nothing is lost or repeated.

Extraction names things as the text spells them, so one company can arrive as
several rows — an abbreviation and its expansion, a name and a transcript's
misspelling. `merge_entities` folds those together, but **only where two
extractions each independently listed the other as an alias**. Mutual pairs are
unioned into groups; the most-mentioned row survives and carries the graph mass,
the longest spelling becomes the display name, and every variant stays an alias
so retrieval is unaffected either way. That conservative rule is what stops a
product being swallowed by the company it is named after.

> `/brain-enrich` runs extraction batch by batch. Safe to stop and resume.
> `merge_entities` runs the folding pass — ask for the plan first; it shows
> exactly what it proposes to merge before anything is written.

## The layer above — answers no passage contains

Ask *"what are our recurring risks?"* and there is no chunk to find. The answer
is spread across dozens.

So **Leiden** partitions the entity graph into communities — clusters that
connect to each other far more than to everything else, the way friend groups
emerge in a social network without anyone labelling them. Partitioning runs at
two resolutions (0.6 and 1.2) to give a coarse and a fine view. Each cluster is
summarised once, and those summaries become first-class searchable objects that
still cite the real files underneath.

> `/brain-enrich --communities` finds the clusters and writes the summaries.
> Needs the entity graph first.
> `search_themes` ranks those summaries instead of passages.

## Why it stays fast

Everything expensive — embedding a chunk, having Claude read it — is stored
against a sha of that chunk's exact text. Re-indexing compares shas and skips
whatever has not moved. Edit one paragraph and only the chunks that actually
changed are recomputed.

A first full index of a few hundred thousand words takes tens of minutes.
Every index after that takes seconds.

> `/brain-index` is cheap to run often. When files move ahead of the index, the
> dashboard's health strip says so and names the command.

## The other direction — putting knowledge back

Tell the brain something worth keeping and it writes a real markdown file into
your repository — dated, titled, tagged, with a confidence level and links to
entities it recognises — then indexes it immediately.

That direction matters more than it sounds. Your markdown is the truth; the index
is disposable. Delete the whole database and you lose an afternoon of
recomputation, never any knowledge.

Facts that go stale are written as typed assertions — `owner:: Priya since:2026-03`.
Close one with `until:` rather than deleting it, and `/brain-dream` will tell you
when two contradict.

> `/brain-add` writes the note and indexes it, so the next search finds it.
> `/brain-dream` reports contradictions, ageing facts, orphans and hubs. It
> reports; it never edits.

---

## Every command, and the part it drives

Read top to bottom, this is also the order you run them in.

| Command | The concept it drives |
|---|---|
| `/brain-setup` | Fetches the model that turns a chunk into 1,024 numbers. Without it the **meaning** signal is unavailable. |
| `/brain-source` | Chooses which repositories become chunks, and sets each one's **trust weight**. |
| `/brain-index` | Cuts documents into **chunks**, builds the **BM25 index**, embeds whatever's sha changed. |
| `/brain-enrich` | Reads the chunks to build the **entity graph**. |
| `/brain-enrich --communities` | **Leiden-clusters** that graph and writes a summary per cluster. |
| `/brain-ask` | Runs all three searches, the **rank-fusion** vote and the **rerank**. |
| `entity` | Queries the graph directly when your question names one thing. |
| `search_themes` | Ranks the **cluster summaries** instead of passages. |
| `/brain-add` | Writes new knowledge back as **markdown**. |
| `/brain-dream` | Reports contradictions, ageing facts and orphans. |
| `/brain-view` | Opens the dashboard, including this explanation with diagrams. |

Only the first four are needed before the brain is useful. `/brain-setup` and
`/brain-index` alone give you two of the three signals; everything after adds the
graph and the layer above it.

## Tuning constants

Every number above, and where it lives.

| Constant | Value | File |
|---|---|---|
| `CHUNK_WORDS` / `CHUNK_OVERLAP` / `CHUNK_MIN` | 240 / 36 / 18 | `server/company_brain/ingest.py` |
| `RRF_K` | 60 | `server/company_brain/search.py` |
| `W_FUSED` / `W_GRAPH` / `W_SOURCE` / `W_FRESH` | 0.55 / 0.25 / 0.10 / 0.10 | `server/company_brain/search.py` |
| `SUPERSEDED_PENALTY` / `MAX_PER_DOC` | 0.45 / 2 | `server/company_brain/search.py` |
| `ALPHA` / `TURNS` (PageRank) | 0.82 / 7 | `server/company_brain/graph.py` |
| `RESOLUTIONS` (Leiden) | `{0: 0.6, 1: 1.2}` | `server/company_brain/community.py` |
| `MODEL_NAME` / `MAX_SEQ` / `BATCH` | bge-m3 / 512 / 16 | `embed/worker.py` |

`MAX_SEQ` is deliberately 512, not the model's 8192 default — padding 240-word
chunks to 8192 tokens stalls Metal. Both it and the model name are overridable
via `COMPANY_BRAIN_EMBED_MAX_SEQ` and `COMPANY_BRAIN_EMBED_MODEL`.

## Architecture

| | |
|---|---|
| `server/` | MCP server and engine. Light venv — no torch, so it boots instantly. |
| `embed/` | Embedding worker. Heavy venv, launched as a persistent subprocess and kept warm. |
| `.company-brain/brain.db` | SQLite: documents, chunks, FTS5 index, vectors, entities, relations, edges, communities. Disposable. |
| `.company-brain/sources.json` | Which repos to read and how far to trust each. Keep this. |
| `server/company_brain/webui.py` + `assets/` | Read-only dashboard on a daemon thread. Loopback only, validates `Host`, emits no CORS headers. |

The two-venv split is what keeps the MCP server's boot instant: torch is a
multi-second import, and the server never needs it in-process.

If the embedding environment is missing, every tool still answers using lexical
and graph retrieval, and says the dense signal is unavailable.

## Adding a repository

```
/brain-source add /path/to/repo <name> [weight]
/brain-index
```

Weight scales how far that source is trusted relative to the anchor repo (1.0).
`/brain-index` walks every configured repository, so one command picks up changes
anywhere.
