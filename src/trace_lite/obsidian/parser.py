"""Obsidian markdown parsing: frontmatter, tags, wikilinks — with byte spans."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PARSER_NAME = "obsidian-markdown"
PARSER_VERSION = "v1"

# CRLF-tolerant: opening/closing fences and the trailing newline accept an
# optional carriage return, so CRLF notes strip frontmatter exactly like LF.
FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|\Z)", re.DOTALL)
TAG_RE = re.compile(r"(?<![\w/#])#([\w\-/]+)")
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class Span:
    start_byte: int
    end_byte: int
    text: str


@dataclass
class ParsedNote:
    path: str
    frontmatter: dict[str, list[str]]
    tags: list[str]
    wikilinks: list[tuple[str, str | None, Span]]
    sections: list[Span]
    body: str
    body_byte_offset: int
    # R02 adapter fields (defaults only; existing callers unaffected):
    frontmatter_span: Span | None = None
    body_char_offset: int = 0
    newline: str = "lf"
    parser_version: str = PARSER_VERSION


def _parse_frontmatter_block(block: str) -> dict[str, list[str]]:
    data: dict[str, list[str]] = {}
    current_key: str | None = None
    for line in block.splitlines():
        if re.match(r"^\s*-\s+", line) and current_key is not None:
            data[current_key].append(re.sub(r"^\s*-\s+", "", line).strip().strip("'\""))
            continue
        match = re.match(r"^([\w\-]+)\s*:\s*(.*)$", line)
        if not match:
            current_key = None
            continue
        key, value = match.group(1), match.group(2).strip()
        current_key = key
        if not value:
            data.setdefault(key, [])
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            data[key] = [value.strip().strip("'\"")]
    return data


def parser_version_for() -> str:
    """Extraction-run-compatible version hash for this parser (R02 records it)."""
    import hashlib

    digest = hashlib.sha256(f"{PARSER_NAME}:{PARSER_VERSION}".encode("utf-8")).hexdigest()[:16]
    return f"{PARSER_NAME}-{PARSER_VERSION}:{digest}"


def parse_note(path: str, raw: str) -> ParsedNote:
    """Parse a markdown note; all spans are UTF-8 byte offsets into `raw`.

    Byte-offset mapping (ARCHITECTURE §2):
    - ``body_byte_offset``: byte length of the stripped frontmatter block; always
      ``raw.encode()[body_byte_offset:] == body.encode()`` (verified identity,
      never assumed). For CRLF input the offset counts the ``\\r`` bytes.
    - ``frontmatter_span``: byte range of the stripped block (None when absent).
    - ``body_char_offset``: the same boundary in characters (differs from the
      byte offset past any multi-byte content before the body).
    - Whitespace trimming by callers (``body.strip()``) has no recorded mapping:
      trimmed text must never be presented as a raw-byte slice.
    """
    blob = raw.encode("utf-8")
    frontmatter: dict[str, list[str]] = {}
    body_offset = 0
    body_char_offset = 0
    frontmatter_span: Span | None = None
    match = FRONTMATTER_RE.match(raw)
    if match:
        frontmatter = _parse_frontmatter_block(match.group(1))
        body_offset = len(match.group(0).encode("utf-8"))
        body_char_offset = match.end()
        frontmatter_span = Span(0, body_offset, match.group(0))

    def span_of(m: re.Match) -> Span:
        start = len(raw[: m.start()].encode("utf-8"))
        return Span(start, start + len(m.group(0).encode("utf-8")), m.group(0))

    tags = [m.group(1) for m in TAG_RE.finditer(raw)]
    wikilinks = [(m.group(1).strip(), m.group(2), span_of(m)) for m in WIKILINK_RE.finditer(raw)]
    sections = [span_of(m) for m in HEADING_RE.finditer(raw)]
    body = raw[match.end() :] if match else raw
    _ = blob
    return ParsedNote(
        path=path, frontmatter=frontmatter, tags=tags, wikilinks=wikilinks,
        sections=sections, body=body, body_byte_offset=body_offset,
        frontmatter_span=frontmatter_span, body_char_offset=body_char_offset,
        newline="crlf" if "\r\n" in raw else "lf",
    )


def facet_hints(note: ParsedNote) -> dict[str, list[str]]:
    """Map vault structure + tags into Hearst facet hints."""
    topics = [t.replace("/", ":") for t in note.frontmatter.get("tags", [])]
    topics += [t.replace("/", ":") for t in note.tags]
    parts = note.path.replace("\\", "/").split("/")
    projects = [parts[0]] if len(parts) > 1 else []
    return {
        "Topics": sorted(set(topics)),
        "Entities": sorted({target for target, _, _ in note.wikilinks}),
        "Types": sorted(set(note.frontmatter.get("type", []))),
        "Projects": projects,
        "Sources": ["ObsidianVault"],
    }
