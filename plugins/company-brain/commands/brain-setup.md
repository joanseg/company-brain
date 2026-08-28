---
description: One-time setup — install the brain's Python environments
allowed-tools: Bash
---

Set up the Company Brain environments. This is a one-time cost.

1. Run the server environment (fast, no ML dependencies):

   `uv sync --project "${CLAUDE_PLUGIN_ROOT}/server"`

2. Run the embedding environment. This installs torch and sentence-transformers
   and is a large download — tell the user it may take several minutes:

   `uv sync --project "${CLAUDE_PLUGIN_ROOT}/embed"`

3. Warm the model so the first search is not slow. The model itself (~2GB) is
   downloaded on this first run:

   `printf '{"texts":["warm"]}\n{"stop":true}\n' | uv run --project "${CLAUDE_PLUGIN_ROOT}/embed" python "${CLAUDE_PLUGIN_ROOT}/embed/worker.py"`

Report the model name, device and dimension from the worker's `ready` line.

Then tell the user to run `/brain-index`.
