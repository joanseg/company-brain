"""The shapes Circleback's API actually returns, captured from live probes.

SearchMeetings and GetTranscriptsForMeetings both return a BARE JSON LIST inside
`content[0].text` — there is no `structuredContent` and no `{"meetings": [...]}`
wrapper. Meetings carry no `duration`, `tags` or `url`, and transcript items carry
only `id`, `meetingName` and `transcript`, so the metadata for a transcript file
has to come from the listing row the user chose.
"""
import json

import pytest

from company_brain import db, meetings

# Captured from a live SearchMeetings call.
LISTING = [{
    "id": "m1",
    "name": "Pricing review",
    "createdAt": "2026-08-20T10:00:00Z",
    "attendees": [{"name": "Joan"}, {"email": "david@example.com"}],
    "notes": "Circleback's own summary, which this feature never reads.",
}]

# Captured from a live GetTranscriptsForMeetings call.
TRANSCRIPTS = [{
    "id": "m1",
    "meetingName": "Pricing review",
    "transcript": [
        {"speaker": "Joan", "text": "We should raise the floor price.", "timestamp": 12},
        {"speaker": "David", "text": "Agreed, from March.", "timestamp": 20},
    ],
}]


def _rpc(payload) -> dict:
    """The real envelope: result.content[0].text holding a JSON string."""
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def test_unwrap_returns_a_bare_list_unchanged():
    assert meetings._unwrap(_rpc(LISTING)) == LISTING


def test_call_sends_the_client_version_header(monkeypatch):
    """Without it Circleback answers HTTP 426 and nothing works at all."""
    monkeypatch.setenv("CIRCLEBACK_API_KEY", "cb_test")
    seen = {}

    class Resp:
        headers = {"Content-Type": "application/json"}

        def read(self):
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": _rpc([])}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(request, timeout=None):
        seen["version"] = request.get_header(meetings.CLIENT_VERSION_HEADER.capitalize())
        return Resp()

    meetings._call("SearchMeetings", {}, opener=opener)
    assert seen["version"] == meetings.CLIENT_VERSION


def test_list_meetings_reads_a_bare_list(empty_conn, monkeypatch):
    pages = [LISTING, []]
    monkeypatch.setattr(meetings, "_call", lambda t, a, opener=None: pages[a["pageIndex"]])

    rows = meetings.list_meetings(empty_conn)["meetings"]
    assert [r["id"] for r in rows] == ["m1"]
    assert rows[0]["title"] == "Pricing review"
    assert rows[0]["date"] == "2026-08-20"
    assert rows[0]["attendees"] == ["Joan", "david@example.com"]


def test_list_meetings_tolerates_absent_duration_and_tags(empty_conn, monkeypatch):
    """The live payload has neither; they must not crash or invent a value."""
    pages = [LISTING, []]
    monkeypatch.setattr(meetings, "_call", lambda t, a, opener=None: pages[a["pageIndex"]])
    row = meetings.list_meetings(empty_conn)["meetings"][0]
    assert row["duration"] == 0
    assert row["tags"] == []


def _conn(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    return db.connect()


def test_sync_takes_chosen_rows_and_writes_real_metadata(tmp_path, monkeypatch):
    """Transcript items carry no date or attendees, so the chosen row supplies them."""
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda t, a, opener=None: TRANSCRIPTS)

    chosen = [{"id": "m1", "title": "Pricing review", "date": "2026-08-20",
               "attendees": ["Joan", "david@example.com"]}]
    result = meetings.sync(conn, chosen)

    assert result["synced"] == ["m1"]
    assert result["paths"] == ["meeting-transcripts/2026-08-20-pricing-review.md"]
    text = (tmp_path / result["paths"][0]).read_text()
    assert "date: 2026-08-20" in text
    assert "attendees: [Joan, david@example.com]" in text
    assert "Joan: We should raise the floor price." in text
    conn.close()


def test_sync_falls_back_to_meeting_name_when_the_row_lacks_a_title(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda t, a, opener=None: TRANSCRIPTS)

    result = meetings.sync(conn, [{"id": "m1"}])
    text = (tmp_path / result["paths"][0]).read_text()
    assert "# Pricing review" in text          # from the transcript's meetingName
    assert "date: \n" in text or "date:\n" in text
    conn.close()


def test_sync_never_writes_circlebacks_own_notes(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    laden = [dict(TRANSCRIPTS[0], notes="VENDOR SUMMARY", actionItems=["vendor action"])]
    monkeypatch.setattr(meetings, "_call", lambda t, a, opener=None: laden)

    result = meetings.sync(conn, [{"id": "m1", "title": "Pricing review", "date": "2026-08-20"}])
    text = (tmp_path / result["paths"][0]).read_text()
    assert "VENDOR SUMMARY" not in text
    assert "vendor action" not in text
    conn.close()


def test_sync_rejects_bare_string_ids(tmp_path, monkeypatch):
    """The old signature took ids; passing them now must fail loudly, not silently."""
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda t, a, opener=None: TRANSCRIPTS)
    with pytest.raises(ValueError):
        meetings.sync(conn, ["m1"])
    conn.close()
