"""Deterministic structural relation extractor (ARCHITECTURE §4).

Consumes R02 span dicts (``span_id``, ``text``, ``start_byte``, ``end_byte``)
and emits typed :class:`AssertionFrame` / :class:`RelationEdge` values with
exact byte anchors. Rules:

- Supported relationships only: causal keywords (improves, degrades,
  restores, requires, conflicts with), comparison, definitions, reporting /
  reference hooks, and conditions/exceptions (under, unless, only, during,
  when). Anything else yields no frame — never a forced edge.
- Window: one span (≤1200 chars by the R02 cap) plus its recorded heading
  context; long-range links use :func:`find_candidates` (bounded top-k token
  overlap) and bind only on exact unique surfaces — otherwise unresolved.
- Lexical similarity never emits support/contradiction/causation rows.
  Opposing assertions coexist as separate rows; they are never merged.
- Malformed input yields a partial result with warnings. This module never
  raises on data (programming errors from storage layers still raise there).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .frames import (
    EPISTEMIC_SOURCE_ASSERTED,
    MODALITY_ASSERTED,
    MODALITY_POSSIBLE,
    MODALITY_REQUIRED,
    POLARITY_NEGATIVE,
    POLARITY_POSITIVE,
    REL_TYPES,
    STATUS_ASSERTED,
    STATUS_UNRESOLVED,
    AssertionFrame,
    EntityMention,
    RelationEdge,
)

SCHEMA_VERSION = "r04-graph-v1"
EXTRACTOR_NAME = "structural"
EXTRACTOR_VERSION = "v1"

MAX_SENTENCES_PER_SPAN = 200
MAX_SPAN_CHARS_INPUT = 50000
LONG_RANGE_TOP_K = 3

_PRONOUNS = frozenset(
    {"it", "they", "he", "she", "this", "that", "these", "those", "them", "him", "her"}
)

# (canonical predicate, relation type, surface-verb regex, modality)
_VERB_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    ("improves", "improves", r"improv(?:e|es|ed|ing)", MODALITY_ASSERTED),
    ("degrades", "degrades", r"degrad(?:e|es|ed|ing)", MODALITY_ASSERTED),
    ("restores", "restores", r"restor(?:e|es|ed|ing)", MODALITY_ASSERTED),
    ("requires", "requires", r"requir(?:e|es|ed|ing)", MODALITY_REQUIRED),
    ("conflicts with", "conflicts_with", r"conflicts?\s+with", MODALITY_ASSERTED),
    ("helps", "improves", r"helps?|helped|helping", MODALITY_ASSERTED),
    ("disappears", "degrades", r"disappears?|vanishes?|evaporates?", MODALITY_ASSERTED),
    ("matches", "compares", r"match(?:es|ed|ing)?", MODALITY_ASSERTED),
    ("reports", "reports", r"report(?:s|ed|ing)?", MODALITY_ASSERTED),
    ("allows", "allows", r"allows?|allowed", MODALITY_POSSIBLE),
    ("uses", "uses", r"\buse[sd]?", MODALITY_ASSERTED),
    ("dominates", "dominates", r"dominat(?:e|es|ed|ing)", MODALITY_ASSERTED),
    ("defines", "defines", r"means?|defin(?:e|es|ed|ing)|refers?\s+to", MODALITY_ASSERTED),
    ("is", "defines", r"\bis\b|\bare\b|\bwas\b|\bwere\b", MODALITY_ASSERTED),
)
assert {rel for _, rel, _, _ in _VERB_PATTERNS} <= REL_TYPES

_NEG_RE = re.compile(
    r"\b(?:not|n't|never|without|cannot|can't|does\s+not|doesn't|did\s+not"
    r"|disappears?|fails?|vanishes?)\b",
    re.IGNORECASE,
)
_LEAD_COND_RE = re.compile(
    r"^\s*(Under|Unless|Only\s[^,;]+|During|When|If)\b([^,;]*)([,;])",
)
# Fronted "only" inversion without a comma ("Only under low contention does
# batching improve throughput."): the condition precedes the auxiliary.
_FRONTED_ONLY_RE = re.compile(
    r"^\s*Only\s+(under|when|if|unless|during)\b(.+?)\s*\b(does|do|did)\b\s+",
    re.IGNORECASE,
)
_PRECAP_RE = re.compile(r"[A-Z][a-zA-Z\-]*\s*$")
_TRAIL_COND_RE = re.compile(
    r"[,;]?\s*\b(under[^,;.]*|unless[^,;.]*|only\s+(?:when|if|under)?[^,;.]*"
    r"|during[^,;.]*|when[^,;.]*)\s*\.?\s*$",
    re.IGNORECASE,
)
_PAREN_RE = re.compile(r"\([^()]*\)")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")
_UNITS_RE = re.compile(r"\d+(?:\.\d+)?\s?(?:%|ms|µs|us|ns|MB|GB|KB|bytes?|s\b|x\b)")
_VERSION_TRAIL_RE = re.compile(r"\s+\bon\s+([A-Z][\w\-]*)\s*\.?\s*$")
_AUX_SUBJECT_RE = re.compile(r"^(?:does|do|did)\s+", re.IGNORECASE)
_AUX_TAIL_RE = re.compile(r"\s+(?:does|do|did)\s+(?:not\s*)?$", re.IGNORECASE)
_QUALIFIED_NAME_RE = re.compile(r"\b([A-Z][a-zA-Z\-]*)\s+\(([^()]*)\)")
_WS_NORM_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS_NORM_RE.sub(" ", text).strip()


def _stable_id(run_id: str, kind: str, seq: int) -> str:
    return hashlib.sha256(f"{run_id}:{kind}:{seq}".encode("utf-8")).hexdigest()[:32]


def _sentence_byte_ranges(text: str, base_byte: int) -> list[tuple[str, int, int]]:
    """Split span text into (sentence, start_byte, end_byte) with exact bytes."""
    bounds: list[tuple[int, int]] = []
    pos = 0
    for match in _SENT_SPLIT_RE.finditer(text):
        end = match.end()
        if text[pos:end].strip():
            bounds.append((pos, end))
        pos = end
    if pos < len(text) and text[pos:].strip():
        bounds.append((pos, len(text)))
    out: list[tuple[str, int, int]] = []
    for char_start, char_end in bounds[:MAX_SENTENCES_PER_SPAN]:
        start = base_byte + len(text[:char_start].encode("utf-8"))
        end = start + len(text[char_start:char_end].encode("utf-8"))
        out.append((text[char_start:char_end], start, end))
    return out


def _split_condition(sentence: str) -> tuple[str, str | None, str | None]:
    """Return (core, condition_text, temporal_scope) for one sentence."""
    condition: str | None = None
    temporal: str | None = None

    def _classify(clause: str) -> None:
        nonlocal condition, temporal
        cleaned = _norm(clause.strip().strip(",;"))
        if not cleaned:
            return
        if re.match(r"(?i)^during\b", cleaned):
            temporal = cleaned if temporal is None else temporal + "; " + cleaned
        else:
            condition = cleaned if condition is None else condition + "; " + cleaned

    core = sentence
    fronted = _FRONTED_ONLY_RE.match(core)
    if fronted:
        _classify("Only " + fronted.group(1) + fronted.group(2))
        core = core[fronted.end():]
    else:
        lead = _LEAD_COND_RE.match(core)
        if lead:
            _classify(lead.group(1) + lead.group(2))
            core = core[lead.end():]
    trail = _TRAIL_COND_RE.search(core)
    if trail:
        _classify(trail.group(1))
        core = core[: trail.start()]
    return core, condition, temporal


@dataclass
class ExtractResult:
    """Extractor output: frames + edges + per-item warnings (never raises)."""

    status: str
    assertions: list[AssertionFrame] = field(default_factory=list)
    relations: list[RelationEdge] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    spans_seen: int = 0
    spans_skipped: int = 0


def find_candidates(
    surface: str, spans: list[dict], *, top_k: int = LONG_RANGE_TOP_K
) -> list[str]:
    """Bounded long-range candidate retrieval: token-overlap top-k span IDs.

    Used only for unresolved mentions. Returns candidate span IDs; binding
    still requires an exact unique surface match done by the caller.
    """
    try:
        query_terms = {t.lower() for t in re.findall(r"[A-Za-z0-9']+", surface)}
    except Exception:
        return []
    if not query_terms:
        return []
    scored: list[tuple[int, str]] = []
    for span in spans:
        try:
            text = span.get("text", "")
            sid = span.get("span_id", "")
            if not isinstance(text, str) or not isinstance(sid, str) or not sid:
                continue
            terms = {t.lower() for t in re.findall(r"[A-Za-z0-9']+", text)}
            overlap = len(query_terms & terms)
            if overlap:
                scored.append((overlap, sid))
        except Exception:
            continue
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [sid for _, sid in scored[:top_k]]


def _qualified_name_index(spans: list[dict]) -> dict[str, set[str]]:
    """Map bare name token -> set of qualifier strings seen in the input."""
    index: dict[str, set[str]] = {}
    for span in spans:
        try:
            text = span.get("text", "")
            if not isinstance(text, str):
                continue
            for token, qualifier in _QUALIFIED_NAME_RE.findall(text):
                index.setdefault(token, set()).add(_norm(qualifier))
        except Exception:
            continue
    return index


def _make_mention(
    surface: str,
    role: str,
    span_id: str,
    sent_start: int,
    sentence: str,
    *,
    canonical_key: str | None = None,
    status: str = STATUS_ASSERTED,
) -> EntityMention:
    """Build a mention with a placeholder id (assigned in the id post-pass)."""
    start = sentence.find(surface)
    if start < 0:
        start_byte = sent_start
        end_byte = sent_start + len(sentence.encode("utf-8"))
    else:
        start_byte = sent_start + len(sentence[:start].encode("utf-8"))
        end_byte = start_byte + len(surface.encode("utf-8"))
    return EntityMention(
        mention_id="",
        surface=surface,
        role=role,
        span_id=span_id,
        start_byte=start_byte,
        end_byte=end_byte,
        canonical_key=canonical_key,
        mapping_version=SCHEMA_VERSION,
        status=status,
    )


def _parse_sentence(
    sentence: str,
    sent_start: int,
    sent_end: int,
    span_id: str,
    name_index: dict[str, set[str]],
    *,
    _depth: int = 0,
) -> list[AssertionFrame]:
    """Parse one sentence into zero or more frames (pure; ids assigned later)."""
    # Parentheticals are verbatim qualifier arguments — except name qualifiers
    # ("Chen (storage team)"), which stay inline as subject identity.
    qualifiers: list[str] = []
    kept: list[str] = []
    cursor = 0
    for paren in _PAREN_RE.finditer(sentence):
        prefix = sentence[cursor:paren.start()]
        kept.append(prefix)
        if _PRECAP_RE.search(prefix):
            kept.append(paren.group(0))
        else:
            cleaned = _norm(paren.group(0)[1:-1])
            if cleaned:
                qualifiers.append(cleaned)
        cursor = paren.end()
    kept.append(sentence[cursor:])
    core, condition, temporal = _split_condition("".join(kept))
    units_match = _UNITS_RE.search(core)
    units = _norm(units_match.group(0)) if units_match else None
    version_scope: str | None = None
    version_trail = _VERSION_TRAIL_RE.search(core)
    if version_trail:
        version_scope = _norm("on " + version_trail.group(1))
        core = core[: version_trail.start()]
    core = _norm(core)
    if not core:
        return []

    # Earliest surface verb wins (pattern order breaks ties), so reporting
    # verbs beat later copulas ("Chen reports latency improved." → reports).
    best: tuple[int, int] | None = None
    match: re.Match[str] | None = None
    predicate = ""
    for index, (cand_pred, _rel, verb_alt, _mod) in enumerate(_VERB_PATTERNS):
        found = re.search(r"\b(?:" + verb_alt + r")\b", core, re.IGNORECASE)
        if found and (best is None or (found.start(), index) < best):
            best = (found.start(), index)
            match = found
            predicate = cand_pred
    if match is None:
        return []
    subject_raw = _norm(core[: match.start()]).strip()
    subject_raw = _AUX_SUBJECT_RE.sub("", subject_raw).strip()
    subject_raw = _AUX_TAIL_RE.sub("", subject_raw).strip()
    object_raw = _norm(core[match.end():].strip().rstrip(".;"))
    if not subject_raw:
        return []

    polarity = POLARITY_NEGATIVE if _NEG_RE.search(core) else POLARITY_POSITIVE
    _p, _rel_type, _alt, modality = next(
        (p, r, a, m) for p, r, a, m in _VERB_PATTERNS if p == predicate
    )

    status = STATUS_ASSERTED
    subject_lower = subject_raw.lower()
    if subject_lower in _PRONOUNS:
        status = STATUS_UNRESOLVED
    elif re.fullmatch(r"[A-Z][a-zA-Z\-]*", subject_raw) and subject_raw in name_index:
        # Bare single-token name with qualified variants elsewhere in the
        # input (e.g. bare "Chen" beside "Chen (storage team)"): ambiguous,
        # never forced.
        status = STATUS_UNRESOLVED

    frame = AssertionFrame(
        assertion_id="",
        ordinal=0,
        predicate=predicate,
        polarity=polarity,
        modality=modality,
        epistemic_status=EPISTEMIC_SOURCE_ASSERTED,
        subject_text=subject_raw,
        object_text=object_raw or None,
        condition_text=condition,
        units=units,
        temporal_scope=temporal,
        version_scope=version_scope,
        span_id=span_id,
        start_byte=sent_start,
        end_byte=sent_end,
        status=status,
    )
    frame.mentions.append(
        _make_mention(subject_raw, "subject", span_id, sent_start, sentence,
                      canonical_key=(subject_lower if status == STATUS_ASSERTED
                                     else None),
                      status=status)
    )
    if object_raw:
        frame.mentions.append(
            _make_mention(object_raw, "object", span_id, sent_start, sentence,
                          canonical_key=object_raw.lower())
        )
    if condition:
        frame.mentions.append(
            _make_mention(condition, "condition", span_id, sent_start, sentence)
        )
    if temporal:
        frame.mentions.append(
            _make_mention(temporal, "temporal", span_id, sent_start, sentence)
        )
    if units:
        frame.mentions.append(
            _make_mention(units, "unit", span_id, sent_start, sentence)
        )
    for qualifier in qualifiers:
        frame.qualifier_texts.append(qualifier)
        frame.mentions.append(
            _make_mention(qualifier, "qualifier", span_id, sent_start, sentence)
        )
    # A trailing "with ..." phrase is kept verbatim in the object AND stored
    # as a qualifier argument (no information loss, no invented claim).
    with_match = re.search(r"\bwith\s+([^,;.]+)$", object_raw or "", re.IGNORECASE)
    if with_match and object_raw:
        extra = _norm("with " + with_match.group(1))
        if extra not in frame.qualifier_texts:
            frame.qualifier_texts.append(extra)
            frame.mentions.append(
                _make_mention(extra, "qualifier", span_id, sent_start, sentence)
            )
    frames = [frame]

    # Reporting verbs ("X reports <clause>") additionally parse the reported
    # clause as its own source-asserted frame; the outer frame stays positive
    # unless the reporting itself is negated ("does not report").
    if predicate == "reports" and object_raw and _depth < 1:
        head = subject_raw + " " + match.group(0)
        frame.polarity = (
            POLARITY_NEGATIVE if _NEG_RE.search(head) else POLARITY_POSITIVE
        )
        frames.extend(
            _parse_sentence(object_raw, sent_start, sent_end, span_id,
                            name_index, _depth=_depth + 1)
        )
    return frames


def _rel_type_for(predicate: str) -> str:
    return next((r for p, r, _a, _m in _VERB_PATTERNS if p == predicate), "defines")


def extract_spans(
    spans: list[dict] | None,
    *,
    run_id: str = "",
    heading_context: dict[str, str] | None = None,
) -> ExtractResult:
    """Extract local assertion graphs from R02 span dicts. Never raises."""
    run = run_id or hashlib.sha256(b"r04-local").hexdigest()[:16]
    result = ExtractResult(status="complete")
    try:
        if not isinstance(spans, list):
            result.status = "partial"
            result.warnings.append({"reason": "input is not a span list"})
            return result
        valid: list[dict] = []
        for index, item in enumerate(spans):
            try:
                if not isinstance(item, dict):
                    raise TypeError(f"span #{index} is not a mapping")
                text = item.get("text", "")
                span_id = item.get("span_id", "")
                if not isinstance(text, str):
                    raise TypeError(f"span #{index} text is not str")
                if not isinstance(span_id, str) or not span_id:
                    raise TypeError(f"span #{index} has no span_id")
                start = int(item.get("start_byte", 0))
                end = int(item.get("end_byte", start + len(text.encode("utf-8"))))
                valid.append(
                    {"span_id": span_id, "text": text,
                     "start_byte": start, "end_byte": end}
                )
            except Exception as exc:
                result.spans_skipped += 1
                result.warnings.append({"index": index, "reason": str(exc)[:200]})
        result.spans_seen = len(valid)
        name_index = _qualified_name_index(valid)

        # Pass 1: parse sentences into frames (ids assigned in pass 2).
        parsed: list[tuple[AssertionFrame, dict]] = []
        # (outer position in parsed, first inner position): reporting linkage.
        reports_links: list[tuple[int, int]] = []
        for item in valid:
            try:
                text = item["text"]
                if len(text) > MAX_SPAN_CHARS_INPUT:
                    text = text[:MAX_SPAN_CHARS_INPUT]
                    result.warnings.append(
                        {"span_id": item["span_id"],
                         "reason": "span truncated at extractor bound"}
                    )
                if not text.strip():
                    continue
                for sentence, s_start, s_end in _sentence_byte_ranges(
                    text, item["start_byte"]
                ):
                    try:
                        frames = _parse_sentence(
                            sentence.strip(), s_start, s_end,
                            item["span_id"], name_index,
                        )
                    except Exception as exc:
                        result.warnings.append(
                            {"span_id": item["span_id"],
                             "reason": f"sentence skipped: {exc}"[:200]}
                        )
                        continue
                    if not frames:
                        continue
                    outer_pos = len(parsed)
                    for frame in frames:
                        parsed.append((frame, item))
                    if frames[0].predicate == "reports" and len(frames) > 1:
                        reports_links.append((outer_pos, outer_pos + 1))
            except Exception as exc:
                result.spans_skipped += 1
                result.warnings.append(
                    {"span_id": str(item.get("span_id", "?"))[:64],
                     "reason": str(exc)[:200]}
                )

        # Pass 2: stable seq ids, mention ids, relations with typed endpoints.
        inner_of: dict[int, int] = dict(reports_links)
        seq_of_pos: dict[int, int] = {}
        for seq, (frame, _item) in enumerate(parsed):
            frame.assertion_id = _stable_id(run, "a", seq)
            frame.ordinal = seq
            seq_of_pos[len(seq_of_pos)] = seq
            for pos, mention in enumerate(frame.mentions):
                mention.mention_id = _stable_id(
                    run, f"m{seq}-{mention.role}:{pos}", seq * 64 + pos)
        for pos, (frame, item) in enumerate(parsed):
            seq = seq_of_pos[pos]
            rel_type = _rel_type_for(frame.predicate)
            subj_m = next(
                (m for m in frame.mentions if m.role == "subject"), None)
            obj_m = next(
                (m for m in frame.mentions if m.role == "object"), None)
            if frame.status == STATUS_UNRESOLVED:
                # Ambiguous binding: record surfaces + retrieval candidates,
                # bind no endpoint.
                result.relations.append(
                    RelationEdge(
                        relation_id=_stable_id(run, "r", seq),
                        rel_type=rel_type,
                        subject_text=frame.subject_text,
                        object_text=frame.object_text,
                        polarity=frame.polarity,
                        condition_text=frame.condition_text,
                        epistemic_status=EPISTEMIC_SOURCE_ASSERTED,
                        span_id=item["span_id"],
                        start_byte=frame.start_byte,
                        end_byte=frame.end_byte,
                        status=STATUS_UNRESOLVED,
                        candidates=[
                            sid for sid in find_candidates(
                                frame.subject_text or "", valid)
                            if sid != item["span_id"]
                        ][:LONG_RANGE_TOP_K],
                    )
                )
            else:
                edge = RelationEdge(
                    relation_id=_stable_id(run, "r", seq),
                    rel_type=rel_type,
                    subject_text=frame.subject_text,
                    object_text=frame.object_text,
                    subject_mention_id=(subj_m.mention_id if subj_m else None),
                    object_mention_id=(obj_m.mention_id if obj_m else None),
                    polarity=frame.polarity,
                    condition_text=frame.condition_text,
                    epistemic_status=EPISTEMIC_SOURCE_ASSERTED,
                    span_id=item["span_id"],
                    start_byte=frame.start_byte,
                    end_byte=frame.end_byte,
                    status=STATUS_ASSERTED,
                )
                if pos in inner_of:
                    # Reporting edge targets the reported-clause assertion.
                    inner_seq = seq_of_pos[inner_of[pos]]
                    edge.object_assertion_id = _stable_id(run, "a", inner_seq)
                    edge.object_mention_id = None
                result.relations.append(edge)
            if frame.condition_text or frame.temporal_scope:
                scope = frame.condition_text or frame.temporal_scope or ""
                scope_m = next(
                    (m for m in frame.mentions
                     if m.role in ("condition", "temporal")
                     and m.surface == scope),
                    None,
                )
                result.relations.append(
                    RelationEdge(
                        relation_id=_stable_id(run, "q", seq),
                        rel_type="qualifies",
                        subject_text=scope,
                        object_text=frame.subject_text,
                        subject_mention_id=(
                            scope_m.mention_id if scope_m else None),
                        object_assertion_id=frame.assertion_id,
                        polarity=POLARITY_POSITIVE,
                        epistemic_status=EPISTEMIC_SOURCE_ASSERTED,
                        span_id=item["span_id"],
                        start_byte=frame.start_byte,
                        end_byte=frame.end_byte,
                        status=frame.status,
                    )
                )
        result.assertions.extend(frame for frame, _item in parsed)

        # Heading context: 1-hop envelope qualifier edges (bounded, R02 C2-4).
        for item in valid:
            heading = (heading_context or {}).get(item["span_id"], "")
            if heading and heading.strip():
                seq = len(parsed) + len(result.relations)
                result.relations.append(
                    RelationEdge(
                        relation_id=_stable_id(run, "h", seq),
                        rel_type="qualifies",
                        subject_text=_norm(heading),
                        object_text=None,
                        polarity=POLARITY_POSITIVE,
                        epistemic_status=EPISTEMIC_SOURCE_ASSERTED,
                        span_id=item["span_id"],
                        start_byte=item["start_byte"],
                        end_byte=item["end_byte"],
                        status=STATUS_ASSERTED,
                    )
                )
        if result.warnings or result.spans_skipped:
            result.status = "partial"
        return result
    except Exception as exc:
        result.status = "partial"
        result.warnings.append({"reason": f"extractor fallback: {exc}"[:200]})
        return result


def extracted_assertion_to_row(frame: AssertionFrame, run_id: str,
                               revision_id: str) -> dict:
    """Render a frame as an ``assertion`` table row dict."""
    return {
        "assertion_id": frame.assertion_id,
        "version": 1,
        "run_id": run_id,
        "revision_id": revision_id,
        "predicate": frame.predicate,
        "polarity": frame.polarity,
        "modality": frame.modality,
        "epistemic_status": frame.epistemic_status,
        "subject_text": frame.subject_text,
        "object_text": frame.object_text,
        "condition_text": frame.condition_text,
        "units": frame.units,
        "temporal_scope": frame.temporal_scope,
        "version_scope": frame.version_scope,
        "span_id": frame.span_id,
        "start_byte": frame.start_byte,
        "end_byte": frame.end_byte,
        "status": frame.status,
    }
