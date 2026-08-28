from company_brain import views


def test_overview_reports_counts_and_sources(seeded):
    data = views.overview(seeded)
    assert data["empty"] is False
    assert data["counts"]["documents"] == 2
    assert data["counts"]["chunks"] == 3
    assert data["counts"]["entities"] == 3
    assert data["counts"]["communities"] == 1
    names = [s["name"] for s in data["sources"]]
    assert names == ["handbook", "notes"]
    assert data["sources"][0]["weight"] == 1.0
    assert data["health"]["enrich_pending"] == 1
    assert data["health"]["stale_files"] == 0  # no .indexed marker in the temp project dir
    assert "generated_at" in data


def test_overview_on_empty_index_says_what_to_run(empty_conn):
    data = views.overview(empty_conn)
    assert data["empty"] is True
    assert data["next"] == "/brain-index"


def test_communities_include_summary_members_and_dominant_source(seeded):
    data = views.communities(seeded)
    assert data["empty"] is False
    row = data["communities"][0]
    assert row["id"] == 1
    assert row["title"] == "Acme and Bob"
    assert row["rating"] == 8.0
    assert row["members"][0] == "Acme"          # ordered by mentions, Acme has 3
    assert row["dominant_source"] == "handbook"   # most member mentions sit in the handbook source


def test_communities_member_count_exceeds_displayed_members_when_over_cap(empty_conn):
    """The members list is capped at 20 for card readability; member_count must
    still report the true total, and the badge must use it — not len(members)."""
    conn = empty_conn
    conn.execute("INSERT INTO sources(id,name,root,weight,added_at) VALUES(1,'s','/tmp',1.0,'x')")
    conn.execute("INSERT INTO documents(id,source_id,rel_path,title,sha,mtime,doc_date) "
                 "VALUES(1,1,'a.md','A','sha',0,'2026-01-01')")
    for i in range(1, 26):
        conn.execute(
            "INSERT INTO entities(id,norm,name,type,description) VALUES(?,?,?,?,?)",
            (i, "e%d" % i, "Entity%d" % i, "concept", ""))
    conn.execute("INSERT INTO communities(id,level,parent_id) VALUES(1,0,NULL)")
    conn.executemany(
        "INSERT INTO community_members(community_id,entity_id) VALUES(1,?)",
        [(i,) for i in range(1, 26)])
    conn.execute(
        "INSERT INTO community_summaries(community_id,title,summary,rating,sha) "
        "VALUES(1,'Big community','A community with many members.',7.0,'sha')")
    conn.commit()

    data = views.communities(conn)
    row = data["communities"][0]
    assert row["member_count"] == 25
    assert len(row["members"]) == 20
    assert row["member_count"] > len(row["members"])


def test_communities_include_citations_from_evidence(seeded):
    data = views.communities(seeded)
    row = data["communities"][0]
    assert "citations" in row
    assert isinstance(row["citations"], list)


def test_communities_absent_is_not_an_error(empty_conn):
    empty_conn.execute("INSERT INTO sources(id,name,root,weight,added_at) VALUES(1,'s','/tmp',1.0,'x')")
    empty_conn.execute("INSERT INTO documents(id,source_id,rel_path,title,sha,mtime,doc_date) "
                       "VALUES(1,1,'a.md','A','sha',0,'2026-01-01')")
    empty_conn.commit()
    data = views.communities(empty_conn)
    assert data["communities"] == []
    assert data["next"] == "/brain-enrich --communities"


def test_corpus_groups_by_folder_and_flags_entity_coverage(seeded):
    data = views.corpus(seeded)
    by_path = {d["path"]: d for d in data["documents"]}
    assert by_path["memory/a.md"]["folder"] == "memory"
    assert by_path["deep/folder/b.md"]["folder"] == "deep"
    assert by_path["memory/a.md"]["has_entities"] is True
    assert by_path["memory/a.md"]["chunks"] == 2
    assert by_path["deep/folder/b.md"]["source"] == "notes"


def test_graph_returns_nodes_ranked_by_mentions(seeded):
    data = views.graph(seeded)
    assert [n["name"] for n in data["nodes"]] == ["Acme", "Bob", "Edge"]
    assert data["nodes"][0]["mentions"] == 3
    assert data["nodes"][0]["type"] == "organisation"


def test_graph_drops_edges_whose_endpoint_was_cut(seeded):
    data = views.graph(seeded, limit=2)          # keeps Acme and Bob, cuts Edge
    assert len(data["nodes"]) == 2
    pairs = {(e["src"], e["dst"]) for e in data["edges"]}
    assert pairs == {(1, 2)}                      # Acme->Edge is gone
    assert data["limit"] == 2
    assert data["truncated"] is True


def test_graph_reports_when_nothing_was_cut(seeded):
    data = views.graph(seeded, limit=400)
    assert data["truncated"] is False


def test_suggestions_are_derived_from_real_content(seeded):
    data = views.suggestions(seeded)
    texts = [q["text"] for q in data["questions"]]
    assert any("Acme" in t for t in texts)             # top entity by mentions
    assert any("Acme and Bob" in t for t in texts)     # top-rated community title
    kinds = {q["kind"] for q in data["questions"]}
    assert kinds <= {"entity", "evidence", "themes"}
    assert any(c["command"] == "/brain-ask" for c in data["commands"])
    assert len(data["how_to"]) == 3


def test_suggestions_on_empty_index(empty_conn):
    assert views.suggestions(empty_conn)["empty"] is True


# --- capture / anchor source -------------------------------------------------

def test_capture_writes_to_a_renamed_anchor_without_a_folder(tmp_path, monkeypatch):
    """The anchor is whichever source comes first, not one with a particular
    name. A hardcoded name check used to make /brain-add refuse to write here."""
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    from company_brain import capture, db

    (tmp_path / ".company-brain").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".company-brain" / "sources.json").write_text(
        '{"sources": [{"name": "acme-docs", "root": ".", "weight": 1.0, "exclude": []}]}')
    conn = db.connect()
    try:
        assert db.anchor_source()["name"] == "acme-docs"
        written = capture.add(conn, "A fact worth keeping.", title="Anchor test")
        assert written["source"] == "acme-docs"
        assert (tmp_path / written["relative"]).is_file()
    finally:
        conn.close()


def test_default_source_is_named_after_the_project_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    from company_brain import db

    names = [s["name"] for s in db.load_sources()]
    # Exactly one default source, named after the directory it was installed
    # into. Guards against re-introducing a hardcoded second source.
    assert names == [db.slug(tmp_path.name)]
