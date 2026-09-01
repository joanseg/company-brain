"""Listing shows what Circleback has and marks what the brain already holds."""
from company_brain import db, meetings

PAYLOAD = {"meetings": [
    {"id": "m1", "name": "Pricing review", "createdAt": "2026-08-20T10:00:00Z",
     "duration": 1800, "tags": ["commercial"],
     "attendees": [{"name": "Joan", "email": "joan@example.com"}]},
    {"id": "m2", "name": "Standup", "createdAt": "2026-08-21T09:00:00Z",
     "duration": 600, "tags": [], "attendees": []},
]}


def test_list_maps_circleback_fields(empty_conn, monkeypatch):
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: PAYLOAD)
    rows = meetings.list_meetings(empty_conn)

    assert [r["id"] for r in rows] == ["m1", "m2"]
    assert rows[0]["title"] == "Pricing review"
    assert rows[0]["date"] == "2026-08-20"
    assert rows[0]["attendees"] == ["Joan"]
    assert rows[0]["tags"] == ["commercial"]
    assert rows[0]["synced"] is False


def test_list_marks_meetings_already_in_the_queue(empty_conn, monkeypatch):
    empty_conn.execute(
        "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
        "VALUES('m1', 'Pricing review', '2026-08-20', 'meeting-transcripts/a.md', 'done', '2026-08-20')")
    empty_conn.commit()
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: PAYLOAD)

    rows = {r["id"]: r["synced"] for r in meetings.list_meetings(empty_conn)}
    assert rows == {"m1": True, "m2": False}


def test_list_passes_a_start_date_and_search_term(empty_conn, monkeypatch):
    seen = {}

    def fake_call(tool, args, opener=None):
        seen["tool"] = tool
        seen["args"] = args
        return {"meetings": []}

    monkeypatch.setattr(meetings, "_call", fake_call)
    meetings.list_meetings(empty_conn, days=7, query="pricing")

    assert seen["tool"] == "SearchMeetings"
    assert seen["args"]["searchTerm"] == "pricing"
    assert "startDate" in seen["args"]


def test_list_tolerates_a_meeting_with_no_name(empty_conn, monkeypatch):
    monkeypatch.setattr(meetings, "_call",
                        lambda tool, args, opener=None: {"meetings": [{"id": "m3"}]})
    rows = meetings.list_meetings(empty_conn)
    assert rows[0]["title"] == "Untitled meeting"
    assert rows[0]["date"] == ""
