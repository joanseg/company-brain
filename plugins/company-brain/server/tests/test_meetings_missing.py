"""A transcript deleted from disk must be recoverable, not a stranded row.

`pull()` marks such a row 'missing'. If 'missing' counted as held, the meeting
would be listed as already synced, skipped by `sync()`, and counted by nothing —
unrecoverable short of hand-editing SQLite.
"""
from company_brain import db, meetings

TRANSCRIPTS = [{
    "id": "m1",
    "name": "Pricing review",
    "createdAt": "2026-08-20T10:00:00Z",
    "attendees": [{"name": "Joan"}],
    "transcript": [{"speaker": "Joan", "text": "We should raise the floor price."}],
}]


def _fake_call(tool, args, opener=None):
    if tool == "SearchMeetings" and args["pageIndex"] > 0:
        return []
    return TRANSCRIPTS


def _conn(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(meetings, "_call", _fake_call)
    return db.connect()


def _mark_missing(conn, tmp_path):
    (tmp_path / meetings.TRANSCRIPT_DIR / "2026-08-20-pricing-review.md").unlink()
    meetings.pull(conn)
    assert conn.execute(
        "SELECT status FROM meeting_queue WHERE meeting_id='m1'").fetchone()[0] == "missing"


def test_a_missing_meeting_lists_as_unsynced(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    meetings.sync(conn, [{"id": "m1", "title": "Pricing review", "date": "2026-08-20", "attendees": ["Joan", "David"], "url": "https://circleback.ai/meetings/m1"}])
    _mark_missing(conn, tmp_path)

    assert meetings.list_meetings(conn)["meetings"][0]["synced"] is False
    conn.close()


def test_sync_refetches_a_missing_meeting(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    meetings.sync(conn, [{"id": "m1", "title": "Pricing review", "date": "2026-08-20", "attendees": ["Joan", "David"], "url": "https://circleback.ai/meetings/m1"}])
    _mark_missing(conn, tmp_path)

    result = meetings.sync(conn, [{"id": "m1", "title": "Pricing review", "date": "2026-08-20", "attendees": ["Joan", "David"], "url": "https://circleback.ai/meetings/m1"}])

    assert result["synced"] == ["m1"]
    assert result["skipped"] == []
    row = conn.execute("SELECT status, rel_path FROM meeting_queue WHERE meeting_id='m1'").fetchone()
    assert row["status"] == "pending"
    assert (tmp_path / row["rel_path"]).exists()
    assert conn.execute("SELECT COUNT(*) FROM meeting_queue").fetchone()[0] == 1
    conn.close()


def test_the_whole_delete_and_recover_cycle_ends_summarisable(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    meetings.sync(conn, [{"id": "m1", "title": "Pricing review", "date": "2026-08-20", "attendees": ["Joan", "David"], "url": "https://circleback.ai/meetings/m1"}])
    _mark_missing(conn, tmp_path)
    meetings.sync(conn, [{"id": "m1", "title": "Pricing review", "date": "2026-08-20", "attendees": ["Joan", "David"], "url": "https://circleback.ai/meetings/m1"}])

    batch = meetings.pull(conn)

    assert [m["meeting_id"] for m in batch] == ["m1"]
    assert "raise the floor price" in batch[0]["transcript"]
    conn.close()
