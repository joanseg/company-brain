"""Fetching chosen transcripts, writing them, and queueing them for summarising."""
import json

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
    assert result == {"synced": [], "skipped": [], "paths": [], "not_returned": []}
    conn.close()


def _must_not_call():
    raise AssertionError("_call must not run when no meetings are chosen")


def test_sync_registers_the_transcripts_source(tmp_path, monkeypatch):
    """Guards the `ensure_sources()` call itself — deleting it must not double-index
    every transcript into the anchor at full weight with enrichment on.
    """
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)
    meetings.sync(conn, ["m1"])

    config = json.loads(db.sources_path().read_text())["sources"]
    names = [s["name"] for s in config]
    assert meetings.TRANSCRIPT_SOURCE in names
    assert meetings.TRANSCRIPT_DIR in config[0]["exclude"]
    conn.close()


def test_three_meetings_same_date_and_title_get_distinct_files(tmp_path, monkeypatch):
    triple = {"meetings": [
        {**TRANSCRIPTS["meetings"][0], "id": "m1"},
        {**TRANSCRIPTS["meetings"][0], "id": "m2"},
        {**TRANSCRIPTS["meetings"][0], "id": "m3"},
    ]}
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: triple)
    result = meetings.sync(conn, ["m1", "m2", "m3"])

    assert len(result["paths"]) == 3
    assert len(set(result["paths"])) == 3
    folder = tmp_path / meetings.TRANSCRIPT_DIR
    assert len(list(folder.glob("*.md"))) == 3
    conn.close()


def test_mixed_batch_of_held_and_new(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)
    meetings.sync(conn, ["m1"])

    second_meeting = {**TRANSCRIPTS["meetings"][0], "id": "m2", "name": "Roadmap sync"}
    seen = {}

    def fake_call(tool, args, opener=None):
        seen["ids"] = args["meetingIds"]
        return {"meetings": [second_meeting]}

    monkeypatch.setattr(meetings, "_call", fake_call)
    result = meetings.sync(conn, ["m1", "m2"])

    assert result["synced"] == ["m2"]
    assert result["skipped"] == ["m1"]
    assert len(result["paths"]) == 1
    assert seen["ids"] == ["m2"]
    conn.close()


def test_partial_response_ignores_missing_and_idless_meetings(tmp_path, monkeypatch):
    """"m2" is requested but never comes back; the one meeting that does come
    back has no id. Neither may leave a file, a row, or a `synced` entry.
    """
    idless = {**TRANSCRIPTS["meetings"][0], "id": ""}
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: {"meetings": [idless]})

    result = meetings.sync(conn, ["m1", "m2"])

    assert result["synced"] == []
    assert result["skipped"] == []
    assert result["paths"] == []
    # Both were requested and neither landed: the spec promises the user is told
    # which ids did not make it, so they can re-select rather than assume.
    assert result["not_returned"] == ["m1", "m2"]
    folder = tmp_path / meetings.TRANSCRIPT_DIR
    assert list(folder.glob("*.md")) == []
    assert conn.execute("SELECT COUNT(*) FROM meeting_queue").fetchone()[0] == 0
    conn.close()


def test_duplicate_id_in_one_response_is_a_noop_not_a_crash(tmp_path, monkeypatch):
    """Regression for the reviewer's finding: a response repeating the same id
    used to write two files and then crash on the second INSERT.
    """
    duplicated = {"meetings": [TRANSCRIPTS["meetings"][0], TRANSCRIPTS["meetings"][0]]}
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: duplicated)

    result = meetings.sync(conn, ["m1"])

    assert result["synced"] == ["m1"]
    assert len(result["paths"]) == 1
    folder = tmp_path / meetings.TRANSCRIPT_DIR
    assert len(list(folder.glob("*.md"))) == 1
    assert conn.execute("SELECT COUNT(*) FROM meeting_queue").fetchone()[0] == 1
    conn.close()


def test_a_partial_response_names_the_ids_that_did_not_arrive(tmp_path, monkeypatch):
    """Two chosen, one returned. The one that did not come back must be named,
    not silently dropped from a result that otherwise reads as a success.
    """
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)

    result = meetings.sync(conn, ["m1", "m2"])

    assert result["synced"] == ["m1"]
    assert result["not_returned"] == ["m2"]
    conn.close()


def test_nothing_is_flagged_when_every_chosen_meeting_arrives(tmp_path, monkeypatch):
    conn = _conn(tmp_path, monkeypatch)
    monkeypatch.setattr(meetings, "_call", lambda tool, args, opener=None: TRANSCRIPTS)
    assert meetings.sync(conn, ["m1"])["not_returned"] == []
    conn.close()
