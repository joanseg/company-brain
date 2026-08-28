"""FTS5 lexical retrieval.

The index does the work, so cost is proportional to matching rows rather than
to corpus size — which is the whole reason for moving off the previous
tokenise-everything-per-query design.
"""

from __future__ import annotations

import re
import sqlite3

TOKEN_RE = re.compile(r"[\w'’-]+", re.UNICODE)

STOP = set("""a an and are as at be but by for from has have if in into is it its of on or that the
their then there these this to was were what when where which who why will with your you we our not
no do does did can could should would may might must than them they i he she his her
de la el los las un una y en por para con del al que es se su sus como mas más""".split())


def terms(question: str) -> list[str]:
    found = [t.lower() for t in TOKEN_RE.findall(question)]
    kept = [t for t in found if len(t) > 1 and t not in STOP]
    return kept or found


def _match_expression(words: list[str]) -> str:
    return " OR ".join('"%s"' % w.replace('"', '') for w in words)


def search(conn: sqlite3.Connection, question: str, limit: int = 200) -> list[tuple[int, float]]:
    """Return (chunk_id, score) with higher meaning better."""
    words = terms(question)
    if not words:
        return []
    try:
        rows = conn.execute(
            "SELECT rowid AS id, bm25(chunks_fts, 1.0, 2.0, 1.5) AS rank "
            "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
            (_match_expression(words), limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    # bm25() is negative, most negative is best.
    return [(row["id"], -row["rank"]) for row in rows]
