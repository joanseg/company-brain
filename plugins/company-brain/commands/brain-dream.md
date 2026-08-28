---
description: Maintenance report — contradictions, stale facts, orphans, hubs
---

Call the `maintenance` tool and present the report for a human to act on.

Lead with anything that needs a decision:

1. **Contradictions** — two open values for one functional predicate. Show each
   value with its `path:line`. The fix is to close the older one with `until:`,
   not to delete it. Do not edit anything yourself.
2. **Ageing facts** — open functional assertions older than 12 months.
3. **Duplicate facts** — the same claim stated in more than one file.
4. **Superseded documents** still being indexed.
5. **Hubs** — the entities everything connects through.
6. **Documents no entity touches** and **thin documents** — usually a sign that
   enrichment has not reached them, or that the file is a stub.

Keep it short. This is a report, not a task list — ask before changing files.
