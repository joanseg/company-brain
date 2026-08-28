# Changelog

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
