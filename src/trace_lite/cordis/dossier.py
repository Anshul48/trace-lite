"""Query-generated evidence dossiers (R07).

Deterministic, LLM-free compilation of retrieval candidates into an
evidence dossier per ARCHITECTURE §7 / EVALUATION §3:

- Rank against objective TEXT (``goal_state['objective']``), never ``goal_id``.
- Namespace/access/version filters BEFORE text reveal (filter-first ordering;
  storage-layer enforcement stays the caller's job and is stated as such).
- Mandatory constraints + goal context reserved FIRST; mandatory overflow is
  an explicit ``insufficient_budget`` failure, never silent drops.
- Oversized evidence reduced via source-linked anchored excerpts, never
  arbitrary prefixes; an atomic group that cannot fit is an explicit
  coverage failure.
- Conflicting evidence coexists under explicit disagreements; cross-source
  virtual holons are transient (``query_dossier`` kind, derived status with
  lineage — never ground truth).
- Support states (supported/partial/conflicting/insufficient) are assessed
  by a NAMED lexical heuristic (``SUPPORT_METHOD``); lexical overlap does
  not prove sufficiency and is never marketed as NLI.
- Token accounting uses the CONSUMER tokenizer
  (:func:`trace_lite.cordis.models.estimate_tokens`) over content PLUS
  citation wrappers and relation metadata.

Honesty boundary (Cordis adapter): schema conformance is reported
separately from harness loading/use; the Tier-1 lexical action gate stays
labeled ``heuristic``; memory provenance never authorizes execution.
"""

from __future__ import annotations

import hashlib
import sqlite3
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import CommitmentRecord, TokenBudget, estimate_tokens

#: Named support-check method. A lexical heuristic — explicitly NOT NLI.
SUPPORT_METHOD = "lexical-heuristic-v1"
#: Required label for the Tier-1 lexical action gate wherever it is surfaced.
TIER1_GATE_LABEL = "heuristic"
#: Relation types that mark explicit disagreement (verbatim retention).
CONFLICT_REL_TYPES = frozenset(
    {"conflicts_with", "contradicts", "disagrees_with", "disputes"}
)
#: Minimum anchored-excerpt window (tokens, consumer tokenizer).
MIN_EXCERPT_TOKENS = 25


class DossierStatus(str, Enum):
    OK = "ok"
    INSUFFICIENT_BUDGET = "insufficient_budget"
    COVERAGE_FAILURE = "coverage_failure"


class SupportState(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    doc_id: str = ""
    source_id: str = ""
    revision_id: str = ""
    span_id: str = ""
    start_byte: int = 0
    end_byte: int = 0
    excerpt_text: str = ""
    excerpt_hash: str = ""
    citation_valid: bool = False
    citation_method: str = ""  # r02_excerpt | source_slice | atom_self[:degraded]
    citation_degraded: bool = False  # True iff revision resolution failed
    is_excerpt: bool = False
    candidate_route: str = ""  # lexical | vector | holon | fused
    relevance_score: float = 0.0
    selection_reason: str = ""
    token_cost: int = 1  # content + citation wrapper + relation metadata
    relations: list[dict[str, Any]] = Field(default_factory=list)


class VirtualHolon(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    holon_id: str
    kind: str = "query_dossier"
    member_ids: list[str] = Field(default_factory=list)
    member_sources: list[str] = Field(default_factory=list)
    status: str = "derived"  # transient synthesis: lineage, never ground truth
    derivation: str = ""
    qualification_edges: list[dict[str, Any]] = Field(default_factory=list)


class EvidenceDossier(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    objective: str = ""
    goal_id: str = ""  # opaque label only; never feeds ranking
    scope: dict[str, Any] = Field(default_factory=dict)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    status: DossierStatus = DossierStatus.OK
    failure_reason: str = ""
    selections: list[EvidenceSelection] = Field(default_factory=list)
    virtual_holons: list[VirtualHolon] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    disagreements: list[dict[str, Any]] = Field(default_factory=list)
    omissions: list[dict[str, Any]] = Field(default_factory=list)
    support_state: SupportState = SupportState.INSUFFICIENT
    relevant_candidate_count: int = 0
    supported_count: int = 0
    candidate_routes: dict[str, int] = Field(default_factory=dict)
    coverage_report: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    token_accounting: dict[str, Any] = Field(default_factory=dict)
    checker: dict[str, Any] = Field(default_factory=dict)
    adapter_honesty: dict[str, Any] = Field(default_factory=dict)


# -- scoring (objective text only) --------------------------------------

def _terms(text: str) -> set[str]:
    import re

    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def _trigrams(text: str) -> set[str]:
    s = "".join(ch for ch in text.lower() if ch.isalnum() or ch == " ")
    return {s[i : i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else set()


def objective_relevance(candidate_text: str, objective: str) -> float:
    """Hybrid lexical + char-trigram Jaccard against the OBJECTIVE text.

    The trigram backoff keeps near-paraphrases retrievable even with weak
    word overlap; a zero-lexical paraphrase arriving via the holon route is
    still retained by union semantics (route recorded, score honestly low).
    ``goal_id`` is never consulted.
    """
    cand_terms, obj_terms = _terms(candidate_text), _terms(objective)
    lex = (
        len(cand_terms & obj_terms) / len(cand_terms | obj_terms)
        if cand_terms and obj_terms
        else 0.0
    )
    cand_tri, obj_tri = _trigrams(candidate_text), _trigrams(objective)
    tri = (
        len(cand_tri & obj_tri) / len(cand_tri | obj_tri)
        if cand_tri and obj_tri
        else 0.0
    )
    return min(1.0, 0.7 * lex + 0.3 * tri)


def rank_candidates(
    candidates: list[dict[str, Any]], objective: str
) -> list[tuple[float, dict[str, Any]]]:
    """Stable rank by objective-text relevance (ties keep union order)."""
    scored = [
        (objective_relevance(str(c.get("text", "")), objective), c)
        for c in candidates
    ]
    scored.sort(key=lambda pair: -pair[0])
    return scored


# -- citations ------------------------------------------------------------

def citation_wrapper(selection: EvidenceSelection) -> str:
    return (
        f"[{selection.doc_id or selection.source_id} "
        f"{selection.start_byte}:{selection.end_byte} "
        f"route={selection.candidate_route}]"
    )


def verify_citation(
    candidate: dict[str, Any], conn: sqlite3.Connection | None = None
) -> tuple[bool, str]:
    """Verify an exact byte citation. Returns (valid, method).

    Strength ladder (recorded honestly per selection):
    ``r02_excerpt`` (hash-verified via R02 ``excerpt_span``) >
    ``source_slice`` (byte slice of the NAMED revision payload — either
    resolved server-side from ``revision_id`` via ``conn``, or of a
    caller-supplied payload) >
    ``atom_self`` (self-consistent atom bounds; weakest, labeled as such;
    with ``conn`` it is a degraded fallback only — see :func:`compile_dossier`).
    """
    text = str(candidate.get("text", ""))
    blob = text.encode("utf-8")
    span_id = str(candidate.get("span_id", "") or "")
    if span_id and conn is not None:
        try:
            from ..source.views import excerpt_span

            if excerpt_span(conn, span_id) == blob:
                return True, "r02_excerpt"
            return False, "r02_excerpt"
        except (KeyError, ValueError):
            return False, "r02_excerpt"
    revision_id = str(candidate.get("revision_id", "") or "")
    if revision_id and conn is not None:
        try:
            row = conn.execute(
                "SELECT text FROM source_revision WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
        except Exception:
            row = None
        if row is None:
            return False, "source_slice"
        raw = str(row[0]).encode("utf-8")
        try:
            start = int(candidate.get("start_byte", 0))
            end = int(candidate.get("end_byte", len(raw)))
        except (TypeError, ValueError):
            return False, "source_slice"
        if 0 <= start <= end <= len(raw) and bytes(raw[start:end]) == blob:
            return True, "source_slice"
        return False, "source_slice"
    payload = candidate.get("source_bytes", candidate.get("revision_text"))
    if payload is not None:
        raw = payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode("utf-8")
        try:
            start = int(candidate.get("start_byte", 0))
            end = int(candidate.get("end_byte", len(raw)))
        except (TypeError, ValueError):
            return False, "source_slice"
        if 0 <= start <= end <= len(raw) and bytes(raw[start:end]) == blob:
            return True, "source_slice"
        return False, "source_slice"
    try:
        start = int(candidate.get("start_byte", 0))
        end = int(candidate.get("end_byte", len(blob)))
    except (TypeError, ValueError):
        return False, "atom_self"
    if start == 0 and end == len(blob):
        return True, "atom_self"
    return False, "atom_self"


# -- server-side resolution -------------------------------------------------

#: Resolution outcomes for :func:`resolve_candidate`.
RESOLVED = "resolved"
DEGRADED = "degraded"  # identity unprovable: atom_self fallback only, surfaced
INVALID = "invalid"  # proven wrong: dropped as citation_invalid, never included


def _atom_binding(candidate: dict[str, Any]) -> int | None:
    """Lexical atom id bound to a candidate, if any."""
    raw = candidate.get("atom_id")
    if raw is not None:
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    cid = str(candidate.get("candidate_id", "") or "")
    if cid.startswith("atom-"):
        try:
            return int(cid.split("-", 1)[1])
        except ValueError:
            return None
    return None


def resolve_candidate(
    conn: sqlite3.Connection,
    candidate: dict[str, Any],
    *,
    current_only: bool = True,
) -> tuple[dict[str, Any], str, str]:
    """Resolve one candidate to (revision_id, span_id, byte range).

    Returns ``(enriched, status, reason)`` with ``status`` one of
    ``RESOLVED``/``DEGRADED``/``INVALID``. Namespace/access/version come from
    the serving store (server truth, overriding caller claims); byte bounds
    come from the served atom row. Span-anchored candidates adopt their
    revision from the span row; whole-atom candidates keep ``span_id`` empty
    (their text spans several R02 spans) and verify as ``source_slice``
    against the named revision payload.
    """
    c = dict(candidate)
    del current_only  # version filtering stays in compile_dossier, below
    try:
        return _resolve_candidate(conn, c)
    except Exception as exc:  # resolution store trouble: degraded, never crash
        return c, DEGRADED, f"resolution store unavailable: {exc}"


def _resolve_candidate(
    conn: sqlite3.Connection, c: dict[str, Any]
) -> tuple[dict[str, Any], str, str]:
    text = str(c.get("text", ""))
    blob = text.encode("utf-8")
    span_id = str(c.get("span_id", "") or "")
    if span_id:
        try:
            from ..source.views import excerpt_span

            full = excerpt_span(conn, span_id)
        except (KeyError, ValueError):
            # The span claim is unprovable: drop it so verification falls
            # back to the caller-asserted bounds (degraded, surfaced) rather
            # than failing closed on a span that was never retained.
            c["span_id"] = ""
            return c, DEGRADED, f"span {span_id} unresolvable against any revision"
        if full != blob:
            return c, INVALID, f"span {span_id} bytes differ from candidate text"
        row = conn.execute(
            "SELECT revision_id FROM evidence_span WHERE span_id = ?",
            (span_id,),
        ).fetchone()
        if row is None:
            return c, DEGRADED, f"span {span_id} has no revision row"
        c["revision_id"] = str(row[0])
        _adopt_scope(conn, c, str(row[0]))
        return c, RESOLVED, ""
    atom_id = _atom_binding(c)
    if atom_id is None:
        return c, DEGRADED, "no revision/span binding; caller-asserted bounds only"
    atom = conn.execute(
        "SELECT id, doc_id, start_byte, end_byte, text FROM atom WHERE id = ?",
        (atom_id,),
    ).fetchone()
    if atom is None:
        return c, DEGRADED, f"atom {atom_id} not served (replaced or retracted)"
    served_text = str(atom[4])
    if served_text.encode("utf-8") != blob:
        return c, INVALID, f"atom {atom_id} text differs from candidate text"
    doc_id = str(atom[1])
    c["text"] = served_text
    c["doc_id"] = doc_id
    c["source_id"] = doc_id
    c["start_byte"] = int(atom[2])
    c["end_byte"] = int(atom[3])
    c["span_id"] = ""
    src = conn.execute(
        "SELECT namespace, access_scope FROM source WHERE source_id = ?",
        (doc_id,),
    ).fetchone()
    if src is None:  # pre-R01 legacy atom: honestly labeled, never current
        c["namespace"] = str(c.get("namespace", "default"))
        c["access_scope"] = str(c.get("access_scope", "default"))
        c["version_status"] = "legacy"
        c["revision_id"] = ""
        return c, DEGRADED, f"atom {atom_id} is legacy text without a source revision"
    c["namespace"] = str(src[0])
    c["access_scope"] = str(src[1])
    rev = conn.execute(
        "SELECT revision_id, status FROM source_revision"
        " WHERE source_id = ? AND atom_id = ? ORDER BY observed_at LIMIT 1",
        (doc_id, atom_id),
    ).fetchone()
    if rev is None:
        cur = conn.execute(
            "SELECT revision_id, status FROM source_revision sr JOIN source s"
            " ON s.current_revision_id = sr.revision_id WHERE s.source_id = ?",
            (doc_id,),
        ).fetchone()
        if cur is None:
            return c, DEGRADED, f"atom {atom_id} has no revision row"
        c["revision_id"] = str(cur[0])
        c["version_status"] = str(cur[1])
    else:
        c["revision_id"] = str(rev[0])
        c["version_status"] = str(rev[1])
    return c, RESOLVED, ""


def _adopt_scope(conn: sqlite3.Connection, c: dict[str, Any], revision_id: str) -> None:
    """Adopt namespace/access/version truth for a span-resolved revision."""
    rev = conn.execute(
        "SELECT source_id, namespace, status FROM source_revision WHERE revision_id = ?",
        (revision_id,),
    ).fetchone()
    if rev is None:
        return
    source_id = str(rev[0])
    src = conn.execute(
        "SELECT access_scope FROM source WHERE source_id = ?", (source_id,)
    ).fetchone()
    c["namespace"] = str(rev[1])
    if src is not None:
        c["access_scope"] = str(src[0])
    c["version_status"] = str(rev[2])


# -- anchored excerpts ------------------------------------------------------

def anchored_excerpt(
    text: str, objective: str, max_tokens: int
) -> tuple[str, int, int, bool]:
    """Query-anchored window: best objective-term-density slice, byte-exact.

    Returns (excerpt, start_byte, end_byte, is_excerpt). Never an arbitrary
    prefix: the window centers on the highest term-density anchor; ties go
    to the earliest anchor. Byte offsets index the UTF-8 payload and the
    slice is decoded on character boundaries.
    """
    blob = text.encode("utf-8")
    if estimate_tokens(text) <= max_tokens or max_tokens <= 0:
        return text, 0, len(blob), False
    obj_terms = _terms(objective) or _terms(text)
    words = text.split(" ")
    # Anchor on the median objective-term hit: the window centers on the
    # densest term cluster (a window-best with earliest-tie-break would
    # park 12 words ahead of the terms and clip them).
    import re as _re

    lower = [_re.sub(r"[^a-z0-9]", "", w.lower()) for w in words]
    hits = [i for i, w in enumerate(lower) if w in obj_terms]
    anchor_word = hits[len(hits) // 2] if hits else 0
    # Grow a char window around the anchor word to the token budget.
    anchor_char = sum(len(w) + 1 for w in words[:anchor_word])
    char_budget = max(64, max_tokens * 4)
    half = char_budget // 2
    cs = max(0, anchor_char - half)
    # Map char offsets to byte offsets on character boundaries.
    raw = blob.decode("utf-8")
    # cs is a char offset; walk chars so multi-byte text stays aligned.
    start_char = min(cs, len(raw))
    excerpt_chars = raw[start_char : start_char + char_budget]
    start_byte = len(raw[:start_char].encode("utf-8"))
    end_byte = start_byte + len(excerpt_chars.encode("utf-8"))
    return excerpt_chars, start_byte, end_byte, True


# -- adapter honesty ---------------------------------------------------------

def adapter_report(*, schema_conformance: bool) -> dict[str, Any]:
    """Cordis adapter honesty block (ARCH §7: conformance ≠ harness loading)."""
    return {
        "schema_conformance": bool(schema_conformance),
        "harness_loading_verified": False,
        "harness_note": (
            "schema-shape conformance only; real harness loading/use is a "
            "separate check (R09), never implied by this dossier"
        ),
        "tier1_action_gate_label": TIER1_GATE_LABEL,
        "tier1_note": (
            "deterministic lexical screen (heuristic); memory provenance "
            "never authorizes execution"
        ),
        "synthesis_status": "derived",
        "synthesis_note": (
            "generated synthesis carries lineage and derived status; "
            "never ground truth"
        ),
    }


# -- dossier compilation ------------------------------------------------------

def compile_dossier(
    *,
    query: str,
    objective: str = "",
    goal_id: str = "",
    scope: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    disagreements: list[dict[str, Any]] | None = None,
    mandatory: list[CommitmentRecord] | None = None,
    budget: TokenBudget | None = None,
    snapshot: dict[str, Any] | None = None,
    synthesis_text: str = "",
    synthesis_lineage: str = "",
    overretrieve_bound: int = 50,
    relevance_threshold: float = 0.05,
    support_coverage: float = 0.5,
    conn: sqlite3.Connection | None = None,
    schema_conformance: bool = True,
) -> EvidenceDossier:
    """Compile a query-generated evidence dossier (deterministic, no LLM)."""
    scope = dict(scope or {})
    candidates = list(candidates or [])
    relations = list(relations or [])
    disagreements = list(disagreements or [])
    mandatory = list(mandatory or [])
    budget = budget if budget is not None else TokenBudget()
    snapshot = dict(snapshot or {})

    namespaces = scope.get("namespaces")
    access = scope.get("access_scopes") or scope.get("allowed_visibility")
    current_only = bool(scope.get("current_only", True))

    checker = {
        "method": SUPPORT_METHOD,
        "note": "lexical overlap does not prove sufficiency; not NLI",
    }
    honesty = adapter_report(schema_conformance=schema_conformance)

    def base(scope_note: str = "", **kw: Any) -> EvidenceDossier:
        return EvidenceDossier(
            query=query,
            objective=objective,
            goal_id=goal_id,
            scope={**scope, "note": scope_note} if scope_note else dict(scope),
            snapshot=dict(snapshot),
            checker=checker,
            adapter_honesty=honesty,
            **kw,  # type: ignore[arg-type]
        )

    # 1. Reserve mandatory constraints + goal context FIRST (consumer tokens,
    #    wrappers included). Mandatory overflow is explicit failure.
    mandatory_wrappers = [f"[invariant {m.id} {m.authority.value}]" for m in mandatory]
    mandatory_cost = sum(
        estimate_tokens(f"{w} {m.statement}")
        for w, m in zip(mandatory_wrappers, mandatory)
    )
    goal_cost = estimate_tokens(objective) if objective else 0
    reserved = (
        budget.system_prompt_tokens + mandatory_cost + goal_cost
    )
    # A zero salient budget means "use the remainder" (documented default);
    # an explicit positive budget caps evidence. Either way the ceiling holds.
    salient = budget.salient_evidence_budget
    evidence_allowance = (
        min(salient, budget.max_total_tokens - reserved)
        if salient > 0
        else budget.max_total_tokens - reserved
    )
    budget_block = {
        "max_total_tokens": budget.max_total_tokens,
        "system_prompt_tokens": budget.system_prompt_tokens,
        "mandatory_invariant_tokens": budget.mandatory_invariant_tokens,
        "goal_state_tokens": budget.goal_state_tokens,
        "salient_evidence_budget": budget.salient_evidence_budget,
        "mandatory_cost": mandatory_cost,
        "goal_cost": goal_cost,
        "evidence_allowance": max(0, evidence_allowance),
    }
    # Zero mandatory reservation = fit under the ceiling (same rule as
    # TraceLiteContextCompiler); an explicit positive reservation is strict.
    mandatory_reservation = budget.mandatory_invariant_tokens
    if (
        mandatory_reservation > 0 and mandatory_cost > mandatory_reservation
    ) or evidence_allowance < 0:
        return base(
            "mandatory_overflow",
            status=DossierStatus.INSUFFICIENT_BUDGET,
            failure_reason=(
                f"mandatory cost {mandatory_cost} exceeds reservation "
                f"{budget.mandatory_invariant_tokens}; zero silent drops"
            ),
            omissions=[
                {"kind": "mandatory_invariant", "id": m.id, "reason": "insufficient_budget"}
                for m in mandatory
            ],
            coverage_report={
                "requested": len(candidates),
                "pre_filter": len(candidates),
                "post_filter": 0,
                "method": "none: mandatory overflow",
            },
            budget=budget_block,
            token_accounting={
                "mandatory_cost": mandatory_cost,
                "goal_cost": goal_cost,
                "evidence_cost": 0,
                "total": reserved,
            },
        )

    # 1b. Server-side resolution (conn present only): bind each candidate
    # to (revision_id, span_id, byte range) against the named immutable
    # revision BEFORE filtering, so the visibility/version rules below apply
    # to store truth rather than caller claims. Pure callers (conn=None)
    # keep the caller-asserted path unchanged.
    if conn is not None:
        bound: list[dict[str, Any]] = []
        for c in candidates:
            rc, status, reason = resolve_candidate(
                conn, c, current_only=current_only
            )
            rc["_resolution"] = status
            rc["_resolution_reason"] = reason
            bound.append(rc)
        candidates = bound

    # 2. Namespace/access/version filters BEFORE text reveal or scoring.
    pre_filter = len(candidates)
    filtered_out: dict[str, int] = {"namespace": 0, "access": 0, "version": 0}
    visible: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    for c in candidates:
        if namespaces is not None and str(c.get("namespace", "default")) not in namespaces:
            filtered_out["namespace"] += 1
            filtered.append(c)
            continue
        if access is not None and str(c.get("access_scope", "default")) not in access:
            filtered_out["access"] += 1
            filtered.append(c)
            continue
        if current_only and str(c.get("version_status", "current")) != "current":
            filtered_out["version"] += 1
            filtered.append(c)
            continue
        visible.append(c)
    overretrieved = pre_filter >= min(overretrieve_bound, max(pre_filter, 1))
    coverage: dict[str, Any] = {
        "requested": len(candidates),
        "pre_filter": pre_filter,
        "post_filter": len(visible),
        "filtered_out": filtered_out,
        "method": (
            f"overretrieve_bound={overretrieve_bound}"
            if overretrieved
            else "filtered_fallback"
        ),
        "coverage_gap_after_filter": bool(visible == [] and candidates != []),
        "resolution": (
            {
                RESOLVED: sum(1 for c in candidates if c.get("_resolution") == RESOLVED),
                DEGRADED: sum(1 for c in candidates if c.get("_resolution") == DEGRADED),
                INVALID: sum(1 for c in candidates if c.get("_resolution") == INVALID),
            }
            if conn is not None
            else "unresolved: pure caller-asserted path (no conn)"
        ),
        "degraded_citations": 0,
    }

    # 3. Rank the VISIBLE set against the objective text only. Union
    # semantics: a holon-route candidate is retained even at ~zero lexical
    # score (that is how paraphrases with no word overlap stay retrievable);
    # its score stays honestly low and its route is recorded.
    ranked = rank_candidates(visible, objective)
    relevant = [
        (s, c)
        for s, c in ranked
        if s >= relevance_threshold or str(c.get("route", "")) == "holon"
    ]
    routes: dict[str, int] = {}
    for _, c in relevant:
        routes[str(c.get("route", "lexical"))] = routes.get(str(c.get("route", "lexical")), 0) + 1

    # 4. Pack evidence with anchored excerpts; atomic groups that cannot fit
    #    are an explicit coverage failure (never partial silent inclusion).
    groups: dict[str, list[int]] = {}
    for idx, (_, c) in enumerate(relevant):
        gid = str(c.get("group_id", "") or "")
        if gid:
            groups.setdefault(gid, []).append(idx)
    atomic = {
        str(c.get("group_id"))
        for _, c in relevant
        if c.get("group_id") and c.get("atomic_group")
    }
    selections: list[EvidenceSelection] = []
    omissions: list[dict[str, Any]] = []
    # Scope/version filtering can partition an atomic group: visible members
    # are still selected (they fit), but the dossier must not claim group
    # completeness — the partition is a named omission, never silent.
    filtered_group_ids = {
        str(f.get("group_id", "") or "") for f in filtered
    } - {""}
    for gid in sorted(gid for gid in atomic if gid in filtered_group_ids):
        omissions.append(
            {
                "kind": "atomic_group_partitioned",
                "id": gid,
                "reason": "some members excluded by scope/version filters;"
                          " visible members selected without group-completeness claim",
            }
        )
    spent = 0
    evidence_token_total = 0
    consumed_idx: set[int] = set()
    failed_group = ""
    done_groups: set[str] = set()

    def _pack_member(
        score: float,
        c: dict[str, Any],
        idx: int,
        excerpt: str,
        sb: int,
        eb: int,
        is_ex: bool,
        cost: int,
    ) -> bool:
        candidate_id = str(c.get("candidate_id", f"c{idx}"))
        # Proven-wrong at resolution time (e.g. candidate text differs from
        # the served bytes): dropped as citation_invalid, never included —
        # even when the caller-asserted bounds look self-consistent.
        if conn is not None and str(c.get("_resolution", "") or "") == INVALID:
            omissions.append(
                {
                    "kind": "citation_invalid",
                    "id": candidate_id,
                    "reason": str(c.get("_resolution_reason", "resolution mismatch")),
                }
            )
            consumed_idx.add(idx)
            return False
        span_id = str(c.get("span_id", "") or "")
        if is_ex and span_id and conn is not None:
            # Excerpt of a span: the window must be a byte slice of the span.
            try:
                from ..source.views import excerpt_span

                full = excerpt_span(conn, span_id)
                valid = full[sb:eb] == excerpt.encode("utf-8")
            except (KeyError, ValueError):
                valid = False
            method = "r02_excerpt"
        else:
            valid, method = verify_citation(c, conn)
        degraded = False
        if conn is not None and valid and method == "atom_self":
            # Degraded fallback: revision resolution failed, so the citation
            # is self-consistent at best. Explicitly labeled and surfaced;
            # never counted as support (see §6 below).
            method = "atom_self:degraded"
            degraded = True
        sel = EvidenceSelection(
            candidate_id=candidate_id,
            doc_id=str(c.get("doc_id", "")),
            source_id=str(c.get("source_id", "")),
            revision_id=str(c.get("revision_id", "")),
            span_id=str(c.get("span_id", "") or ""),
            start_byte=int(c.get("start_byte", sb)),
            end_byte=int(c.get("end_byte", eb)),
            excerpt_text=excerpt,
            excerpt_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            citation_valid=valid,
            citation_method=method,
            citation_degraded=degraded,
            is_excerpt=is_ex,
            candidate_route=str(c.get("route", "lexical")),
            relevance_score=round(score, 6),
            selection_reason=f"ranked@{idx} objective-text relevance {score:.3f}",
            token_cost=cost,
            relations=[dict(r) for r in c.get("relations", [])],
        )
        if not valid:
            omissions.append(
                {"kind": "citation_invalid", "id": sel.candidate_id, "reason": method}
            )
            consumed_idx.add(idx)
            return False
        if degraded:
            omissions.append(
                {
                    "kind": "citation_degraded",
                    "id": sel.candidate_id,
                    "reason": str(c.get("_resolution_reason", "revision resolution failed")),
                }
            )
            coverage["degraded_citations"] = int(coverage.get("degraded_citations", 0)) + 1
        selections.append(sel)
        consumed_idx.add(idx)
        return True

    def _excerpt_cost(c: dict[str, Any], excerpt: str) -> int:
        probe = EvidenceSelection(
            candidate_id=str(c.get("candidate_id", "?")),
            doc_id=str(c.get("doc_id", "")),
            source_id=str(c.get("source_id", "")),
            start_byte=0,
            end_byte=len(excerpt.encode("utf-8")),
            excerpt_text=excerpt,
            candidate_route=str(c.get("route", "lexical")),
        )
        rel_cost = estimate_tokens(" ".join(str(r) for r in c.get("relations", [])))
        return estimate_tokens(excerpt) + estimate_tokens(citation_wrapper(probe)) + rel_cost

    for idx, (score, c) in enumerate(relevant):
        if idx in consumed_idx:
            continue
        gid = str(c.get("group_id", "") or "")
        if gid and gid in atomic and gid not in done_groups:
            done_groups.add(gid)
            members = [
                (s2, c2, i2)
                for i2, (s2, c2) in enumerate(relevant)
                if str(c2.get("group_id", "") or "") == gid and i2 not in consumed_idx
            ]
            # Cost the whole group at minimum windows first.
            planned = []
            group_total = 0
            for s2, c2, i2 in members:
                excerpt, sb, eb, is_ex = anchored_excerpt(
                    str(c2.get("text", "")), objective or query, MIN_EXCERPT_TOKENS
                )
                cost = _excerpt_cost(c2, excerpt)
                planned.append((s2, c2, i2, excerpt, sb, eb, is_ex, cost))
                group_total += cost
            if spent + group_total > evidence_allowance:
                failed_group = gid
                break
            for s2, c2, i2, excerpt, sb, eb, is_ex, cost in planned:
                if _pack_member(s2, c2, i2, excerpt, sb, eb, is_ex, cost):
                    spent += cost
                    evidence_token_total += cost
            continue
        # Non-atomic candidate: anchored excerpt within the REMAINDER.
        remainder = evidence_allowance - spent
        if remainder < MIN_EXCERPT_TOKENS:
            omissions.append({"kind": "candidate", "id": str(c.get("candidate_id", f"c{idx}")), "reason": "budget_remainder"})
            consumed_idx.add(idx)
            continue
        excerpt, sb, eb, is_ex = anchored_excerpt(
            str(c.get("text", "")), objective or query, remainder
        )
        cost = _excerpt_cost(c, excerpt)
        if spent + cost > evidence_allowance:
            # Try the minimum window before giving up on this candidate.
            excerpt, sb, eb, is_ex = anchored_excerpt(
                str(c.get("text", "")), objective or query, MIN_EXCERPT_TOKENS
            )
            cost = _excerpt_cost(c, excerpt)
            if spent + cost > evidence_allowance:
                omissions.append({"kind": "candidate", "id": str(c.get("candidate_id", f"c{idx}")), "reason": "budget_remainder"})
                consumed_idx.add(idx)
                continue
        if _pack_member(score, c, idx, excerpt, sb, eb, is_ex, cost):
            spent += cost
            evidence_token_total += cost
    # Ranked-out leftovers are named omissions (explicit, never silent).
    for idx, (_, c) in enumerate(relevant):
        if idx not in consumed_idx:
            omissions.append({"kind": "candidate", "id": str(c.get("candidate_id", f"c{idx}")), "reason": "ranked_out"})
    if failed_group:
        return base(
            "atomic_group_overflow",
            status=DossierStatus.COVERAGE_FAILURE,
            failure_reason=(
                f"atomic evidence group {failed_group!r} cannot fit its "
                f"source-linked minimum excerpts in the evidence allowance; "
                f"reduce via excerpts, not prefixes"
            ),
            selections=selections,
            omissions=omissions
            + [{"kind": "evidence_group", "id": failed_group, "reason": "coverage_failure"}],
            support_state=SupportState.INSUFFICIENT,
            relevant_candidate_count=len(relevant),
            supported_count=0,
            candidate_routes=routes,
            coverage_report=coverage,
            budget=budget_block,
            token_accounting={
                "mandatory_cost": mandatory_cost,
                "goal_cost": goal_cost,
                "evidence_cost": evidence_token_total,
                "total": reserved + evidence_token_total,
            },
        )

    # 5. Relations + disagreements: conflicting evidence coexists — under
    # the SAME visibility/version rules as candidates. Any edge touching a
    # KNOWN-excluded (filtered-out) candidate/doc is dropped as a named
    # omission — this closes the collect-from-original-candidates bypass, in
    # which relations from filtered-out candidates leaked in. Endpoints that
    # name no known candidate (graph-native claim/assertion ids, e.g. R07
    # qualification targets) are caller-asserted graph references and are
    # kept: exclusion cannot be proven for them. Candidate-embedded relations
    # ride their (visible) host. The served endpoint additionally
    # re-retrieves relations scoped to resolved-visible revisions, so served
    # edges reference visible revisions by construction.
    excluded_ids: set[str] = set()
    for fc in filtered:
        for key in ("candidate_id", "doc_id", "source_id"):
            val = str(fc.get(key, "") or "")
            if val:
                excluded_ids.add(val)

    def _touches_excluded(rel: dict[str, Any], a: str, b: str) -> bool:
        return str(rel.get(a, "")) in excluded_ids or str(rel.get(b, "")) in excluded_ids

    # Revision-bound edges (served re-retrieval) prove scope directly: an
    # edge naming a revision outside the resolved set is dropped, even when
    # its endpoints name no known candidate.
    proven_revisions = {
        str(c.get("revision_id", "") or "")
        for c in candidates
        if c.get("_resolution") == RESOLVED and c.get("revision_id")
    }
    rel_out = []
    for r in relations:
        rd = dict(r)
        edge_rev = str(rd.get("revision_id", "") or "")
        if edge_rev and conn is not None and edge_rev not in proven_revisions:
            omissions.append(
                {
                    "kind": "relation_unresolved_revision",
                    "reason": "edge revision outside the resolved candidate set",
                    "rel_type": str(rd.get("rel_type", "")),
                    "revision_id": edge_rev,
                }
            )
        elif _touches_excluded(rd, "subject", "object"):
            omissions.append(
                {
                    "kind": "relation_filtered",
                    "reason": "endpoint names scope/version-filtered evidence",
                    "rel_type": str(rd.get("rel_type", "")),
                    "subject": str(rd.get("subject", "")),
                    "object": str(rd.get("object", "")),
                }
            )
        else:
            rel_out.append(rd)
    for c in visible:
        for r in c.get("relations", []):
            if r not in rel_out:
                rel_out.append(dict(r))
    qual_edges = [r for r in rel_out if str(r.get("rel_type", "")) == "qualifies"]
    seen_dis: set[tuple[str, str]] = set()
    dis_out: list[dict[str, Any]] = []
    for d in disagreements:
        dd = dict(d)
        if _touches_excluded(dd, "side_a", "side_b"):
            omissions.append(
                {
                    "kind": "disagreement_filtered",
                    "reason": "side names scope/version-filtered evidence",
                    "side_a": str(dd.get("side_a", "")),
                    "side_b": str(dd.get("side_b", "")),
                }
            )
        else:
            dis_out.append(dd)
    for r in rel_out:
        if str(r.get("rel_type", "")) in CONFLICT_REL_TYPES:
            key = (str(r.get("subject", "")), str(r.get("object", "")))
            if key not in seen_dis:
                seen_dis.add(key)
                dis_out.append(
                    {
                        "side_a": key[0],
                        "side_b": key[1],
                        "rel_type": str(r.get("rel_type", "")),
                        "evidence": str(r.get("evidence", "")),
                    }
                )
    stances: dict[str, set[str]] = {}
    for s in selections:
        for r in s.relations:
            if str(r.get("rel_type", "")) in CONFLICT_REL_TYPES:
                stances.setdefault(str(r.get("scope", "claim")), set()).add(s.candidate_id)

    # 6. Support assessment (named heuristic; relevant ≠ supported).
    # Conservative: degraded citations are unproven and never count as
    # support; an all-degraded relevant set stays insufficient (never
    # marketed upward by lexical overlap).
    supported_ids = [
        s.candidate_id
        for s in selections
        if not s.citation_degraded
        and _lexical_support(s.excerpt_text, objective or query)
    ]
    n_rel, n_sup = len(relevant), len(supported_ids)
    if coverage["coverage_gap_after_filter"]:
        support: SupportState = SupportState.INSUFFICIENT
    elif n_rel == 0:
        support = SupportState.INSUFFICIENT
    elif dis_out:
        support = SupportState.CONFLICTING
    elif n_sup == 0:
        support = SupportState.INSUFFICIENT
    elif n_sup / max(n_rel, 1) >= support_coverage:
        support = SupportState.SUPPORTED
    else:
        support = SupportState.PARTIAL

    # 7. Cross-source virtual holon (transient, derived, with lineage).
    # Same visibility rule: members are selections only, and the holon is
    # withheld unless every member source stayed visible.
    virtuals: list[VirtualHolon] = []
    visible_docs = {
        s.doc_id or s.source_id for s in selections if (s.doc_id or s.source_id)
    }
    sources = sorted(visible_docs)
    if len(sources) >= 2:
        digest = hashlib.sha256(
            "|".join(sorted(s.candidate_id for s in selections)).encode("utf-8")
        ).hexdigest()[:16]
        vh = VirtualHolon(
            holon_id=f"qdossier-{digest}",
            member_ids=[s.candidate_id for s in selections],
            member_sources=sources,
            derivation=(
                f"query-time virtual holon over {len(selections)} selections "
                f"from {len(sources)} sources for query {query!r}; "
                f"method={SUPPORT_METHOD}; lineage=pinned snapshot "
                f"{snapshot.get('generation_id', '?')}"
            ),
            qualification_edges=qual_edges,
        )
        if set(vh.member_sources) <= visible_docs and set(vh.member_ids) <= {
            s.candidate_id for s in selections
        }:
            virtuals.append(vh)

    # 8. Synthesis (optional) is always derived, never ground truth — and
    # scope-enforced: references to filtered-out candidates are surfaced,
    # never silently carried as lineage.
    synthesis_block: dict[str, Any] = {}
    if synthesis_text:
        synthesis_block = {
            "text": synthesis_text,
            "status": "derived",
            "lineage": synthesis_lineage or f"derived from {[s.candidate_id for s in selections]}",
            "scope_enforced": True,
        }
        if excluded_ids:
            haystack = f"{synthesis_text} {synthesis_lineage}"
            leaked = sorted(e for e in excluded_ids if e and e in haystack)
            if leaked:
                omissions.append(
                    {
                        "kind": "synthesis_references_filtered",
                        "id": leaked[0],
                        "reason": "synthesis names scope/version-filtered evidence;"
                                  " lineage restricted to selections",
                    }
                )
    total = reserved + evidence_token_total
    return base(
        "compiled",
        status=DossierStatus.OK,
        selections=selections,
        virtual_holons=virtuals,
        relations=rel_out,
        disagreements=dis_out,
        omissions=omissions,
        support_state=support,
        relevant_candidate_count=n_rel,
        supported_count=n_sup,
        candidate_routes=routes,
        coverage_report=coverage,
        budget=budget_block,
        token_accounting={
            "mandatory_cost": mandatory_cost,
            "goal_cost": goal_cost,
            "evidence_cost": evidence_token_total,
            "total": total,
            "synthesis": synthesis_block,
        },
    )


def _lexical_support(text: str, objective: str) -> bool:
    """Heuristic support vote: ≥ half the objective terms appear in the text."""
    obj = _terms(objective)
    if not obj:
        return False
    return len(_terms(text) & obj) / len(obj) >= 0.5
