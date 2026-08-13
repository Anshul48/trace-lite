"""Prompt normalisation and deterministic LLM-output quality gates.

The summary boundary is deliberately independent of any provider.  Provider
adapters can normalise a response envelope, but every caller that persists
model output must still pass it through these deterministic checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_MARKDOWN_LINK_RE = re.compile(r"!??\[([^\]]+)\]\([^)]*\)")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WORD_RE = re.compile(r"[\w][\w'-]*", re.UNICODE)
_TRANSPORT_WRAPPER_RE = re.compile(
    r"(?:<\|(?:assistant|end|eot_id|eom_id|endoftext)\|>|"
    r"<\|start_header_id\|>assistant<\|end_header_id\|>)",
    re.IGNORECASE,
)

# These are intentionally small and conservative. They are used only to make
# the lexical source-overlap gate less likely to accept generic output; they
# never delete, rewrite, or create an alternate copy of summary text.
_OVERLAP_IGNORED_TERMS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "can", "could", "do", "does", "for", "from", "has", "have", "in",
    "into", "is", "it", "its", "of", "on", "or", "that", "the", "their",
    "this", "to", "was", "were", "which", "with", "will", "would", "you",
    "your", "we", "they", "he", "she", "i", "every", "child", "children",
    "passage", "passages", "text", "note", "notes", "source", "sources",
}

_PROMPT_ECHO_PATTERNS = (
    re.compile(r"\bwe\s+need\s+(?:an?\s+)?answer\b", re.IGNORECASE),
    re.compile(r"\bsummar(?:y|ize|ise|izing|ising)\s+every\s+child\b", re.IGNORECASE),
    re.compile(r"\breturn\s+only\b", re.IGNORECASE),
    re.compile(r"\bdo\s+not\s+mention\s+the\s+prompt\b", re.IGNORECASE),
    re.compile(r"\b(?:child\s+passages|attempted\s+summary|repaired\s+summary)\s*:", re.IGNORECASE),
    re.compile(r"\b(?:give\s+a\s+short|topic\s+label)\b", re.IGNORECASE),
)

_REASONING_PATTERNS = (
    re.compile(r"<\s*/?\s*(?:think|analysis|reasoning|thought|cot)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:analysis|reasoning|thought(?:\s+process)?|chain\s+of\s+thought)\s*[:\-]", re.IGNORECASE),
    re.compile(r"\b(?:analysis|reasoning|thought(?:\s+process)?|chain\s+of\s+thought)\s*[:\-]", re.IGNORECASE),
    re.compile(r"\b(?:let\s+me\s+think|i(?:'|’)ll\s+think|thinking\s+step\s+by\s+step)\b", re.IGNORECASE),
)

_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:summary|answer|response|title|output|repaired\s+summary)\s*[:\-]",
    re.IGNORECASE,
)


def normalize_markdown_for_summary(text: str) -> str:
    """Return prompt-friendly Markdown without changing the source atom.

    Headings and their content remain visible.  Fences, blockquote/list
    decoration, HTML comments, link destinations, and emphasis delimiters are
    presentation noise for a summarisation prompt.  The function is pure and
    never writes to Spine.
    """
    if not text:
        return ""
    text = _HTML_COMMENT_RE.sub("", text)
    lines: list[str] = []
    in_frontmatter = False
    in_fence = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if line == "```" or line.startswith("```") or line.startswith("~~~"):
            in_fence = not in_fence
            continue
        if line == "---" and not lines and not in_fence:
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        # Preserve heading markers, but remove quote/list/checkbox decoration.
        if not line.startswith("#"):
            line = re.sub(r"^(?:>\s*)+", "", line)
            line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line)
            line = re.sub(r"^\[[ xX]\]\s+", "", line)
        line = _MARKDOWN_LINK_RE.sub(r"\1", line)
        line = re.sub(r"[*_~`]+", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def strip_transport_wrappers(text: str | None) -> str:
    """Remove only known protocol delimiters from a candidate response.

    Deliberately do not strip ``<think>`` or labels such as ``Answer:``.  Those
    are content-quality failures and must remain visible to the validator.
    """
    if text is None:
        return ""
    value = str(text).replace("\ufeff", "").strip()
    value = _TRANSPORT_WRAPPER_RE.sub("", value).strip()
    return " ".join(value.split())


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def _meaningful_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for word in _words(text):
        cleaned = word.strip("'-").casefold()
        if not cleaned or cleaned in _OVERLAP_IGNORED_TERMS:
            continue
        # Keep acronyms and short technical identifiers intact, while adding a
        # light root for common inflections.  This recognises facts/factual
        # without pretending to be a full linguistic stemmer.
        terms.add(cleaned)
        if len(cleaned) > 5:
            for suffix in ("ingly", "edly", "ing", "ed", "ies", "es", "s"):
                if cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 4:
                    root = cleaned[: -len(suffix)]
                    if suffix == "ies":
                        root += "y"
                    terms.add(root)
                    break
        if len(cleaned) >= 5:
            terms.add(cleaned[:4])
    return terms


def _has_source_overlap(candidate: str, source_texts: Iterable[str]) -> bool:
    source_terms = _meaningful_terms("\n".join(source_texts))
    candidate_terms = _meaningful_terms(candidate)
    return bool(source_terms and candidate_terms and source_terms.intersection(candidate_terms))


@dataclass(frozen=True)
class ValidationResult:
    """Stable result returned by title and summary validators."""

    valid: bool
    code: str | None = None
    feedback: str = ""
    normalized_text: str = ""

    @property
    def failure_code(self) -> str | None:
        """Compatibility/readability alias for callers emitting diagnostics."""
        return self.code

    @property
    def message(self) -> str:
        return self.feedback


SummaryValidationResult = ValidationResult
TitleValidationResult = ValidationResult


def _failure(code: str, feedback: str, value: str) -> ValidationResult:
    return ValidationResult(False, code=code, feedback=feedback, normalized_text=value)


def _success(value: str) -> ValidationResult:
    return ValidationResult(True, normalized_text=value)


def validate_summary(
    text: str | None,
    minimum_length: int = 1,
    child_texts: Iterable[str] | None = None,
) -> ValidationResult:
    """Validate a generated summary before it can enter Cortex.

    The validator intentionally returns a code and deterministic repair advice
    instead of only a boolean.  It never includes the model's raw response in
    that advice, so corrective prompts cannot treat untrusted output as an
    instruction.
    """
    value = strip_transport_wrappers(text)
    if not value:
        return _failure("empty_response", "Return visible summary prose, not an empty response.", value)
    for pattern in _PROMPT_ECHO_PATTERNS:
        if pattern.search(value):
            return _failure(
                "prompt_echo",
                "Return factual prose about the source passages; do not repeat instructions or prompt labels.",
                value,
            )
    for pattern in _REASONING_PATTERNS:
        if pattern.search(value):
            return _failure(
                "reasoning_output",
                "Return only the visible factual answer; omit hidden reasoning and analysis preambles.",
                value,
            )
    if _LABEL_PREFIX_RE.search(value) or value.rstrip(" :-–—").casefold() in {
        "summary", "answer", "response", "title", "output",
    }:
        return _failure(
            "label_only",
            "Return the content itself, without a Summary:, Answer:, Response:, or Title: label.",
            value,
        )
    if len(value) < max(1, minimum_length):
        return _failure(
            "too_short",
            f"Return meaningful prose of at least {max(1, minimum_length)} characters.",
            value,
        )
    if len(_words(value)) < 3:
        return _failure(
            "too_few_words",
            "Return at least three factual words.",
            value,
        )
    if child_texts is not None:
        sources = [strip_transport_wrappers(str(source)) for source in child_texts if source]
        if sources and not _has_source_overlap(value, sources):
            return _failure(
                "missing_source_terms",
                "Use at least one meaningful term from the child passages while covering their facts.",
                value,
            )
    return _success(value)


def validate_title(text: str | None, min_words: int = 2, max_words: int = 5) -> ValidationResult:
    """Validate a generated tree title as a short, visible topic label."""
    raw = "" if text is None else str(text)
    if not raw.strip():
        return _failure("empty_response", "Return a visible 2–5 word topic label.", "")
    # A title should be one line.  Newlines are not a harmless transport
    # wrapper because they frequently indicate a returned prompt or rationale.
    if len([line for line in raw.splitlines() if line.strip()]) > 1:
        return _failure("malformed_title", "Return one single-line topic label.", strip_transport_wrappers(raw))
    value = strip_transport_wrappers(raw).strip("'\"“”‘’ ")
    value = " ".join(value.split())
    for pattern in _PROMPT_ECHO_PATTERNS:
        if pattern.search(value):
            return _failure(
                "prompt_echo",
                "Return only a topic label; do not repeat the title instruction or prompt text.",
                value,
            )
    for pattern in _REASONING_PATTERNS:
        if pattern.search(value):
            return _failure(
                "reasoning_output",
                "Return only the visible topic label; omit reasoning and analysis text.",
                value,
            )
    if _LABEL_PREFIX_RE.search(value):
        return _failure(
            "label_only",
            "Return the title itself without a Title:, Answer:, or Summary: prefix.",
            value,
        )
    if len(value) > 120 or not re.fullmatch(r"[\w][\w &'./+#\-]*[\w+#)]", value, re.UNICODE):
        return _failure("malformed_title", "Return a plain, readable topic label.", value)
    words = _words(value)
    if len(words) < min_words or len(words) > max_words:
        return _failure(
            "title_word_count",
            f"Return a topic label containing {min_words}–{max_words} words.",
            value,
        )
    return _success(value)


def is_meaningful_summary(
    text: str | None,
    minimum_length: int,
    child_texts: Iterable[str] | None = None,
) -> bool:
    """Compatibility wrapper for the old boolean summary check."""
    return validate_summary(text, minimum_length, child_texts).valid


@dataclass(frozen=True)
class SummaryResult:
    text: str
    provenance: str
    attempts: int = 0
    failure_codes: tuple[str, ...] = ()
