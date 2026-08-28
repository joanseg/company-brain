---
description: Open the Company Brain dashboard in your browser
---

Open the read-only Company Brain dashboard.

## Steps

1. Call **`open_view`**. It launches the default browser at the localhost URL
   the dashboard server is listening on (e.g. `http://127.0.0.1:4717`).
2. If `open_view` raises an error, the dashboard server never started — tell
   the user to restart their Claude Code session; the MCP process starts the
   dashboard at boot. Don't state or guess a URL: the port is configurable
   (`view_port`), and when the server did not bind there is no real one to
   give.

## Don't

- Don't start a separate process or use `Bash` to launch a browser. The tool
  handles the platform differences in one place.
- Don't offer to run searches from the page. It is deliberately read-only;
  questions go through `/brain-ask`.
