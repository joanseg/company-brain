"""Fixtures build a tiny brain in a temp dir, so tests never touch the real index."""
import os
import pytest


@pytest.fixture
def empty_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    from company_brain import db
    conn = db.connect()
    yield conn
    conn.close()


@pytest.fixture
def seeded(empty_conn):
    conn = empty_conn
    conn.execute("INSERT INTO sources(id,name,root,weight,added_at) VALUES(1,'handbook','/tmp/c',1.0,'2026-01-01')")
    conn.execute("INSERT INTO sources(id,name,root,weight,added_at) VALUES(2,'notes','/tmp/n',0.8,'2026-01-01')")
    conn.execute("INSERT INTO documents(id,source_id,rel_path,title,sha,mtime,doc_date) "
                 "VALUES(1,1,'memory/a.md','Alpha','sha-a',0,'2026-05-01')")
    conn.execute("INSERT INTO documents(id,source_id,rel_path,title,sha,mtime,doc_date) "
                 "VALUES(2,2,'deep/folder/b.md','Beta','sha-b',0,'2026-06-01')")
    for cid, doc, sha in ((1, 1, 'x'), (2, 1, 'y'), (3, 2, 'z')):
        conn.execute("INSERT INTO chunks(id,doc_id,ordinal,heading_path,line_start,text,sha) "
                     "VALUES(?,?,0,'H',1,'body',?)", (cid, doc, sha))
        conn.execute("INSERT INTO vectors(sha,dim,vec) VALUES(?,2,X'0000')", (sha,))
    conn.execute("INSERT INTO entities(id,norm,name,type,description) VALUES(1,'acme','Acme','organisation','An org.')")
    conn.execute("INSERT INTO entities(id,norm,name,type,description) VALUES(2,'bob','Bob','person','A person.')")
    conn.execute("INSERT INTO entities(id,norm,name,type,description) VALUES(3,'edge','Edge','concept','')")
    conn.executemany("INSERT INTO mentions(chunk_id,entity_id,weight) VALUES(?,?,1.0)",
                     [(1, 1), (2, 1), (3, 1), (1, 2), (2, 3)])
    conn.execute("INSERT INTO relations(id,src,dst,type,description,weight) "
                 "VALUES(1,1,2,'employs','Acme employs Bob',2.0)")
    conn.execute("INSERT INTO relations(id,src,dst,type,description,weight) "
                 "VALUES(2,1,3,'relates','',1.0)")
    conn.execute("INSERT INTO communities(id,level,parent_id) VALUES(1,0,NULL)")
    conn.executemany("INSERT INTO community_members(community_id,entity_id) VALUES(1,?)", [(1,), (2,)])
    conn.execute("INSERT INTO community_summaries(community_id,title,summary,rating,sha) "
                 "VALUES(1,'Acme and Bob','Acme employs Bob.',8.0,'csha')")
    conn.execute("INSERT INTO enrich_queue(sha,status,updated_at) VALUES('x','done','2026-01-01')")
    conn.execute("INSERT INTO enrich_queue(sha,status,updated_at) VALUES('y','pending','2026-01-01')")
    conn.commit()
    return conn
