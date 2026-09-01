"""Leasing transcripts to subagents and capturing what they write back."""
from datetime import datetime, timedelta, timezone

from company_brain import db, meetings


def _seed(tmp_path, monkeypatch, status="pending"):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    conn = db.connect()
    meetings.ensure_sources()
    folder = tmp_path / meetings.TRANSCRIPT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-08-20-pricing.md").write_text("# Pricing\n\n## Transcript\n\nJoan: raise it.\n")
    conn.execute(
        "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
        "VALUES('m1','Pricing review','2026-08-20','meeting-transcripts/2026-08-20-pricing.md',?,?)",
        (status, meetings._now()))
    conn.commit()
    return conn


def test_pull_leases_and_returns_the_transcript_text(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    batch = meetings.pull(conn)

    assert len(batch) == 1
    assert batch[0]["meeting_id"] == "m1"
    assert batch[0]["title"] == "Pricing review"
    assert "Joan: raise it." in batch[0]["transcript"]
    assert conn.execute(
        "SELECT status FROM meeting_queue WHERE meeting_id='m1'").fetchone()[0] == "leased"
    conn.close()


def test_pull_does_not_return_an_already_leased_meeting(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch, status="leased")
    assert meetings.pull(conn) == []
    conn.close()


def test_expired_leases_return_to_pending(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch, status="leased")
    stale = (datetime.now(timezone.utc)
             - timedelta(minutes=meetings.LEASE_MINUTES + 1)).isoformat(timespec="seconds")
    conn.execute("UPDATE meeting_queue SET updated_at = ? WHERE meeting_id='m1'", (stale,))
    conn.commit()

    assert meetings.reset_leases(conn) == 1
    assert len(meetings.pull(conn)) == 1
    conn.close()


def test_push_captures_the_summary_and_closes_the_row(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    meetings.pull(conn)

    stats = meetings.push(conn, [{
        "meeting_id": "m1",
        "summary": "The team agreed to raise the floor price from March.",
        "facts": ["action:: David to publish the new price list"],
        "related": ["pricing"],
    }])

    assert stats["captured"] == 1
    assert stats["pending"] == 0
    note = (tmp_path / stats["paths"][0]).read_text()
    assert "raise the floor price" in note
    assert "type: meeting" in note
    assert "action:: David to publish the new price list" in note
    assert conn.execute(
        "SELECT status FROM meeting_queue WHERE meeting_id='m1'").fetchone()[0] == "done"
    conn.close()


def test_push_skips_an_unknown_meeting_id(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    stats = meetings.push(conn, [{"meeting_id": "nope", "summary": "x", "facts": [], "related": []}])
    assert stats["captured"] == 0
    assert stats["skipped"] == ["nope"]
    conn.close()


def test_push_leaves_the_row_pending_when_the_summary_is_empty(tmp_path, monkeypatch):
    conn = _seed(tmp_path, monkeypatch)
    meetings.pull(conn)
    stats = meetings.push(conn, [{"meeting_id": "m1", "summary": "   ", "facts": [], "related": []}])

    assert stats["captured"] == 0
    assert stats["skipped"] == ["m1"]
    assert conn.execute(
        "SELECT status FROM meeting_queue WHERE meeting_id='m1'").fetchone()[0] == "leased"
    conn.close()
