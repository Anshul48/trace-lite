"""Text atomizer: Segments raw text into paragraph/bullet-level atoms with exact offset tracking."""

import re
import uuid
from trace_lite.spine.models import Atom


class Atomizer:
    """
    Segments text into paragraph/bullet atoms with exact start/end character offsets.
    Rules:
    1. Paragraph split on double newlines
    2. Bullet/item split on bullet list items within paragraphs
    3. Merge very short fragments (< min_length) with preceding atom if available
    4. Split long blocks (> max_length) at sentence boundaries
    """

    def __init__(self, min_length: int = 50, max_length: int = 2000):
        self.min_length = min_length
        self.max_length = max_length

    def atomize(
        self, text: str, source_artifact_id: str, metadata: dict | None = None
    ) -> list[Atom]:
        if not text.strip():
            return []

        metadata = metadata or {}
        raw_spans = self._get_initial_spans(text)
        merged_spans = self._merge_short_spans(text, raw_spans)
        final_spans = self._split_long_spans(text, merged_spans)

        atoms: list[Atom] = []
        for idx, (start, end) in enumerate(final_spans):
            content = text[start:end].strip()
            if not content:
                continue
            atom_id = f"atom-{uuid.uuid4().hex[:12]}"
            atom = Atom.create(
                atom_id=atom_id,
                content=content,
                source_artifact_id=source_artifact_id,
                sequence_index=idx,
                char_offset_start=start,
                char_offset_end=end,
                metadata=metadata.copy(),
            )
            atoms.append(atom)

        return atoms

    def _get_initial_spans(self, text: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        # Split by double newlines or bullet patterns
        paragraph_pattern = re.compile(r"\n\s*\n")
        start = 0

        for match in paragraph_pattern.finditer(text):
            p_end = match.start()
            if p_end > start:
                spans.extend(self._split_bullets(text, start, p_end))
            start = match.end()

        if start < len(text):
            spans.extend(self._split_bullets(text, start, len(text)))

        return spans

    def _split_bullets(self, text: str, start: int, end: int) -> list[tuple[int, int]]:
        segment = text[start:end]
        bullet_pattern = re.compile(r"(?m)^(?:[-*•]|\d+[\.\)])\s+")
        matches = list(bullet_pattern.finditer(segment))

        if not matches or matches[0].start() > 0 and len(matches) == 1:
            return [(start, end)]

        spans: list[tuple[int, int]] = []
        b_start = start
        for i, match in enumerate(matches):
            m_abs = start + match.start()
            if m_abs > b_start and i == 0:
                spans.append((b_start, m_abs))
                b_start = m_abs
            elif i > 0:
                spans.append((b_start, m_abs))
                b_start = m_abs
        if b_start < end:
            spans.append((b_start, end))

        return spans if spans else [(start, end)]

    def _merge_short_spans(
        self, text: str, spans: list[tuple[int, int]]
    ) -> list[tuple[int, int]]:
        if not spans:
            return []

        merged: list[tuple[int, int]] = []
        current_start, current_end = spans[0]

        for next_start, next_end in spans[1:]:
            length = current_end - current_start
            if length < self.min_length:
                # Extend current span
                current_end = next_end
            else:
                merged.append((current_start, current_end))
                current_start, current_end = next_start, next_end

        merged.append((current_start, current_end))
        return merged

    def _split_long_spans(
        self, text: str, spans: list[tuple[int, int]]
    ) -> list[tuple[int, int]]:
        final_spans: list[tuple[int, int]] = []

        for start, end in spans:
            length = end - start
            if length <= self.max_length:
                final_spans.append((start, end))
            else:
                # Split at sentence boundaries
                sub_spans = self._split_by_sentence(text, start, end)
                final_spans.extend(sub_spans)

        return final_spans

    def _split_by_sentence(self, text: str, start: int, end: int) -> list[tuple[int, int]]:
        segment = text[start:end]
        sentence_endings = re.compile(r"(?<=[.!?])\s+")
        sub_spans: list[tuple[int, int]] = []

        curr = start
        for match in sentence_endings.finditer(segment):
            split_pos = start + match.end()
            if split_pos - curr >= self.min_length:
                sub_spans.append((curr, split_pos))
                curr = split_pos

        if curr < end:
            sub_spans.append((curr, end))

        return sub_spans if sub_spans else [(start, end)]
