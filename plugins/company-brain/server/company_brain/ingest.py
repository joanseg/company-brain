"""Walk source roots, parse markdown, and cut it into retrievable chunks.

Two metadata conventions live side by side and both are supported:

  * YAML frontmatter
  * an H1 followed by `**Key:** value` lines, for corpora that carry no
    frontmatter at all
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

CHUNK_WORDS = 240
CHUNK_OVERLAP = 36
CHUNK_MIN = 18

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TABLE_RE = re.compile(r"^\s*\|")
CODE_RE = re.compile(r"```.*?```", re.S)
BOLD_META_RE = re.compile(r"^\s*\*\*(?P<key>[A-Za-zÀ-ÿ ]{2,30}):?\*\*\s*:?\s*(?P<val>.+?)\s*$")

FIELD_RE = re.compile(
    r"^\s*[-*]\s+"
    r"(?:\[\[(?P<subj>[^\]|#]+)(?:\|[^\]]*)?\]\]\s+)?"
    r"(?P<pred>[A-Za-z][\w-]*)::\s*(?P<val>.+?)\s*$"
)
SINCE_RE = re.compile(r"\bsince:\s*([0-9]{4}(?:-[0-9]{2})?(?:-[0-9]{2})?)")
UNTIL_RE = re.compile(r"\buntil:\s*([0-9]{4}(?:-[0-9]{2})?(?:-[0-9]{2})?)")

# Predicates that hold exactly one value at a time. These are the ones that go
# stale and contradict; everything else is free-form.
FUNCTIONAL = {
    "role", "employer", "owner", "status", "location", "stage",
    "instrument", "branch", "lead", "phase", "tier",
}

# Metadata keys worth lifting out of the loose bold-line convention. Spanish
# variants are included because mixed-language corpora are common.
META_KEYS = {
    "date": "date", "fecha": "date", "last updated": "date", "prepared": "date",
    "owner": "owner", "prepared by": "owner", "author": "owner",
    "status": "status", "estado": "status",
    "participants": "participants", "participantes": "participants",
    "attendees": "participants", "meeting participants": "participants",
    "type": "type", "tipo": "type", "format": "type",
    "source": "source", "purpose": "purpose", "audience": "audience",
    "stage": "stage", "subject": "subject",
}

MONTHS = {
    "jan": 1, "january": 1, "ene": 1, "enero": 1,
    "feb": 2, "february": 2, "febrero": 2,
    "mar": 3, "march": 3, "marzo": 3,
    "apr": 4, "april": 4, "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "june": 6, "junio": 6,
    "jul": 7, "july": 7, "julio": 7,
    "aug": 8, "august": 8, "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "september": 9, "septiembre": 9,
    "oct": 10, "october": 10, "octubre": 10,
    "nov": 11, "november": 11, "noviembre": 11,
    "dec": 12, "december": 12, "dic": 12, "diciembre": 12,
}
MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-_/](\d{1,2})[-_/](\d{1,2})(?!\d)")
DMY_RE = re.compile(rf"(?<!\d)(\d{{1,2}})[-_ ]({MONTH_ALT})[a-z]*[-_ ,]+(20\d{{2}})", re.I)
MDY_RE = re.compile(rf"\b({MONTH_ALT})[a-z]*[-_ ]+(\d{{1,2}})(?:st|nd|rd|th)?[-_ ,]+(20\d{{2}})", re.I)
MY_RE = re.compile(rf"\b({MONTH_ALT})[a-z]*[-_ ]+(20\d{{2}})\b", re.I)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    return re.sub(r"[\s_]+", "-", value).strip("-")


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML subset: `key: value`, `key: [a, b]`, and `- item` blocks."""
    match = FM_RE.match(text)
    if not match:
        return {}, text
    meta: dict = {}
    key = None
    for raw in match.group(1).splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(line.lstrip()[2:].strip().strip("\"'"))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            meta[key] = [p.strip().strip("\"'") for p in inner.split(",") if p.strip()] if inner else []
        else:
            meta[key] = value.strip("\"'")
    return meta, text[match.end():]


def parse_bold_meta(body: str) -> dict:
    """Lift `**Owner:** …` style metadata out of the document preamble."""
    meta: dict = {}
    for line in body.splitlines()[:14]:
        if line.startswith("#"):
            continue
        match = BOLD_META_RE.match(line)
        if not match:
            continue
        key = META_KEYS.get(match.group("key").strip().lower().rstrip(":"))
        if key and key not in meta:
            meta[key] = re.sub(r"\*\*", "", match.group("val")).strip()
    return meta


def parse_date(*candidates: str) -> date | None:
    """Best-effort date from the formats real-world markdown actually uses.

    A date after today is a deadline or a planned milestone, never when the
    document was written, so it is skipped in favour of the next candidate.
    """
    today = date.today()

    def keep(value: date) -> date | None:
        return value if value <= today else None

    for text in candidates:
        if not text:
            continue
        for pattern, order in ((ISO_DATE_RE, "ymd"), (DMY_RE, "dmy"), (MDY_RE, "mdy")):
            match = pattern.search(text)
            if not match:
                continue
            try:
                if order == "ymd":
                    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
                elif order == "dmy":
                    day, month, year = int(match.group(1)), MONTHS[match.group(2).lower()], int(match.group(3))
                else:
                    month, day, year = MONTHS[match.group(1).lower()], int(match.group(2)), int(match.group(3))
                found = keep(date(year, month, day))
                if found:
                    return found
                continue
            except (ValueError, KeyError):
                continue
        match = MY_RE.search(text)
        if match:
            try:
                found = keep(date(int(match.group(2)), MONTHS[match.group(1).lower()], 1))
                if found:
                    return found
            except (ValueError, KeyError):
                continue
    return None


def parse_assertions(body: str) -> list[dict]:
    """Dataview-style `- predicate:: value since:… until:…` lines."""
    out = []
    for number, line in enumerate(body.splitlines(), 1):
        match = FIELD_RE.match(line)
        if not match:
            continue
        predicate = match.group("pred").lower()
        rest = match.group("val")
        since = SINCE_RE.search(rest)
        until = UNTIL_RE.search(rest)
        out.append({
            "line": number,
            "subject": slug(match.group("subj")) if match.group("subj") else "",
            "predicate": predicate,
            "value": SINCE_RE.sub("", UNTIL_RE.sub("", rest)).strip(" .;·"),
            "since": since.group(1) if since else None,
            "until": until.group(1) if until else None,
            "functional": predicate in FUNCTIONAL,
        })
    return out


# ---------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------

def heading_chunks(body: str) -> list[tuple[str, int, str]]:
    """Split on headings, then pack to CHUNK_WORDS without ever cutting a table."""
    sections: list[tuple[str, int, list[str]]] = []
    headings: list[str] = []
    buffer: list[str] = []
    start = 1
    for number, line in enumerate(body.splitlines(), 1):
        match = HEADING_RE.match(line)
        if match:
            if buffer:
                sections.append((" › ".join(headings) or "Document", start, buffer))
            level = len(match.group(1))
            headings = headings[: level - 1] + [match.group(2).strip().rstrip("#").strip()]
            buffer, start = [], number + 1
        else:
            buffer.append(line)
    if buffer:
        sections.append((" › ".join(headings) or "Document", start, buffer))

    out: list[tuple[str, int, str]] = []
    for heading, line_start, lines in sections:
        packed: list[str] = []
        pack_line = line_start
        count = 0
        for offset, line in enumerate(lines):
            words = line.split()
            if count and count + len(words) > CHUNK_WORDS and not TABLE_RE.match(line):
                out.append((heading, pack_line, " ".join(packed)))
                overlap = packed[-CHUNK_OVERLAP:] if CHUNK_OVERLAP else []
                packed, count = list(overlap), len(overlap)
                pack_line = line_start + offset
            packed.extend(words)
            count += len(words)
        if count >= CHUNK_MIN or (packed and not out):
            out.append((heading, pack_line, " ".join(packed)))
        elif packed and out:
            heading_prev, line_prev, text_prev = out[-1]
            out[-1] = (heading_prev, line_prev, text_prev + " " + " ".join(packed))
    return [(h, l, t) for h, l, t in out if t.strip()]


# ---------------------------------------------------------------------------
# walking
# ---------------------------------------------------------------------------

def walk(root: Path, exclude: list[str]) -> list[Path]:
    blocked = set(exclude)
    found = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        if set(path.relative_to(root).parts[:-1]) & blocked:
            continue
        found.append(path)
    return found


def read_document(root: Path, path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(text)
    clean = CODE_RE.sub("", body)
    title = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), path.stem)
    meta = {**parse_bold_meta(body), **{k: v for k, v in meta.items() if v not in ("", [], None)}}

    mtime = path.stat().st_mtime
    modified = datetime.fromtimestamp(mtime, timezone.utc).date()
    when = parse_date(str(meta.get("date") or ""), path.name)
    if when is None:
        # Prose mentions a lot of dates. Only trust one from the body when it is
        # plausibly about this document rather than about history it describes.
        scanned = parse_date(body[:1200])
        when = scanned if scanned and (modified - scanned).days < 900 else None
    aliases = {slug(title), slug(path.stem)}
    raw_aliases = meta.get("aliases") or []
    aliases.update(slug(a) for a in ([raw_aliases] if isinstance(raw_aliases, str) else raw_aliases))
    if meta.get("name"):
        aliases.add(slug(str(meta["name"])))

    return {
        "rel_path": str(path.relative_to(root)).replace(os.sep, "/"),
        "title": title,
        "sha": sha(text),
        "mtime": mtime,
        "doc_date": (when or datetime.fromtimestamp(mtime, timezone.utc).date()).isoformat(),
        "meta": meta,
        "aliases": sorted(a for a in aliases if len(a) > 1),
        "links": sorted({slug(x) for x in LINK_RE.findall(clean) if slug(x)}),
        "superseded_by": meta.get("superseded_by") or (
            "self" if str(meta.get("status", "")).lower() == "superseded" else None),
        "assertions": parse_assertions(clean),
        "chunks": heading_chunks(body),
    }
