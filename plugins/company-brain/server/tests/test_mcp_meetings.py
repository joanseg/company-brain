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
    monkeypatch.setattr(meetings, "list_meetings",
                        lambda conn, days, query: {"meetings": rows, "truncated": True})

    result = mcp_server.meetings_list()

    assert result["meetings"] == rows
    assert result["count"] == 3
    assert result["unsynced"] == 2
    assert result["truncated"] is True


@pytest.mark.parametrize("days, expected", [(0, 1), (400, 365)])
def test_meetings_list_clamps_days(monkeypatch, days, expected):
    seen = {}

    def fake(conn, days, query):
        seen["days"] = days
        return {"meetings": [], "truncated": False}

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


def test_meetings_sync_accepts_an_object_wrapping_the_rows(monkeypatch):
    seen = {}

    def fake_sync(conn, chosen, opener=None):
        seen["chosen"] = chosen
        return {"synced": [r["id"] for r in chosen], "skipped": [], "paths": []}

    monkeypatch.setattr(meetings, "sync", fake_sync)
    mcp_server.meetings_sync('{"meetings": [{"id": "m3", "title": "T", "date": "2026-08-20"}]}')

    # Rows pass through whole — the date and title are the metadata a transcript
    # response does not carry.
    assert seen["chosen"] == [{"id": "m3", "title": "T", "date": "2026-08-20"}]


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


def test_meetings_push_rejects_a_bare_json_string():
    # A bare string only "worked" before because iterating it yields
    # characters and push()'s per-item except swallows the AttributeError on
    # each one, silently returning nothing captured. Not a shape worth
    # tolerating — reject it like meetings_sync does.
    with pytest.raises(ValueError):
        mcp_server.meetings_push('"m1"')


def test_meetings_push_rejects_a_bare_number():
    with pytest.raises(ValueError):
        mcp_server.meetings_push("5")


def test_meetings_sync_rejects_a_bare_number():
    with pytest.raises(ValueError):
        mcp_server.meetings_sync("5")


def test_meetings_sync_rejects_an_object_without_the_wrapper_key(monkeypatch):
    """It used to fall through as an empty selection: a clean success, nothing synced."""
    def boom(conn, ids, opener=None):
        raise AssertionError("must not sync on an unrecognised shape")

    monkeypatch.setattr(meetings, "sync", boom)
    with pytest.raises(ValueError):
        mcp_server.meetings_sync('{"foo": 1}')


def test_meetings_push_rejects_an_object_without_the_wrapper_key(monkeypatch):
    """A single result object, unwrapped, used to report success having written
    nothing — the summary the user watched a subagent produce silently lost.
    """
    def boom(conn, results):
        raise AssertionError("must not push on an unrecognised shape")

    monkeypatch.setattr(meetings, "push", boom)
    with pytest.raises(ValueError):
        mcp_server.meetings_push('{"meeting_id":"m1","summary":"s"}')
