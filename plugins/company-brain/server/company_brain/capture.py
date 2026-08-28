"""Write new knowledge back as markdown.

Captured notes become real files in the anchor repo, because markdown in the
repo is the source of truth and the index is disposable. A note that only
existed inside the database would be invisible to every other tool.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .ingest import slug

DEFAULT_FOLDER = "memory/inbox"
CONFIDENCE = ("stated", "observed", "inferred", "draft")
GENERIC = {"company", "the-company", "note", "project", "context"}


def _safe_folder(value: str) -> Path:
    folder = Path(value.strip())
    if not value.strip() or folder.is_absolute() or ".." in folder.parts:
        raise ValueError("Folder must be a non-empty relative path inside the corpus.")
    return folder


def _title_from(content: str) -> str:
    for line in content.splitlines():
        candidate = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
        if candidate:
            return re.sub(r"\s+", " ", candidate)[:100].rstrip(" .,:;-—") or "Captured note"
    return "Captured note"


def _unique_path(root: Path, folder: Path, title: str) -> Path:
    base = "%s-%s" % (datetime.now(timezone.utc).date().isoformat(), slug(title)[:70] or "captured-note")
    candidate = root / folder / (base + ".md")
    ordinal = 2
    while candidate.exists():
        candidate = root / folder / ("%s-%d.md" % (base, ordinal))
        ordinal += 1
    return candidate


def _inferred_links(conn: sqlite3.Connection, content: str, title: str, supplied: list[str]) -> list[str]:
    """Link only to entities distinctive enough not to create noisy hubs."""
    haystack = "-" + slug(content + " " + title) + "-"
    related = {slug(item) for item in supplied if slug(item)}
    for row in conn.execute("SELECT alias FROM entity_aliases"):
        alias = row["alias"]
        if alias in GENERIC or len(alias) < 5:
            continue
        if "-" + alias + "-" in haystack:
            related.add(alias)
    return sorted(related)[:12]


def _structured_fact(raw: str) -> str:
    if "::" not in raw:
        raise ValueError("Each fact must be `predicate:: value` or `subject|predicate:: value`.")
    left, value = (part.strip() for part in raw.split("::", 1))
    if not left or not value:
        raise ValueError("Each fact needs both a predicate and a value.")
    if "|" in left:
        subject, predicate = (part.strip() for part in left.split("|", 1))
        if not subject or not predicate:
            raise ValueError("A subject fact must use `subject|predicate:: value`.")
        return "- [[%s]] %s:: %s" % (slug(subject), slug(predicate), value)
    return "- %s:: %s" % (slug(left), value)


def _md_list(values: list[str]) -> str:
    cleaned = [re.sub(r"[\[\],\n\r]", " ", v).strip() for v in values]
    return "[" + ", ".join(v for v in cleaned if v) + "]"


def add(conn: sqlite3.Connection, content: str, title: str | None = None, source: str | None = None,
        folder: str | None = None, kind: str = "note", tags: list[str] | None = None,
        aliases: list[str] | None = None, related: list[str] | None = None,
        facts: list[str] | None = None, confidence: str = "stated",
        source_url: str | None = None) -> dict:
    if not content or not content.strip():
        raise ValueError("Nothing to capture: content is empty.")
    if confidence not in CONFIDENCE:
        raise ValueError("confidence must be one of %s" % ", ".join(CONFIDENCE))

    anchor = db.anchor_source()["name"]
    source = source or anchor
    configured = {s["name"]: s for s in db.load_sources()}
    if source not in configured:
        raise ValueError("Unknown source %r. Known: %s" % (source, ", ".join(configured)))
    # Anything but the anchor is a repo the user did not install into; make the
    # destination explicit rather than guessing a folder inside someone's corpus.
    if source != anchor and not folder:
        raise ValueError("Writing to %r rather than the anchor source %r requires an "
                         "explicit folder." % (source, anchor))

    root = configured[source]["root"]
    destination = _unique_path(root, _safe_folder(folder or DEFAULT_FOLDER), title or _title_from(content))
    heading = (title or _title_from(content)).strip()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    links = _inferred_links(conn, content, heading, related or [])

    lines = [
        "---",
        "name: %s" % slug(heading),
        "type: %s" % kind,
        "aliases: %s" % _md_list([slug(heading)] + [slug(a) for a in (aliases or [])]),
        "created: %s" % now,
        "updated: %s" % now,
        "confidence: %s" % confidence,
        "tags: %s" % _md_list(sorted({slug(t) for t in (tags or ["captured"]) if slug(t)})),
        "---",
        "",
        "# %s" % heading,
        "",
        "## Notes",
        content.strip(),
    ]
    structured = [_structured_fact(f) for f in (facts or [])]
    if structured:
        lines += ["", "## Structured facts", *structured]
    lines += ["", "## Provenance", "- source:: company-brain capture", "- confidence:: %s" % confidence]
    if source_url:
        lines.append("- reference:: %s" % source_url)
    if links:
        lines.append("- related:: %s" % ", ".join("[[%s]]" % item for item in links))

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {
        "path": str(destination),
        "relative": str(destination.relative_to(root)),
        "source": source,
        "linked": links,
        "facts": structured,
    }
