"""Command line entry point — the same engine the MCP server exposes.

    uv run --project server -m company_brain.cli status
    uv run --project server -m company_brain.cli index
    uv run --project server -m company_brain.cli search "question" --explain
"""

from __future__ import annotations

import argparse
import json
import sys

from . import db, dense, graph, indexer, search


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="company-brain", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("index", help="Refresh the index from disk")
    build.add_argument("--no-embed", action="store_true", help="Skip the dense backfill")

    sub.add_parser("status", help="Index health and source roots")
    sub.add_parser("sources", help="Show configured sources")

    query = sub.add_parser("search", help="Retrieve cited evidence")
    query.add_argument("question")
    query.add_argument("--mode", choices=("local", "global"), default="local")
    query.add_argument("--limit", type=int, default=8)
    query.add_argument("--explain", action="store_true")
    query.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    conn = db.connect()

    if args.command == "sources":
        for source in db.load_sources():
            print("%-12s %-8s %s" % (source["name"], source["weight"], source["root"]))
        return 0

    if args.command == "status":
        print(json.dumps(indexer.status(conn), indent=2))
        return 0

    if args.command == "index":
        stats = indexer.refresh(conn)
        if not args.no_embed:
            def progress(done, total):
                print("  embedded %d/%d" % (done, total), file=sys.stderr)
            stats["dense"] = dense.backfill(conn, progress)
        stats["edges_built"] = graph.rebuild(conn)
        print(json.dumps(stats, indent=2))
        return 0

    result = search.run(conn, args.question, args.limit, args.mode, args.explain)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    signals = result["signals"]
    print("signals: lexical=%s dense=%s graph=%s%s\n" % (
        signals["lexical"], signals["dense"], signals["graph"],
        "  (%s)" % signals["dense_note"] if signals.get("dense_note") else ""))
    for number, item in enumerate(result["results"], 1):
        print("%d. [%s] %s:%d — %s" % (
            number, item["source"], item["path"], item["line"], item["heading"]))
        print("   %s" % item["text"][:400].replace("\n", " "))
        if args.explain:
            print("   score=%s %s" % (item["score"], json.dumps(item["signals"], sort_keys=True)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
