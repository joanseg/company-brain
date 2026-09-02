"""Pending meetings surface in status alongside pending enrichment."""
import re
from pathlib import Path

from company_brain import db, indexer, meetings

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "staleness.sh"


def _hook_query() -> str:
    """The SessionStart hook's own SQL, run here so the shell need not be.

    A leased row is a meeting whose summary was handed out and never came back;
    at session start no lease is legitimately in flight, so the nag must see it.
    """
    match = re.search(r'"(SELECT COUNT\(\*\) FROM meeting_queue[^"]*)"', HOOK.read_text())
    assert match, "staleness.sh no longer contains a meeting_queue count"
    return match.group(1)


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


def test_the_session_hook_counts_a_stranded_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    conn = db.connect()
    for meeting_id, status in (("m1", "pending"), ("m2", "leased"), ("m3", "done")):
        conn.execute(
            "INSERT INTO meeting_queue(meeting_id, title, held_at, rel_path, status, updated_at) "
            "VALUES(?,?,'2026-08-20',?,?,?)",
            (meeting_id, meeting_id, "meeting-transcripts/%s.md" % meeting_id,
             status, meetings._now()))
    conn.commit()

    assert conn.execute(_hook_query()).fetchone()[0] == 2
    conn.close()
