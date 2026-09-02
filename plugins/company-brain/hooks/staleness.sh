#!/usr/bin/env bash
# Report, never act: say whether the working tree has moved since the last index.
# Compares against a marker file rather than a timestamp string, because BSD and
# GNU find disagree about every date format.
set -u
STATE="${CLAUDE_PROJECT_DIR:-$PWD}/.company-brain"
MARKER="$STATE/.indexed"
[ -f "$STATE/brain.db" ] || exit 0
[ -f "$MARKER" ] || exit 0
command -v sqlite3 >/dev/null 2>&1 || exit 0

stale=0
while IFS= read -r root; do
  [ -d "$root" ] || continue
  newer=$(find "$root" -name '*.md' -newer "$MARKER" \
            -not -path '*/.versions/*' -not -path '*/.git/*' \
            -not -path '*/node_modules/*' -not -path '*/.company-brain/*' \
            -not -path '*/plugins/*' 2>/dev/null | wc -l | tr -d ' ')
  stale=$((stale + newer))
done < <(sqlite3 "$STATE/brain.db" "SELECT root FROM sources;" 2>/dev/null)

if [ "$stale" -gt 0 ]; then
  echo "company-brain: ${stale} markdown file(s) changed since the last index — run /brain-index"
fi

# Meetings fetched but not yet summarised. 'leased' counts too: a session is
# starting, so no lease is legitimately in flight, and a meeting whose summary
# failed would otherwise be invisible to every counter. Guarded: databases
# predating this feature have no meeting_queue, and this script reports rather
# than acts.
meetings=$(sqlite3 "$STATE/brain.db" \
  "SELECT COUNT(*) FROM meeting_queue WHERE status IN ('pending','leased');" 2>/dev/null || echo 0)
if [ "${meetings:-0}" -gt 0 ]; then
  echo "company-brain: ${meetings} synced meeting(s) awaiting a summary — run /brain-meetings"
fi
exit 0
