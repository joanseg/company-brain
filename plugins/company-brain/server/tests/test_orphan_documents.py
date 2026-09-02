"""A document that is never eligible for extraction is not an orphan.

Transcripts live in a source with `enrich: false` precisely so they are never
entity-extracted. Counting them as documents with no entities puts a permanent
red flag on the dashboard behind a /brain-enrich button that cannot clear it,
and crowds real orphans out of /brain-dream's capped list.
"""
from company_brain import dream, views


def _seed(conn):
    conn.execute("INSERT INTO sources(id,name,root,weight,added_at,enrich) "
                 "VALUES(1,'notes','/tmp/n',1.0,'2026-01-01',1)")
    conn.execute("INSERT INTO sources(id,name,root,weight,added_at,enrich) "
                 "VALUES(2,'meeting-transcripts','/tmp/t',0.4,'2026-01-01',0)")
    rows = [
        (1, 1, 'memory/enriched.md', 'Enriched'),      # has a mention
        (2, 1, 'memory/orphan.md', 'Genuine orphan'),  # eligible, but has none
        (3, 2, 'meeting-transcripts/a.md', 'Transcript'),
    ]
    for doc_id, source_id, rel_path, title in rows:
        conn.execute("INSERT INTO documents(id,source_id,rel_path,title,sha,mtime,doc_date) "
                     "VALUES(?,?,?,?,?,0,'2026-05-01')", (doc_id, source_id, rel_path, title, rel_path))
        conn.execute("INSERT INTO chunks(id,doc_id,ordinal,heading_path,line_start,text,sha) "
                     "VALUES(?,?,0,'H',1,'body',?)", (doc_id, doc_id, 'sha-%d' % doc_id))
    conn.execute("INSERT INTO entities(id,norm,name,type,description) "
                 "VALUES(1,'acme','Acme','organisation','An org.')")
    conn.execute("INSERT INTO mentions(chunk_id,entity_id,weight) VALUES(1,1,1.0)")
    conn.commit()
    return conn


def test_overview_counts_only_documents_that_could_have_entities(empty_conn):
    _seed(empty_conn)
    assert views.overview(empty_conn)["health"]["documents_without_entities"] == 1


def test_dream_reports_the_real_orphan_and_not_the_transcript(empty_conn):
    _seed(empty_conn)
    orphans = dream.report(empty_conn)["documents_without_entities"]
    assert [o["rel_path"] for o in orphans] == ["memory/orphan.md"]
