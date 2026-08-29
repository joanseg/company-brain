# Changelog

## 0.3.0

- **The How it works tab is now the full walkthrough.** Sixteen numbered
  sections, each with a sticky rail naming the commands and MCP tools that
  drive it: what lives where across multiple source repos, the two Python
  environments, chunking, the three retrieval signals and the graph's six
  link types, entity extraction and merging, Leiden clusters, the answer
  pipeline, capture, maintenance, and the wiring underneath. Closes with a
  command-to-mechanism table, a constants-to-file table and a glossary.
- Three hand-drawn SVG figures: chunk overlap at heading boundaries, the
  rank-fusion vote worked through, and score propagating across graph hops.
- Every constant stated on the page was read from the source. Corrects
  earlier drafts that miscounted the functional predicates and the tool set.
- Explainer styles are scoped to `.explain` and borrow the dashboard's own
  tokens, so the tab stays offline and the rest of the page is untouched.

## 0.2.0

- **New dashboard tab: How it works.** A plain-language explanation of the
  mechanics — chunking, embeddings, rank fusion, graph propagation and
  community summaries — with the command that drives each part, and diagrams
  for the parts prose handles badly. Bundled and offline like the rest of the
  page.

## 0.1.0

First release.

- Hybrid retrieval: SQLite FTS5 lexical, local multilingual embeddings
  (bge-m3), and personalised PageRank over a graph, fused by reciprocal rank
  fusion. Answers cite `path:line`.
- Entity and relationship extraction, and Leiden community summaries, built by
  subagents in your own session rather than paid API calls.
- `/brain-view`: a read-only localhost dashboard — community map, entity graph,
  corpus treemap, and suggested questions generated from your own content.
- Capture: `/brain-add` writes new knowledge back as markdown and indexes it.
- Multi-repository: point it at as many markdown repos as you like, each with
  its own trust weight.
