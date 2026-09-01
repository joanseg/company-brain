"""A source can opt out of entity extraction while staying fully searchable."""
import json

from company_brain import db, indexer


def test_load_sources_defaults_enrich_to_true(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    sources = db.load_sources()
    assert sources[0]["enrich"] is True


def test_save_and_load_round_trips_enrich_false(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    sources = db.load_sources()
    sources.append({"name": "transcripts", "root": tmp_path / "t",
                    "weight": 0.4, "exclude": [], "enrich": False})
    db.save_sources(sources)

    written = json.loads(db.sources_path().read_text())["sources"]
    assert written[1]["enrich"] is False
    assert db.load_sources()[1]["enrich"] is False


def test_migrate_adds_enrich_column_to_existing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    conn = db.connect()
    conn.execute("DROP TABLE sources")
    conn.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, "
                 "root TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0, added_at TEXT NOT NULL)")
    conn.execute("INSERT INTO sources(name, root, weight, added_at) "
                 "VALUES('old', '/tmp/old', 1.0, '2026-01-01')")
    conn.commit()
    conn.close()

    conn = db.connect()
    assert conn.execute("SELECT enrich FROM sources WHERE name = 'old'").fetchone()[0] == 1
    conn.close()


def test_enqueue_skips_chunks_from_a_non_enriching_source(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    quiet = tmp_path / "quiet"
    quiet.mkdir()
    (quiet / "note.md").write_text("# Quiet\n\nBody text that would otherwise be extracted.\n")
    loud = tmp_path / "loud"
    loud.mkdir()
    (loud / "note.md").write_text("# Loud\n\nBody text that should be extracted.\n")

    # connect() first: it creates the state directory that save_sources writes into.
    conn = db.connect()
    db.save_sources([
        {"name": "loud", "root": loud, "weight": 1.0, "exclude": [], "enrich": True},
        {"name": "quiet", "root": quiet, "weight": 0.4, "exclude": [], "enrich": False},
    ])
    indexer.refresh(conn)

    queued = {r[0] for r in conn.execute(
        "SELECT s.name FROM enrich_queue q JOIN chunks c ON c.sha = q.sha "
        "JOIN documents d ON d.id = c.doc_id JOIN sources s ON s.id = d.source_id")}
    assert queued == {"loud"}

    searchable = {r[0] for r in conn.execute(
        "SELECT s.name FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "JOIN sources s ON s.id = d.source_id")}
    assert searchable == {"loud", "quiet"}
    conn.close()
