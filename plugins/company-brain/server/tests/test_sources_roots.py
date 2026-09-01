"""Roots inside the project are stored relative, so the config survives a move."""
import json
import shutil

from company_brain import db, meetings


def _written(name: str) -> str:
    config = json.loads(db.sources_path().read_text())["sources"]
    return [s for s in config if s["name"] == name][0]["root"]


def test_round_trip_keeps_the_anchor_root_relative(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path))
    anchor = db.load_sources()[0]["name"]
    meetings.ensure_sources()          # load → save round trip

    assert _written(anchor) == "."
    assert _written(meetings.TRANSCRIPT_SOURCE) == meetings.TRANSCRIPT_DIR


def test_an_out_of_tree_source_keeps_its_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(tmp_path / "project"))
    elsewhere = tmp_path / "elsewhere"
    sources = db.load_sources()
    sources.append({"name": "elsewhere", "root": elsewhere, "weight": 1.0,
                    "exclude": [], "enrich": True})
    db.save_sources(sources)

    assert _written("elsewhere") == str(elsewhere)
    assert db.load_sources()[1]["root"] == elsewhere


def test_sources_follow_the_project_when_it_moves(tmp_path, monkeypatch):
    """The regression: an absolute anchor root would resolve to the old copy,
    so notes would be written outside the repo and the real corpus would drop
    out of the index without an error anywhere.
    """
    original = tmp_path / "before"
    original.mkdir()
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(original))
    meetings.ensure_sources()

    moved = tmp_path / "after"
    shutil.copytree(original, moved)
    monkeypatch.setenv("COMPANY_BRAIN_PROJECT_DIR", str(moved))

    roots = {s["name"]: s["root"] for s in db.load_sources()}
    assert roots[meetings.TRANSCRIPT_SOURCE] == moved / meetings.TRANSCRIPT_DIR
    anchor = db.load_sources()[0]
    assert anchor["root"] == moved
