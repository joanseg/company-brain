---
name: company-brain
description: Investigate the company using the indexed markdown repositories as an evidence-first brain. Use for strategy, product, customers, brand, fundraising, people, partners, decisions, status, or any cross-document question — and whenever the user asks to remember or record something.
---

# Company Brain

Use this skill whenever a task needs internal company context. The markdown in
the configured repositories is the source of truth; the index is disposable and
rebuildable. Retrieval ranks evidence — it is not itself an answer.

## Source authority

Sources are whatever the user configured — **run `sources` to see them before
reasoning about authority**, never assume. Each has a weight: the anchor
repository (the one the brain was installed into) sits at 1.0, and anything
added later normally sits below it, so a lower-weighted source has to be a
better match to outrank a curated one.

Weight tells you how far to trust a source, not what is in it. A repository of
finished decisions and one of working drafts can both be present; if you cannot
tell which you are quoting, say so rather than presenting a draft as settled.

Documents whose path or title contains `template`, `placeholder` or `test` are
already scored down to 0.15. Treat them as leads, never as claims. Results
flagged `superseded: true` come from documents explicitly marked as such —
name the newer version instead of quoting them.

## Which tool

- **`search_evidence`** — the default. A named entity, person, partner, project,
  decision or document. `mode="local"` concentrates on the best evidence;
  `mode="global"` spreads the first few results across strategic areas.
- **`search_themes`** — a thematic question whose answer is in no single
  passage: recurring risks, patterns across meetings, how areas relate. It
  reasons over community summaries rather than chunks.
- **`entity`** — resolve a name to what the graph knows: type, aliases, related
  entities, which documents mention it, typed assertions about it. Use this
  first when the question names one thing.
- **`neighbours`** — follow the graph outward from a node id returned above.
- **`remember`** — capture something the user wants retained.

## The retrieval loop

Bounded, at most three passes. Stop as soon as the evidence is sufficient.

1. Name the facets the answer needs — decision, owner, evidence, current
   status, risks, disagreement.
2. Retrieve once. Identify which facets are missing, where sources disagree,
   and what looks stale or draft.
3. Run at most two targeted follow-ups for those gaps, then answer.

For a multi-hop question, prefer two or three focused `search_evidence` calls
over one broad one. Do not recurse indefinitely, do not manufacture consensus,
and never treat a high retrieval score as verification.

## Answer standard

- Lead with the decision or the direct answer.
- Cite concrete `path:line`, and name which source it came from.
- Separate **stated evidence** from **your inference**. Say which is which.
- For engineering or spec work, name the target repository **and branch**.
- Surface conflicts rather than blending them. Report both sources, prefer the
  more recent explicit decision, and ask for a human decision when it changes
  an external action.
- Say when evidence is thin, stale, or only found in a draft.

## Keeping the index honest

- `status` — coverage, embedding progress, outstanding enrichment.
- `reindex` — incremental; run it when files are newer than the index, or
  before comprehensive research. Unchanged files cost nothing.
- `maintenance` — contradictions on functional predicates, duplicate facts,
  ageing assertions, hubs, documents no entity touches. It reports; it never
  edits. Close an outdated functional fact with `until:` rather than deleting.
- `add_source` — bring another repository in. `reindex` afterwards.

If `signals.dense` is false, semantic matching is unavailable and you are
seeing lexical plus graph results only; say so if it matters, and point at
`/brain-setup`.

## Capture

Use `remember` when the user asks to retain something. It writes a real
markdown note into `memory/inbox/` in the anchor repo and indexes it
immediately, so the next search can find it.

Never invent typed facts the user did not state. `facts` entries use
`predicate:: value`, or `subject|predicate:: value` when the fact is about
something other than the note itself. Writing outside the anchor repo requires
an explicit `source` and `folder`.

Do not modify source markdown or company decisions unless asked. Index rebuilds
only write derived cache data.
