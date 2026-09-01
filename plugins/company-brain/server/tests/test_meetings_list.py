"""Listing shows what Circleback has and marks what the brain already holds."""
from datetime import datetime, timedelta, timezone

from company_brain import db, meetings

PAYLOAD = {"meetings": [
    {"id": "m1", "name": "Pricing review", "createdAt": "2026-08-20T10:00:00Z",
     "duration": 1800, "tags": ["commercial"],
     "attendees": [{"name": "Joan", "email": "joan@example.com"}]},
    {"id": "m2", "name": "Standup", "createdAt": "2026-08-21T09:00:00Z",
     "duration": 600, "tags": [], "attendees": []},
]}


def _pages(*pages):
    """Serve one payload per pageIndex, then empties — as the real endpoint does."""
    def fake_call(tool, args, opener=None):
        index = args["pageIndex"]
        return {"meetings": list(pages[index])} if index < len(pages) else {"meetings": []}
    return fake_call


def test_list_maps_circleback_fields(empty_conn, monkeypatch):
    monkeypatch.setattr(meetings, "_call", _pages(PAYLOAD["meetings"]))
    rows = meetings.list_meetings(empty_conn)["meetings"]

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
    monkeypatch.setattr(meetings, "_call", _pages(PAYLOAD["meetings"]))

    rows = {r["id"]: r["synced"] for r in meetings.list_meetings(empty_conn)["meetings"]}
    assert rows == {"m1": True, "m2": False}


def test_list_passes_a_start_date_and_search_term(empty_conn, monkeypatch):
    seen = {}

    def fake_call(tool, args, opener=None):
        seen["tool"] = tool
        seen["args"] = args
        return {"meetings": []}

    monkeypatch.setattr(meetings, "_call", fake_call)
    meetings.list_meetings(empty_conn, days=7, query="pricing")

    expected_start = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    assert seen["tool"] == "SearchMeetings"
    assert seen["args"]["searchTerm"] == "pricing"
    assert seen["args"]["startDate"] == expected_start


def test_list_defaults_to_a_thirty_day_window(empty_conn, monkeypatch):
    seen = {}

    def fake_call(tool, args, opener=None):
        seen["args"] = args
        return {"meetings": []}

    monkeypatch.setattr(meetings, "_call", fake_call)
    meetings.list_meetings(empty_conn)

    expected_start = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    assert seen["args"]["startDate"] == expected_start


def test_list_tolerates_a_meeting_with_no_name(empty_conn, monkeypatch):
    monkeypatch.setattr(meetings, "_call", _pages([{"id": "m3"}]))
    rows = meetings.list_meetings(empty_conn)["meetings"]
    assert rows[0]["title"] == "Untitled meeting"
    assert rows[0]["date"] == ""


def test_list_prefers_attendee_name_then_falls_back_to_email(empty_conn, monkeypatch):
    payload = {"meetings": [{
        "id": "m4", "name": "Kickoff", "createdAt": "2026-08-22T10:00:00Z",
        "attendees": [
            {"name": "Joan", "email": "joan@example.com"},
            {"email": "no-name@example.com"},
            {},
        ],
    }]}
    monkeypatch.setattr(meetings, "_call", _pages(payload["meetings"]))
    rows = meetings.list_meetings(empty_conn)["meetings"]
    assert rows[0]["attendees"] == ["Joan", "no-name@example.com", ""]


def test_list_walks_pages_until_one_comes_back_empty(empty_conn, monkeypatch):
    """The regression: only page 0 was ever fetched, so a user could not see —
    and therefore could not pick — anything past the first page.
    """
    calls = []

    def fake_call(tool, args, opener=None):
        calls.append(args["pageIndex"])
        page = {0: [{"id": "a1"}, {"id": "a2"}], 1: [{"id": "b1"}]}.get(args["pageIndex"], [])
        return {"meetings": page}

    monkeypatch.setattr(meetings, "_call", fake_call)
    listing = meetings.list_meetings(empty_conn)

    assert [r["id"] for r in listing["meetings"]] == ["a1", "a2", "b1"]
    assert calls == [0, 1, 2]
    assert listing["truncated"] is False


def test_a_single_short_page_costs_one_call(empty_conn, monkeypatch):
    calls = []

    def fake_call(tool, args, opener=None):
        calls.append(args["pageIndex"])
        return {"meetings": []}

    monkeypatch.setattr(meetings, "_call", fake_call)
    listing = meetings.list_meetings(empty_conn)

    assert calls == [0]
    assert listing["meetings"] == []
    assert listing["truncated"] is False


def test_the_page_bound_stops_the_walk_and_reports_truncation(empty_conn, monkeypatch):
    calls = []

    def never_empty(tool, args, opener=None):
        calls.append(args["pageIndex"])
        return {"meetings": [{"id": "m%d" % args["pageIndex"]}]}

    monkeypatch.setattr(meetings, "_call", never_empty)
    listing = meetings.list_meetings(empty_conn)

    assert len(calls) == meetings.MAX_PAGES
    assert len(listing["meetings"]) == meetings.MAX_PAGES
    assert listing["truncated"] is True
