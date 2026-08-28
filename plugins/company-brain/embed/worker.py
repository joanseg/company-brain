"""Persistent embedding worker.

Loading a sentence-transformers model costs seconds, so the model is loaded
once and the process is kept alive by the MCP server for the life of the
session. Protocol is JSON lines on stdin/stdout:

    <- {"texts": ["…", "…"], "kind": "passage"}
    -> {"vectors": [[…], […]], "dim": 1024}

Vectors come back L2-normalised, so cosine similarity is a plain dot product.
"""

from __future__ import annotations

import json
import os
import sys

MODEL_NAME = os.getenv("COMPANY_BRAIN_EMBED_MODEL", "BAAI/bge-m3")
BATCH = 16
# bge-m3 advertises an 8192-token window. Our chunks are ~240 words, so leaving
# it at the default pads every batch to a size that stalls the Metal queue.
MAX_SEQ = int(os.getenv("COMPANY_BRAIN_EMBED_MAX_SEQ", "512"))


def pick_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def main() -> int:
    from sentence_transformers import SentenceTransformer

    device = pick_device()
    model = SentenceTransformer(MODEL_NAME, device=device)
    model.max_seq_length = MAX_SEQ
    dim = int(model.encode(["dimension probe"], normalize_embeddings=True,
                           show_progress_bar=False, convert_to_numpy=True).shape[1])
    print(json.dumps({"ready": True, "model": MODEL_NAME, "device": device, "dim": dim}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": "bad json: %s" % exc}), flush=True)
            continue
        if request.get("stop"):
            return 0
        texts = request.get("texts") or []
        try:
            vectors = model.encode(
                texts, batch_size=BATCH, normalize_embeddings=True,
                show_progress_bar=False, convert_to_numpy=True,
            )
            print(json.dumps({"vectors": [v.tolist() for v in vectors], "dim": dim}), flush=True)
        except Exception as exc:  # the worker must survive a bad batch
            print(json.dumps({"error": str(exc)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
