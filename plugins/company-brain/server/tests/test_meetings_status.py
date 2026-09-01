"""Pending meetings surface in status alongside pending enrichment."""
from company_brain import db, indexer, meetings


def test_status_counts_pending_meetings(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    conn = db.connect()
    conn.execute(
        "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
        "VALUES('m1','A','2026-08-20','meeting-transcripts/a.md','pending',?)", (meetings._now(),))
    conn.execute(
        "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
        "VALUES('m2','B','2026-08-21','meeting-transcripts/b.md','done',?)", (meetings._now(),))
    conn.commit()

    assert indexer.status(conn)["meetings_pending"] == 1
    conn.close()
