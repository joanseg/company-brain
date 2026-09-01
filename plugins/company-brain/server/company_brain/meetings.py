"""Pull meetings from Circleback into the brain.

Nothing syncs on its own. `/brain-meetings` lists what Circleback has, the user
picks, and only those transcripts are fetched. Transcripts are raw material and
live in their own source that is never entity-extracted; the summary a subagent
writes from one is the knowledge, and it enters through `capture.add()` like any
hand-written note.

Circleback's own `notes` and `actionItems` are deliberately unused — the point is
the brain's interpretation, not the vendor's.

The official CLI (`@circleback/cli`) is a thin JSON-RPC client over the same
endpoint, so calling it directly keeps a Python plugin free of a Node dependency.
"""

from __future__ import annotations

import json
import os
import urllib.request

API_URL = "https://circleback.ai/api/mcp"
SETTINGS_URL = "https://circleback.ai/settings?tab=api-access"
TIMEOUT_SECONDS = 60

TRANSCRIPT_DIR = "meeting-transcripts"
TRANSCRIPT_SOURCE = "meeting-transcripts"
TRANSCRIPT_WEIGHT = 0.4


def _api_key() -> str:
    key = os.getenv("CIRCLEBACK_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "CIRCLEBACK_API_KEY is not set. Create a key at %s and add it to the "
            "plugin's settings." % SETTINGS_URL)
    return key


def _unwrap(result: dict) -> dict:
    """MCP returns either structured content or JSON inside a text block."""
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for item in result.get("content") or []:
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except (json.JSONDecodeError, KeyError):
                return {"text": item.get("text", "")}
    return result


def _body(payload: bytes, content_type: str) -> dict:
    if "text/event-stream" in content_type:
        for line in payload.decode().splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ValueError("Circleback returned an event stream with no data frame.")
    return json.loads(payload.decode())


def _call(tool: str, args: dict, opener=None) -> dict:
    """One JSON-RPC `tools/call`. `opener` is injected by tests."""
    request = urllib.request.Request(
        API_URL,
        data=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {"intent": "Company Brain sync.", **args}},
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer %s" % _api_key(),
        },
    )
    with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT_SECONDS) as response:
        envelope = _body(response.read(), response.headers.get("Content-Type", ""))
    if envelope.get("error"):
        raise ValueError(envelope["error"].get("message", "Circleback rejected the request."))
    return _unwrap(envelope.get("result") or {})
