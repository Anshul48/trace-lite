"""Structural source segmentation (R02): byte-faithful evidence spans.

Structural baseline only (paragraphs, bullets, headings, atomic code fences and
markdown tables, conversation turns / log events where applicable). No
ML/perplexity boundary detection (R08 non-goal).

Byte contract (ARCHITECTURE §2): every span is a UTF-8 half-open byte range
``[start_byte, end_byte)`` into the retained raw payload, and ``span.text ==
raw_bytes[start_byte:end_byte].decode("utf-8")`` always holds. Span text is the
verbatim raw slice — no trimming that moves a boundary without moving the
offset, no normalization. A normalized-text offset must never claim raw-byte
identity, so this module performs no normalization at all.

Whitespace rule: only whole blank boundary lines are dropped; every kept line is
stored verbatim (including CRLF and indentation). The byte range always covers
exactly the returned text.

Length cap: paragraph/bullet/turn spans are capped at ``MAX_SPAN_CHARS`` (1200),
split on sentence boundaries. Code fences and markdown tables are atomic and
stay undivided even over the cap (structural integrity wins; C2-3).

Repeated identical text: spans are distinguished by ordinal occurrence, so each
occurrence gets a separate span ID downstream (ARCHITECTURE §2).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

SEGMENTER_NAME = "structural"
SEGMENTER_VERSION = "v1"
MAX_SPAN_CHARS = 1200

_FENCE_RE = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_BULLET_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>(?:[-*+])|(?:\d{1,9}[.)]))\s+")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TURN_RE = re.compile(
    r"^(?:"
    r"(?:user|assistant|human|ai|system|speaker(?:\s+\w+)?|interviewer|interviewee"
    r"|narrator|[QA])\s*:"
    r"|\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?[^\]]*\]"
    r"|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\b"
    r"|\d{2}:\d{2}(?::\d{2})?\s+(?:[-–—]\s+)?"
    r")",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class RawSpan:
    """One structural span with verified raw-byte coordinates."""

    ordinal: int
    start_byte: int
    end_byte: int
    kind: str  # paragraph | bullet | heading | code | table | turn
    header: str  # nearest preceding heading text ("" when none)
    text: str


def segmenter_version_for(config: str = "structural-v1") -> str:
    """Extraction-run-compatible version hash for the segmenter + config."""
    digest = hashlib.sha256(
        f"{SEGMENTER_NAME}:{SEGMENTER_VERSION}:{config}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{SEGMENTER_NAME}-{SEGMENTER_VERSION}:{digest}"


def _line_byte_offsets(text: str) -> tuple[list[str], list[int]]:
    """Split text into lines (kept endings) with each line's start byte offset."""
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line.encode("utf-8"))
    return lines, offsets


def _strip_line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1]
    return line


def _split_long_text(
    text: str, start_byte: int, kind: str, header: str, first_ordinal: int
) -> list[RawSpan]:
    """Split an over-cap block on sentence boundaries with exact byte offsets."""
    if len(text) <= MAX_SPAN_CHARS:
        return [
            RawSpan(
                ordinal=first_ordinal,
                start_byte=start_byte,
                end_byte=start_byte + len(text.encode("utf-8")),
                kind=kind,
                header=header,
                text=text,
            )
        ]
    # Tokenize into non-separator parts with char offsets. Separators are any
    # whitespace run the sentence regex matches (spaces, tabs, newlines,
    # unicode whitespace). Chunks are verbatim slices ``text[cs:ce]`` that keep
    # interior separators exactly as written; separators *between* chunks stay
    # unclaimed, so no byte is ever normalized or moved.
    part_bounds: list[tuple[int, int]] = []
    pos = 0
    for match in _SENTENCE_SPLIT_RE.finditer(text):
        if match.start() > pos:
            part_bounds.append((pos, match.start()))
        pos = match.end()
    if pos < len(text):
        part_bounds.append((pos, len(text)))
    # Re-group parts into ≤ cap chunks; a single part over the cap is
    # hard-split on character boundaries (byte math stays exact).
    chunk_bounds: list[tuple[int, int]] = []
    chunk_start: int | None = None
    chunk_end: int | None = None
    for part_start, part_end in part_bounds:
        if chunk_start is None:
            if part_end - part_start > MAX_SPAN_CHARS:
                for cut in range(part_start, part_end, MAX_SPAN_CHARS):
                    chunk_bounds.append((cut, min(cut + MAX_SPAN_CHARS, part_end)))
            else:
                chunk_start, chunk_end = part_start, part_end
            continue
        if part_end - chunk_start <= MAX_SPAN_CHARS:
            chunk_end = part_end
            continue
        chunk_bounds.append((chunk_start, chunk_end))
        if part_end - part_start > MAX_SPAN_CHARS:
            for cut in range(part_start, part_end, MAX_SPAN_CHARS):
                chunk_bounds.append((cut, min(cut + MAX_SPAN_CHARS, part_end)))
            chunk_start = None
            chunk_end = None
        else:
            chunk_start, chunk_end = part_start, part_end
    if chunk_start is not None:
        chunk_bounds.append((chunk_start, chunk_end))
    # Map char ranges onto byte offsets by consuming the block string exactly.
    spans: list[RawSpan] = []
    cursor_text = 0
    cursor_byte = start_byte
    for index, (cs, ce) in enumerate(chunk_bounds):
        cursor_byte += len(text[cursor_text:cs].encode("utf-8"))
        cursor_text = cs
        end_byte = cursor_byte + len(text[cs:ce].encode("utf-8"))
        spans.append(
            RawSpan(
                ordinal=first_ordinal + index,
                start_byte=cursor_byte,
                end_byte=end_byte,
                kind=kind,
                header=header,
                text=text[cs:ce],
            )
        )
        cursor_byte = end_byte
        cursor_text = ce
    return spans


def segment_text(text: str, *, body_byte_base: int = 0) -> list[RawSpan]:
    """Segment raw markdown-ish text into byte-faithful structural spans.

    ``body_byte_base`` shifts every span's byte range (used when segmenting a
    frontmatter-stripped body: pass the parser's ``body_byte_offset`` so spans
    address the retained raw payload, with the mapping recorded on the view).
    """
    lines, offsets = _line_byte_offsets(text)
    n = len(lines)

    def block_span(
        first: int, last: int, kind: str, header: str, ordinal: int
    ) -> tuple[list[RawSpan], int]:
        # Drop whole blank boundary lines only; kept lines stay verbatim.
        while first <= last and not _strip_line_ending(lines[first]).strip():
            first += 1
        while last >= first and not _strip_line_ending(lines[last]).strip():
            last -= 1
        if first > last:
            return [], ordinal
        start = body_byte_base + offsets[first]
        chunk_text = "".join(lines[first : last + 1])
        if kind in ("code", "table"):
            end = start + len(chunk_text.encode("utf-8"))
            return [
                RawSpan(
                    ordinal=ordinal,
                    start_byte=start,
                    end_byte=end,
                    kind=kind,
                    header=header,
                    text=chunk_text,
                )
            ], ordinal + 1
        spans = _split_long_text(chunk_text, start, kind, header, ordinal)
        return spans, ordinal + len(spans)

    spans: list[RawSpan] = []
    ordinal = 0
    header = ""
    i = 0
    while i < n:
        stripped = _strip_line_ending(lines[i])
        if not stripped.strip():
            i += 1
            continue
        # Code fence: atomic through the closing fence (or EOF if unclosed).
        fence = _FENCE_RE.match(stripped)
        if fence:
            fence_char = fence.group("fence")[0]
            fence_len = len(fence.group("fence"))
            j = i + 1
            while j < n:
                inner = _strip_line_ending(lines[j]).strip()
                if inner.startswith(fence_char * fence_len):
                    break
                j += 1
            last = min(j, n - 1)  # unclosed fence: rest of document, still atomic
            made, ordinal = block_span(i, last, "code", header, ordinal)
            spans.extend(made)
            i = last + 1
            continue
        # Markdown table: run of 2+ pipe rows is atomic.
        if _TABLE_ROW_RE.match(stripped):
            j = i
            while j < n and _TABLE_ROW_RE.match(_strip_line_ending(lines[j])):
                j += 1
            if j - i >= 2:
                made, ordinal = block_span(i, j - 1, "table", header, ordinal)
                spans.extend(made)
                i = j
                continue
        # Heading: own span; becomes the 1-hop parent context below.
        if _HEADING_RE.match(stripped):
            made, ordinal = block_span(i, i, "heading", header, ordinal)
            spans.extend(made)
            header = stripped.strip().lstrip("#").strip()
            i += 1
            continue
        # Bullet: each marker line starts a span; indented lines attach.
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            base_indent = len(bullet.group("indent").replace("\t", "    "))
            j = i + 1
            while j < n:
                nxt = _strip_line_ending(lines[j])
                if not nxt.strip():
                    break
                if _BULLET_RE.match(nxt) or _FENCE_RE.match(nxt) or _HEADING_RE.match(nxt):
                    break
                indent = len(nxt) - len(nxt.lstrip(" \t"))
                if indent <= base_indent and _TABLE_ROW_RE.match(nxt):
                    break
                if indent <= base_indent and _TURN_RE.match(nxt):
                    break
                j += 1
            made, ordinal = block_span(i, j - 1, "bullet", header, ordinal)
            spans.extend(made)
            i = j
            continue
        # Conversation turn / log event: marker line plus continuation lines.
        if _TURN_RE.match(stripped):
            j = i + 1
            while j < n:
                nxt = _strip_line_ending(lines[j])
                if not nxt.strip():
                    break
                if (
                    _FENCE_RE.match(nxt)
                    or _HEADING_RE.match(nxt)
                    or _BULLET_RE.match(nxt)
                    or _TURN_RE.match(nxt)
                    or _TABLE_ROW_RE.match(nxt)
                ):
                    break
                j += 1
            made, ordinal = block_span(i, j - 1, "turn", header, ordinal)
            spans.extend(made)
            i = j
            continue
        # Paragraph: run of plain lines through the next blank/special line.
        j = i + 1
        while j < n:
            nxt = _strip_line_ending(lines[j])
            if not nxt.strip():
                break
            if (
                _FENCE_RE.match(nxt)
                or _HEADING_RE.match(nxt)
                or _BULLET_RE.match(nxt)
                or _TURN_RE.match(nxt)
                or _TABLE_ROW_RE.match(nxt)
            ):
                break
            j += 1
        made, ordinal = block_span(i, j - 1, "paragraph", header, ordinal)
        spans.extend(made)
        i = j
    return spans


def verify_spans(raw: str, spans: list[RawSpan], *, body_byte_base: int = 0) -> bool:
    """Check every span slices its exact text from the raw payload bytes."""
    blob = raw.encode("utf-8")
    for span in spans:
        if not (0 <= span.start_byte <= span.end_byte <= len(blob)):
            return False
        if blob[span.start_byte : span.end_byte] != span.text.encode("utf-8"):
            return False
    _ = body_byte_base
    return True
