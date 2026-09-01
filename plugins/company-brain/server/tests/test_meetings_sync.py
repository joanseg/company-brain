"""Fetching chosen transcripts, writing them, and queueing them for summarising."""
from company_brain import db, meetings

TRANSCRIPTS = {"meetings": [{
    "id": "m1",
    "name": "Pricing review",
    "createdAt": "2026-08-20T10:00:00Z",
    "url": "https://circleback.ai/meetings/m1",
    "attendees": [{"name": "Joan"}, {"name": "David"}],
    "transcript": [
        {"speaker": "Joan", "text": "We should raise the floor price.", "timestamp": 12},
        {"speaker": "David", "text": "Agreed, from March.", "timestamp": 20},
    ],
}]}


def _conn(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    return db.connect()


def test_sync_writes_a_transcript_and_queues_it(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)

    result = meetings.sync(conn, ["m1"])

    assert result["synced"] == ["m1"]
    assert result["skipped"] == []
    written = tmp_path / result["paths"][0]
    assert written.exists()

    text = written.read_text()
    assert "type: transcript" in text
    assert "date: 2026-08-20" in text
    assert "Joan: We should raise the floor price." in text
    assert "https://circleback.ai/meetings/m1" in text

    row = conn.execute("SELECT * FROM meeting_queue WHERE meeting_id = 'm1'").fetchone()
    assert row["status"] == "pending"
    assert row["title"] == "Pricing review"
    conn.close()


def test_sync_names_the_file_by_date_and_title(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)
    result = meetings.sync(conn, ["m1"])
    assert result["paths"][0] == "meeting-transcripts/2026-08-20-pricing-review.md"
    conn.close()


def test_resyncing_a_held_meeting_is_a_no_op(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)
    meetings.sync(conn, ["m1"])

    second = meetings.sync(conn, ["m1"])
    assert second["synced"] == []
    assert second["skipped"] == ["m1"]

    folder = tmp_path / meetings.TRANSCRIPT_DIR
    assert len(list(folder.glob("*.md"))) == 1
    assert conn.execute("SELECT COUNT(*) FROM meeting_queue").fetchone()[0] == 1
    conn.close()


def test_sync_requests_only_the_chosen_ids(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    seen = {}

    def fake_call(tool, args, opener=None):
        seen["tool"] = tool
        seen["ids"] = args["meetingIds"]
        return TRANSCRIPTS

    monkeypatch.setattr(meetings, "_call", fake_call)
    meetings.sync(conn, ["m1"])

    assert seen["tool"] == "GetTranscriptsForMeetings"
    assert seen["ids"] == ["m1"]
    conn.close()


def test_sync_of_nothing_touches_nothing(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call",
                        lambda tool, args, opener=None: _must_not_call())
    result = meetings.sync(conn, [])
    assert result == {"synced": [], "skipped": [], "paths": []}
    conn.close()


def _must_not_call():
    raise AssertionError("_call must not run when no meetings are chosen")
