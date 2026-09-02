"""Registering the transcripts source, and keeping the anchor out of it."""
import json

from company_brain import db, indexer, meetings


def test_registration_adds_source_and_anchor_exclude(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    result = meetings.ensure_sources()

    assert result == {"registered": True, "excluded": True}
    config = json.loads(db.sources_path().read_text())["sources"]
    transcripts = [s for s in config if s["name"] == meetings.TRANSCRIPT_SOURCE]
    assert len(transcripts) == 1
    assert transcripts[0]["enrich"] is False
    assert transcripts[0]["weight"] == meetings.TRANSCRIPT_WEIGHT
    assert meetings.TRANSCRIPT_DIR in config[0]["exclude"]


def test_registration_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    meetings.ensure_sources()
    assert meetings.ensure_sources() == {"registered": False, "excluded": False}

    config = json.loads(db.sources_path().read_text())["sources"]
    assert [s["name"] for s in config].count(meetings.TRANSCRIPT_SOURCE) == 1
    assert config[0]["exclude"].count(meetings.TRANSCRIPT_DIR) == 1


def test_transcripts_are_indexed_once_not_twice(tmp_path, monkeypatch):
    """The double-index regression. A two-segment exclude would fail this."""
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    meetings.ensure_sources()

    folder = tmp_path / meetings.TRANSCRIPT_DIR
    folder.mkdir(exist_ok=True)
    (folder / "2026-08-20-pricing.md").write_text("# Pricing\n\n## Transcript\n\nJoan: hello.\n")

    conn = db.connect()
    indexer.refresh(conn)
    owners = [r[0] for r in conn.execute(
        "SELECT s.name FROM documents d JOIN sources s ON s.id = d.source_id "
        "WHERE d.rel_path LIKE '%pricing%'")]
    assert owners == [meetings.TRANSCRIPT_SOURCE]
    conn.close()
