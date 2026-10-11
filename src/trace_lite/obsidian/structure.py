"""Cheap structure for query-native evidence (S5, contract TL-QN-2026-09-12.1).

``CHEAP_STRUCTURE_DEF`` is the pre-trial definition required by steering
line 76 / supplement B2. It names the middle ground between the dead
open-domain 14-verb regex triples (the floor to beat, NOT the structure
arm) and a local SLM on every sentence (explicitly NOT proposed here —
no false dichotomy):

- structural markdown hierarchy: sections, lists, callouts, tables,
  code fences, wikilinks — all byte-anchored;
- entity co-occurrence clusters: capitalized noun phrases, wikilink
  targets and tags co-occurring inside one structural block, with
  byte-anchored mention spans.

No verb patterns, no triples, no model calls. Everything in this module
is deterministic syntax over the raw note bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STRUCTURE_NAME = "cheap-structure"
STRUCTURE_VERSION = "v1"

#: Pre-trial definition (frozen before the S5 trial; the trial records
#: this version string with its results so a later definition change
#: cannot silently rewrite what "cheap" meant).
CHEAP_STRUCTURE_DEF: dict[str, object] = {
    "name": STRUCTURE_NAME,
    "version": STRUCTURE_VERSION,
    "block_kinds": ["section", "list", "callout", "table", "code"],
    "entity_sources": ["wikilink-target", "tag", "capitalized-phrase"],
    "cluster_scope": "one structural block",
    "explicitly_excluded": [
        "open-domain verb-triple regexes (floor arm, not structure)",
        "per-sentence local SLM (not proposed; no false dichotomy)",
        "long-range coreference / pronoun resolution (abstain)",
    ],
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])\s+\S")
_CALLOUT_RE = re.compile(r"^\s*>\s*(?:\[![^\]]*\]\s*)?\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_TAG_RE = re.compile(r"(?<![\w/#])#([\w\-/]+)")
_PHRASE_RE = re.compile(r"\b([A-Z][a-zA-Z\-]+(?:\s+[A-Z][a-zA-Z\-]+){0,2})\b")

#: Single capitalized words that are sentence scaffolding, never entities.
_PHRASE_STOP = frozenset({
    "The", "This", "That", "These", "Those", "Under", "During", "When",
    "Unless", "Only", "Without", "With", "All", "Any", "Blindly", "Operators",
    "Clients", "Provided", "Supersedes", "Note", "See", "For", "And", "But",
    "From", "Into", "Over", "After", "Before", "Random", "Sequential",
})


@dataclass(frozen=True)
class StructSpan:
    """Byte-exact span: ``raw[start_byte:end_byte] == text.encode()``."""

    start_byte: int
    end_byte: int
    text: str


@dataclass
class Block:
    """One structural block of a note (retrieval-expansion unit)."""

    block_id: str  # f"{doc_id}#{ordinal}"
    doc_id: str
    kind: str  # section | list | callout | table | code
    section_path: tuple[str, ...]
    span: StructSpan
    wikilinks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class Cluster:
    """Entity co-occurrence cluster scoped to one block."""

    cluster_id: str  # == block_id (one cluster per block by construction)
    block_id: str
    doc_id: str
    members: list[str] = field(default_factory=list)
    mentions: dict[str, list[StructSpan]] = field(default_factory=dict)


def define_cheap_structure() -> dict[str, object]:
    """Return the frozen pre-trial cheap-structure definition (a copy)."""
    return {k: (list(v) if isinstance(v, list) else v)
            for k, v in CHEAP_STRUCTURE_DEF.items()}


def _byte_span(raw: bytes, char_start: int, char_end: int, text: str) -> StructSpan:
    start = len(text[:char_start].encode("utf-8"))
    end = start + len(text[char_start:char_end].encode("utf-8"))
    assert raw[start:end] == text[char_start:char_end].encode("utf-8")
    return StructSpan(start, end, text[char_start:char_end])


def extract_blocks(doc_id: str, raw: str) -> list[Block]:
    """Split a markdown note into structural blocks with byte-exact spans."""
    blob = raw.encode("utf-8")
    lines = raw.split("\n")
    # char offset of each line start
    offs: list[int] = []
    pos = 0
    for line in lines:
        offs.append(pos)
        pos += len(line) + 1
    n = len(lines)
    line_kind: list[str | None] = [None] * n
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            line_kind[i] = "code"
            continue
        if in_fence:
            line_kind[i] = "code"
        elif _HEADING_RE.match(line):
            line_kind[i] = "heading"
        elif _TABLE_ROW_RE.match(line):
            line_kind[i] = "table"
        elif _CALLOUT_RE.match(line):
            line_kind[i] = "callout"
        elif _LIST_RE.match(line):
            line_kind[i] = "list"

    blocks: list[Block] = []
    section_stack: list[tuple[int, str]] = []

    def section_path() -> tuple[str, ...]:
        return tuple(t for _, t in section_stack)

    i = 0
    ordinal = 0

    def emit(kind: str, start_line: int, end_line: int) -> None:
        nonlocal ordinal
        cs, ce = offs[start_line], offs[end_line] + len(lines[end_line])
        span = _byte_span(blob, cs, ce, raw)
        assert blob[span.start_byte:span.end_byte] == span.text.encode("utf-8")
        chunk = span.text
        links = [m.group(1).strip() for m in _WIKILINK_RE.finditer(chunk)]
        tags = [m.group(1) for m in _TAG_RE.finditer(chunk)]
        blocks.append(Block(
            block_id=f"{doc_id}#{ordinal}", doc_id=doc_id, kind=kind,
            section_path=section_path(), span=span,
            wikilinks=links, tags=tags,
        ))
        ordinal += 1

    while i < n:
        kind = line_kind[i]
        match = _HEADING_RE.match(lines[i])
        if match is not None:
            level = len(match.group(1))
            title = match.group(2).strip()
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            # heading line itself belongs to the parent section block run;
            # a bare heading still yields no block of its own.
            section_stack.append((level, title))
            i += 1
            continue
        if kind in ("table", "code", "callout", "list"):
            j = i
            while j + 1 < n and line_kind[j + 1] == kind:
                j += 1
            # a fence run includes both fence lines (kind already marks them)
            emit(kind, i, j)
            i = j + 1
            continue
        # prose run (plain lines, blank lines, headings excluded above)
        if lines[i].strip():
            j = i
            while (j + 1 < n and line_kind[j + 1] is None
                   and lines[j + 1].strip()):
                j += 1
            emit("section", i, j)
            i = j + 1
        else:
            i += 1
    return blocks


def _entity_surfaces(chunk: str) -> list[tuple[str, int, int]]:
    """Candidate entity surfaces with char offsets (dedup downstream)."""
    out: list[tuple[str, int, int]] = []
    for m in _WIKILINK_RE.finditer(chunk):
        target = m.group(1).strip()
        alias = m.group(2)
        shown = alias if alias else target
        out.append((target, m.start(1), m.start(1) + len(m.group(1))))
        if alias and alias.strip() != target:
            out.append((alias.strip(), m.start(2), m.end(2)))
        _ = shown
    for m in _TAG_RE.finditer(chunk):
        out.append(("#" + m.group(1), m.start(), m.end()))
    for m in _PHRASE_RE.finditer(chunk):
        if m.group(1) not in _PHRASE_STOP:
            out.append((m.group(1), m.start(1), m.end(1)))
    return out


def cluster_block(block: Block, raw: str) -> Cluster:
    """Build the co-occurrence cluster for one block (byte-anchored)."""
    blob = raw.encode("utf-8")
    base = block.span.start_byte
    chunk = block.span.text
    seen: dict[str, int] = {}
    for surface, _, _ in _entity_surfaces(chunk):
        seen[surface] = seen.get(surface, 0) + 1
    members: list[str] = []
    mentions: dict[str, list[StructSpan]] = {}
    for surface, count in seen.items():
        is_link = surface in block.wikilinks or surface.startswith("#")
        if not is_link and " " not in surface and count < 2:
            continue  # singleton bare word: scaffolding until proven otherwise
        members.append(surface)
        spans: list[StructSpan] = []
        from_idx = 0
        while True:
            at = chunk.find(surface, from_idx)
            if at < 0:
                break
            start = base + len(chunk[:at].encode("utf-8"))
            end = start + len(surface.encode("utf-8"))
            if blob[start:end] == surface.encode("utf-8"):
                spans.append(StructSpan(start, end, surface))
            from_idx = at + len(surface)
        if spans:
            mentions[surface] = spans
    members = [m for m in members if m in mentions]
    members.sort()
    return Cluster(cluster_id=block.block_id, block_id=block.block_id,
                   doc_id=block.doc_id, members=members, mentions=mentions)


def build_note(doc_id: str, raw: str) -> tuple[list[Block], list[Cluster]]:
    """Extract blocks + clusters for one note (pure, deterministic)."""
    blocks = extract_blocks(doc_id, raw)
    return blocks, [cluster_block(b, raw) for b in blocks]
