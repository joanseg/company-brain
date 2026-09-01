"""MCP tool wrappers for meetings: clamps, id coercion, and JSON-shape tolerance.

`_conn()` builds its own connection from COMPANY_BRAIN_PROJECT_DIR, so every test
points that env var at a tmp_path rather than passing a connection in directly.
The underlying `meetings.*` functions are monkeypatched so no test touches Circleback.
"""
import pytest

from company_brain import mcp_server, meetings


@pytest.fixture(autouse=True)
def project_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))


def test_meetings_list_counts_unsynced_from_the_synced_flag(monkeypatch):
    rows = [
        {"id": "m1", "synced": True},
        {"id": "m2", "synced": False},
        {"id": "m3", "synced": False},
    ]
    monkeypatch.setattr(meetings, "list_meetings", lambda conn, days, query: rows)

    result = mcp_server.meetings_list()

    assert result["meetings"] == rows
    assert result["count"] == 3
    assert result["unsynced"] == 2


@pytest.mark.parametrize("days, expected", [(0, 1), (400, 365)])
def test_meetings_list_clamps_days(monkeypatch, days, expected):
    seen = {}

    def fake(conn, days, query):
        seen["days"] = days
        return []

    monkeypatch.setattr(meetings, "list_meetings", fake)
    mcp_server.meetings_list(days=days)

    assert seen["days"] == expected


def test_meetings_sync_accepts_a_bare_array(monkeypatch):
    seen = {}

    def fake_sync(conn, ids, opener=None):
        seen["ids"] = ids
        return {"synced": ids, "skipped": [], "paths": []}

    monkeypatch.setattr(meetings, "sync", fake_sync)
    result = mcp_server.meetings_sync('["m1","m2"]')

    assert seen["ids"] == ["m1", "m2"]
    assert result["next"]


def test_meetings_sync_accepts_an_object_wrapping_the_ids(monkeypatch):
    seen = {}

    def fake_sync(conn, ids, opener=None):
        seen["ids"] = ids
        return {"synced": ids, "skipped": [], "paths": []}

    monkeypatch.setattr(meetings, "sync", fake_sync)
    mcp_server.meetings_sync('{"meeting_ids": ["m3"]}')

    assert seen["ids"] == ["m3"]


def test_meetings_sync_rejects_a_bare_json_string():
    with pytest.raises(ValueError):
        mcp_server.meetings_sync('"m1"')


def test_meetings_sync_rejects_null():
    with pytest.raises(ValueError):
        mcp_server.meetings_sync("null")


def test_meetings_sync_empty_array_is_a_no_op_and_never_calls_circleback(monkeypatch):
    def boom(tool, args, opener=None):
        raise AssertionError("must not reach Circleback for an empty selection")

    monkeypatch.setattr(meetings, "_call", boom)
    result = mcp_server.meetings_sync("[]")

    assert result["synced"] == []


@pytest.mark.parametrize("limit, expected", [(0, 1), (100, 20)])
def test_meetings_pull_clamps_limit(monkeypatch, limit, expected):
    seen = {}

    def fake_pull(conn, limit):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(meetings, "pull", fake_pull)
    mcp_server.meetings_pull(limit=limit)

    assert seen["limit"] == expected


def test_meetings_push_accepts_a_bare_array(monkeypatch):
    seen = {}

    def fake_push(conn, results):
        seen["results"] = results
        return {"written": len(results)}

    monkeypatch.setattr(meetings, "push", fake_push)
    result = mcp_server.meetings_push('[{"meeting_id":"m1","summary":"s"}]')

    assert seen["results"] == [{"meeting_id": "m1", "summary": "s"}]
    assert result == {"written": 1}


def test_meetings_push_accepts_an_object_wrapping_the_results(monkeypatch):
    seen = {}

    def fake_push(conn, results):
        seen["results"] = results
        return {"written": len(results)}

    monkeypatch.setattr(meetings, "push", fake_push)
    mcp_server.meetings_push('{"results": [{"meeting_id":"m2","summary":"s"}]}')

    assert seen["results"] == [{"meeting_id": "m2", "summary": "s"}]


def test_meetings_push_rejects_malformed_json():
    with pytest.raises(ValueError):
        mcp_server.meetings_push("not json")


def test_meetings_push_rejects_null():
    with pytest.raises(ValueError):
        mcp_server.meetings_push("null")


def test_meetings_push_still_accepts_a_bare_string_pinning_safe_behaviour():
    # A bare JSON string iterates as characters; meetings.push()'s per-item
    # except routes each to "skipped" rather than raising. Pinned so a future
    # guard on this tool does not over-reject a shape that currently works.
    result = mcp_server.meetings_push('"m1"')
    assert result["captured"] == 0
    assert result["skipped"] == ["<unresolved result>", "<unresolved result>"]
