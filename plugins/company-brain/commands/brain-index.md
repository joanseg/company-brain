---
description: Build or refresh the company brain index
argument-hint: "[--no-embed]"
---

Refresh the Company Brain index from disk using the `reindex` tool.

Pass `embed: false` if the user passed `--no-embed` ($ARGUMENTS).

Indexing is incremental — unchanged files are skipped and only new or changed
text is re-embedded. A first full build over a large corpus takes a while
because every chunk must be embedded; say so rather than appearing stuck.

Afterwards, report per source: documents, chunks, how many chunks were added or
updated, embedding coverage, and how many chunks are pending enrichment. If
anything is pending, mention that `/brain-enrich` builds the entity layer.
