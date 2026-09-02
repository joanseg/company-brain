"""SQLite store for the company brain.

Markdown is the source of truth. Everything here is derived and can be deleted
and rebuilt. Expensive work (embeddings, LLM extraction) is keyed by content
sha so a re-index only touches what actually changed.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .ingest import slug

STATE_DIRNAME = ".company-brain"
DB_FILENAME = "brain.db"
SOURCES_FILENAME = "sources.json"

DEFAULT_EXCLUDES = [
    ".git", ".versions", ".obsidian", "node_modules", "__pycache__",
    ".company-brain", ".next", "_to_delete", ".venv",
]

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sources (
    id       INTEGER PRIMARY KEY,
    name     TEXT UNIQUE NOT NULL,
    root     TEXT NOT NULL,
    weight   REAL NOT NULL DEFAULT 1.0,
    enrich   INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    rel_path      TEXT NOT NULL,
    title         TEXT NOT NULL,
    sha           TEXT NOT NULL,
    mtime         REAL NOT NULL,
    doc_date      TEXT,
    meta_json     TEXT NOT NULL DEFAULT '{}',
    superseded_by TEXT,
    UNIQUE (source_id, rel_path)
);

CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY,
    doc_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal      INTEGER NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    line_start   INTEGER NOT NULL DEFAULT 1,
    text         TEXT NOT NULL,
    sha          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS chunks_sha ON chunks(sha);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, title, heading_path, tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS vectors (
    sha TEXT PRIMARY KEY,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY,
    norm        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'concept',
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias     TEXT NOT NULL,
    PRIMARY KEY (alias, entity_id)
);

CREATE TABLE IF NOT EXISTS mentions (
    chunk_id  INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    weight    REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (chunk_id, entity_id)
);

CREATE TABLE IF NOT EXISTS relations (
    id             INTEGER PRIMARY KEY,
    src            INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dst            INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    type           TEXT NOT NULL DEFAULT 'related',
    description    TEXT NOT NULL DEFAULT '',
    weight         REAL NOT NULL DEFAULT 1.0,
    evidence_chunk INTEGER,
    UNIQUE (src, dst, type)
);

CREATE TABLE IF NOT EXISTS edges (
    src    TEXT NOT NULL,
    dst    TEXT NOT NULL,
    kind   TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (src, dst, kind)
);

CREATE TABLE IF NOT EXISTS communities (
    id        INTEGER PRIMARY KEY,
    level     INTEGER NOT NULL,
    parent_id INTEGER
);

CREATE TABLE IF NOT EXISTS community_members (
    community_id INTEGER NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    entity_id    INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (community_id, entity_id)
);

CREATE TABLE IF NOT EXISTS community_summaries (
    community_id INTEGER PRIMARY KEY REFERENCES communities(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    rating       REAL NOT NULL DEFAULT 5.0,
    sha          TEXT NOT NULL,
    vec          BLOB
);

CREATE TABLE IF NOT EXISTS assertions (
    id         INTEGER PRIMARY KEY,
    doc_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    line       INTEGER NOT NULL,
    subject    TEXT NOT NULL,
    predicate  TEXT NOT NULL,
    value      TEXT NOT NULL,
    since      TEXT,
    until      TEXT,
    functional INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS enrich_queue (
    sha        TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS enrich_status ON enrich_queue(status);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_queue (
    meeting_id TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    held_at    TEXT,
    rel_path   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meeting_status ON meeting_queue(status);
"""


def project_dir() -> Path:
    """The repo the brain is anchored to."""
    raw = os.getenv("COMPANY_BRAIN_PROJECT_DIR") or os.getenv("CLAUDE_PROJECT_DIR")
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()


def state_dir() -> Path:
    return project_dir() / STATE_DIRNAME


def connect() -> sqlite3.Connection:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(directory / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive column migrations for databases built by an earlier version."""
    for table, column, decl in (("community_summaries", "vec", "BLOB"),
                                ("sources", "enrich", "INTEGER NOT NULL DEFAULT 1")):
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


# ---------------------------------------------------------------------------
# sources config — the one file here that is not disposable
# ---------------------------------------------------------------------------

def sources_path() -> Path:
    return state_dir() / SOURCES_FILENAME


def default_sources() -> dict:
    """The repo you installed into, named after itself. Add more with /brain-source."""
    return {"sources": [{
        "name": slug(project_dir().name) or "project",
        "root": ".",
        "weight": 1.0,
        # Tooling directories, not knowledge, wherever this is installed.
        "exclude": [".claude", ".claude-plugin"],
    }]}


def load_sources() -> list[dict]:
    path = sources_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(default_sources(), indent=2) + "\n", encoding="utf-8")
    config = json.loads(path.read_text(encoding="utf-8"))
    resolved = []
    for entry in config.get("sources", []):
        root = Path(entry["root"]).expanduser()
        if not root.is_absolute():
            root = (project_dir() / root).resolve()
        resolved.append({
            "name": entry["name"],
            "root": root,
            "weight": float(entry.get("weight", 1.0)),
            "enrich": bool(entry.get("enrich", True)),
            "exclude": DEFAULT_EXCLUDES + list(entry.get("exclude", [])),
        })
    return resolved


def anchor_source() -> dict:
    """The first configured source. Captured notes land here unless told otherwise."""
    sources = load_sources()
    if not sources:
        raise ValueError("No sources configured. Add one with /brain-source.")
    return sources[0]


def _stored_root(root) -> str:
    """A root inside the project is stored relative to it, so `sources.json`
    survives the repo being moved or re-cloned. `load_sources()` re-resolves it.
    An absolute root written here would keep pointing at the old location, and
    `capture.add()` would recreate that directory and write notes into it.
    """
    path = Path(root).expanduser()
    if not path.is_absolute():
        return str(root)
    try:
        return str(path.relative_to(project_dir()))
    except ValueError:
        return str(path)


def save_sources(sources: list[dict]) -> None:
    payload = {"sources": [
        {
            "name": s["name"],
            "root": _stored_root(s["root"]),
            "weight": s["weight"],
            "enrich": bool(s.get("enrich", True)),
            "exclude": [e for e in s.get("exclude", []) if e not in DEFAULT_EXCLUDES],
        }
        for s in sources
    ]}
    sources_path().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
