---
description: Add or list repositories the company brain reads
argument-hint: "[add <path> <name> [weight]]"
---

Manage the brain's knowledge sources: $ARGUMENTS

- With no arguments, or `list`: call `sources` and show each source with its
  root, weight and exclusions.
- With `add <path> <name> [weight]`: call `add_source`. Default weight 0.8 —
  the anchor repo is 1.0, so a new source should normally sit below it unless
  the user says otherwise. Confirm the path exists and holds markdown before
  adding, then run `reindex` and report what came in.
