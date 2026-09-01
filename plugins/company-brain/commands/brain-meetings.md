---
description: List your Circleback meetings and sync the ones you choose into the brain
argument-hint: "[days] [search term]"
---

Bring meetings into the company brain: **$ARGUMENTS**

Use the `brain-meetings` skill. Nothing syncs without the user choosing it.

Arguments, all optional: a number sets the date window in days (default 30),
any remaining words are a search term.

`CIRCLEBACK_API_KEY` lives in the MCP server's environment, not this session's,
so do not try to check it first. If it is unset, `meetings_list` fails with a
named error pointing at https://circleback.ai/settings?tab=api-access — surface
that error to the user and stop rather than retrying.
