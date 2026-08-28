"""Dense retrieval against locally computed multilingual embeddings.

The embedding model lives in a separate uv project so the MCP server itself
boots without torch. If that project has not been set up, every function here
degrades to "no dense signal" rather than failing — lexical and graph
retrieval stay fully usable.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
EMBED_DIR = PLUGIN_ROOT / "embed"

_worker: subprocess.Popen | None = None
_worker_error: str | None = None
_matrix: tuple[list[str], np.ndarray] | None = None


def available() -> bool:
    return (EMBED_DIR / "worker.py").is_file() and _worker_error is None


def _start() -> subprocess.Popen | None:
    global _worker, _worker_error
    if _worker is not None and _worker.poll() is None:
        return _worker
    if not (EMBED_DIR / "worker.py").is_file():
        _worker_error = "embed project missing"
        return None
    env = dict(os.environ)
    env["COMPANY_BRAIN_EMBED_MODEL"] = os.getenv("COMPANY_BRAIN_EMBED_MODEL", "BAAI/bge-m3")
    try:
        process = subprocess.Popen(
            ["uv", "run", "--project", str(EMBED_DIR), "python", str(EMBED_DIR / "worker.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env=env,
        )
    except (FileNotFoundError, OSError) as exc:
        _worker_error = "cannot launch embed worker: %s" % exc
        return None
    handshake = process.stdout.readline()
    try:
        if not json.loads(handshake or "{}").get("ready"):
            raise ValueError("worker did not report ready")
    except (json.JSONDecodeError, ValueError) as exc:
        _worker_error = "embed worker failed to start (%s) — run /brain-setup" % exc
        process.kill()
        return None
    _worker = process
    return process


def embed(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch, or None when the dense signal is unavailable."""
    if not texts:
        return []
    process = _start()
    if process is None:
        return None
    out: list[list[float]] = []
    for start in range(0, len(texts), 256):
        payload = json.dumps({"texts": texts[start:start + 256]})
        process.stdin.write(payload + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline() or "{}")
        if "vectors" not in response:
            global _worker_error
            _worker_error = response.get("error", "embed worker returned no vectors")
            return None
        out.extend(response["vectors"])
    return out


def unavailable_reason() -> str | None:
    return _worker_error


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

def backfill(conn: sqlite3.Connection, progress=None) -> dict:
    """Embed every chunk text that has no vector yet."""
    pending = [r["sha"] for r in conn.execute(
        "SELECT DISTINCT sha FROM chunks WHERE sha NOT IN (SELECT sha FROM vectors)")]
    if not pending:
        return {"embedded": 0, "remaining": 0}
    done = 0
    for start in range(0, len(pending), 256):
        batch = pending[start:start + 256]
        texts = [conn.execute(
            "SELECT text FROM chunks WHERE sha = ? LIMIT 1", (s,)).fetchone()["text"] for s in batch]
        vectors = embed(texts)
        if vectors is None:
            return {"embedded": done, "remaining": len(pending) - done,
                    "error": unavailable_reason() or "embedding unavailable"}
        conn.executemany(
            "INSERT OR REPLACE INTO vectors(sha, dim, vec) VALUES(?,?,?)",
            [(s, len(v), np.asarray(v, dtype=np.float32).tobytes()) for s, v in zip(batch, vectors)],
        )
        conn.commit()
        done += len(batch)
        if progress:
            progress(done, len(pending))
    _invalidate()
    return {"embedded": done, "remaining": 0}


def _invalidate() -> None:
    global _matrix
    _matrix = None


def matrix(conn: sqlite3.Connection) -> tuple[list[str], np.ndarray] | None:
    """All stored vectors as one array, cached for the life of the process."""
    global _matrix
    if _matrix is not None:
        return _matrix
    rows = conn.execute("SELECT sha, dim, vec FROM vectors").fetchall()
    if not rows:
        return None
    dim = rows[0]["dim"]
    rows = [r for r in rows if r["dim"] == dim]
    shas = [r["sha"] for r in rows]
    array = np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(len(rows), dim)
    _matrix = (shas, array)
    return _matrix


def search(conn: sqlite3.Connection, question: str, limit: int = 200) -> list[tuple[int, float]]:
    """Return (chunk_id, cosine) with higher meaning better."""
    store = matrix(conn)
    if store is None:
        return []
    vectors = embed([question])
    if not vectors:
        return []
    shas, array = store
    scores = array @ np.asarray(vectors[0], dtype=np.float32)
    top = np.argsort(-scores)[:limit]
    by_sha = {shas[int(i)]: float(scores[int(i)]) for i in top}
    if not by_sha:
        return []
    placeholders = ",".join("?" * len(by_sha))
    rows = conn.execute(
        f"SELECT id, sha FROM chunks WHERE sha IN ({placeholders})", tuple(by_sha)).fetchall()
    return sorted(((r["id"], by_sha[r["sha"]]) for r in rows), key=lambda p: -p[1])[:limit]


def shutdown() -> None:
    global _worker
    if _worker is not None and _worker.poll() is None:
        try:
            _worker.stdin.write(json.dumps({"stop": True}) + "\n")
            _worker.stdin.flush()
            _worker.wait(timeout=5)
        except Exception:
            _worker.kill()
    _worker = None
