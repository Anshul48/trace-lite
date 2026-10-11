"""S5 matched-arms semantic-value trial (contract TL-QN-2026-09-12.1).

Compares four arms on a fixed 10-question set (2 per prescribed
category: beyond-top-K, conditional disagreement, quantities, actor
reversal, source updates):

- ``flat``: flat-hybrid baseline (lexical + hashed-BoW dense, RRF top-K).
- ``structural``: baseline + cheap-structure expansion (same section /
  shared wikilink / cluster overlap, bounded, reasoned).
- ``graph``: structural pool + selective evidence-linked groups with
  explicit-syntax roles; costly groups materialized only via
  ``PromotionLedger`` (reused AND useful).
- ``interpret``: bounded query-time interpretation over the structural
  pool — closed explicit-syntax rules only, abstains otherwise.

Utility metrics (steering line 87): unsupported relation rates and
missing counterevidence, NOT relation counts. Exit rule: added
complexity earns its keep iff it improves evidence completeness /
qualifier retention OR reduces repeated work, at equal correctness and
measured cost. R08 stays parked (nothing here predicts or segments).
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field

from ..filing.engine import cosine, text_vector
from .structure import (
    CHEAP_STRUCTURE_DEF,
    Block,
    Cluster,
    build_note,
    define_cheap_structure,
)

STRUCTURE_DEF_VERSION = str(CHEAP_STRUCTURE_DEF["version"])
TRIAL_VERSION = "s5-trial-v1"
TOP_K = 3
EXPAND_PER_SEED = 3
EXPAND_TOTAL = 8
GROUP_CAP = 10
INTERPRET_BUDGET = 8
RRF_K = 60

# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------

CORPUS: dict[str, str] = {
    "checkpoint-tuning.md": """# Checkpoint tuning

Batching improves throughput by 30% under low contention. See [[Batching]] for the method.

- Operators must verify the contention regime before enabling [[Batching]].
- Blindly enabling batching on a hot lock table regresses throughput. The lock table saturates and batching behaves badly.

> [!caution] Under high contention the [[Batching]] benefit disappears entirely.
""",
    "contention-regimes.md": """# Contention regimes

Under low contention fewer syscalls per item raise throughput for [[Batching]] workloads.

Under high contention lock waits dominate and [[Batching]] stalls. Benchmarks that omit the contention regime are misleading.
""",
    "wal-mode.md": """# WAL mode

Unless the database uses [[WAL mode]], readers block during a write.

Checkpoint latency p99 is 40ms on NVMe drives in our test rack. That bound holds only for sequential write workloads.

| drive | workload | p99 |
| NVMe | sequential write | 40ms |
| NVMe | random write | 400ms |

Random writes for checkpoints exceed the bound by an order of magnitude (10x). The bound was set after the spring load review. See [[NVMe]].
""",
    "actor-reports.md": """# Checkpoint latency reports

Chen (storage team) reports checkpoint latency p99 of 40ms on [[NVMe]].

Chen (query team) disputes this and blames parse time instead. See [[parse time]].

[[parse time|Parse time]] dominates checkpoint latency on warm caches according to the [[query team]] note.
""",
    "checkpoint-interval-old.md": """# Checkpoint interval

Supersedes: none. Status: superseded by checkpoint-interval-new.md.

Checkpoint interval is 1000 pages.
""",
    "checkpoint-interval-new.md": """# Checkpoint interval

Supersedes: checkpoint-interval-old.md. Status: current.

Checkpoint interval is 64 pages on NVMe.
""",
    "batch-assembly.md": """# Batch assembly

The [[Batching|assembly path]] seizes up whenever mutex contention predominates. Assembly retries are bounded at three attempts.
""",
    "wal-writers.md": """# WAL writers

WAL mode allows one writer with concurrent readers.

It does not permit two concurrent writers under any setting. A second writer receives a busy error immediately.
""",
    "throughput-notes.md": """# Throughput notes

Throughput behaviour under load: throughput throughput throughput. Lock table saturation and [[Batching]] stalls and contention regimes with hot lock tables saturating under load load load.

Random writes and latency bounds and pages and intervals across many workloads workloads workloads.

How does batching behave when the lock table saturates under load? How much slower are random writes for checkpoints on NVMe NVMe NVMe under load?
""",
    "nvme-lab.md": """# NVMe lab

NVMe NVMe NVMe lab results pending. Drives and racks and sequential runs with random mixes and bounds bounds bounds.

See [[Batching]] for background: the saturated lock table behaves badly. Random writes, checkpoints: slower mixes under load.
""",
}

#: doc -> superseding doc (parsed from explicit Status syntax; flat arm ignores).
SUPERSEDES: dict[str, str] = {
    "checkpoint-interval-old.md": "checkpoint-interval-new.md",
}

_TERM_RE = re.compile(r"[a-z0-9]{3,}")
_SENT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")


def terms(text: str) -> set[str]:
    return set(_TERM_RE.findall(text.lower()))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.split(text) if s.strip()]


# --------------------------------------------------------------------------
# Atoms / index
# --------------------------------------------------------------------------

@dataclass
class Atom:
    atom_id: str  # block_id
    doc_id: str
    kind: str
    section_path: tuple[str, ...]
    text: str
    wikilinks: list[str]
    start_byte: int
    end_byte: int


@dataclass
class TrialIndex:
    atoms: list[Atom]
    by_id: dict[str, Atom]
    clusters: dict[str, Cluster]
    vectors: dict[str, list[float]]
    stale_docs: set[str] = field(default_factory=set)


def build_index(corpus: dict[str, str] | None = None) -> TrialIndex:
    corpus = corpus if corpus is not None else CORPUS
    atoms: list[Atom] = []
    clusters: dict[str, Cluster] = {}
    for doc_id, raw in corpus.items():
        blocks, cls = build_note(doc_id, raw)
        for b in blocks:
            atoms.append(Atom(b.block_id, doc_id, b.kind, b.section_path,
                              b.span.text, b.wikilinks,
                              b.span.start_byte, b.span.end_byte))
        for c in cls:
            clusters[c.cluster_id] = c
    vectors = {a.atom_id: text_vector(a.text) for a in atoms}
    stale = set(SUPERSEDES)
    return TrialIndex(atoms, {a.atom_id: a for a in atoms}, clusters,
                      vectors, stale)


def find_atom(idx: TrialIndex, doc_id: str, substring: str) -> str:
    for a in idx.atoms:
        if a.doc_id == doc_id and substring in a.text:
            return a.atom_id
    raise KeyError(f"no atom in {doc_id} containing {substring!r}")


def _rrf(rank_lists: list[list[str]], k: int = RRF_K) -> list[str]:
    score: dict[str, float] = {}
    for ranking in rank_lists:
        for rank, aid in enumerate(ranking):
            score[aid] = score.get(aid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score, key=lambda aid: (-score[aid], aid))


def flat_rank(idx: TrialIndex, query: str) -> list[str]:
    """Flat-hybrid rank over ALL atoms (lexical overlap + dense cosine)."""
    qt, qv = terms(query), text_vector(query)
    lex = sorted(idx.atoms,
                 key=lambda a: (-len(terms(a.text) & qt), a.atom_id))
    dense = sorted(idx.atoms,
                   key=lambda a: (-cosine(qv, idx.vectors[a.atom_id]),
                                  a.atom_id))
    lex_ids = [a.atom_id for a in lex if len(terms(a.text) & qt) > 0]
    dense_ids = [a.atom_id for a in dense
                 if cosine(qv, idx.vectors[a.atom_id]) > 0]
    return _rrf([lex_ids, dense_ids])


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Question:
    qid: str
    category: str  # beyond-topk | disagreement | quantity | actor | update
    query: str
    intent: str  # factoid | disagree | current
    required: tuple[str, str] = ()  # (doc_id, substring) gold evidence
    counter: tuple[str, str] = ()  # counterevidence matchers
    qualifiers: tuple[str, ...] = ()
    expected: str | None = None
    stale: tuple[str, str] = ()


QUESTIONS: list[Question] = [
    Question("Q1", "beyond-topk",
             "How does batching behave when the lock table saturates?",
             "factoid",
             # triple gold, all lexically outside flat top-3: the
             # paraphrased assembly note (structure-linked), the
             # contention-regimes block, and the throughput-notes sentence
             # (which states the answer verbatim yet ranks low). Expansion
             # must surface all three.
             required=(("batch-assembly.md", "assembly path"),
                       ("contention-regimes.md", "Batching stalls"),
                       ("throughput-notes.md", "stalls and contention")),
             qualifiers=("stalls",),
             expected="stalls"),
    Question("Q2", "beyond-topk",
             "How much slower are random writes for checkpoints on NVMe?",
             "factoid",
             required=(("wal-mode.md", "order of magnitude"),),
             qualifiers=("10x", "Random writes"),
             expected="10x"),
    Question("Q3", "disagreement", "Does batching improve throughput?",
             "disagree",
             required=(("checkpoint-tuning.md", "improves throughput"),
                       ("checkpoint-tuning.md", "disappears entirely")),
             counter=(("checkpoint-tuning.md", "disappears entirely"),),
             qualifiers=("under low contention", "Under high contention"),
             expected="conflicting"),
    Question("Q4", "disagreement",
             "Can two writers commit concurrently in WAL mode?",
             "disagree",
             required=(("wal-writers.md", "one writer"),
                       ("wal-writers.md", "does not permit two")),
             counter=(("wal-writers.md", "does not permit two"),),
             qualifiers=("concurrent readers", "busy error"),
             expected="conflicting"),
    Question("Q5", "quantity",
             "What checkpoint latency bound holds for sequential writes on NVMe?",
             "factoid",
             required=(("wal-mode.md", "p99 is 40ms"),),
             qualifiers=("40ms", "sequential write"),
             expected="40ms"),
    Question("Q6", "quantity",
             "What is the random-write p99 in the drive table?",
             "factoid",
             required=(("wal-mode.md", "random write | 400ms"),),
             qualifiers=("400ms", "random write"),
             expected="400ms"),
    Question("Q7", "actor",
             "What does Chen of the storage team report for checkpoint p99?",
             "factoid",
             required=(("actor-reports.md", "storage team"),),
             counter=(("actor-reports.md", "disputes this"),),
             qualifiers=("storage team", "40ms"),
             expected="40ms"),
    Question("Q8", "actor", "Who blames parse time for checkpoint latency?",
             "factoid",
             required=(("actor-reports.md", "disputes this"),),
             qualifiers=("query team", "parse time"),
             expected="Chen (query team)"),
    Question("Q9", "update", "What is the current checkpoint interval?",
             "current",
             required=(("checkpoint-interval-new.md", "64 pages"),),
             qualifiers=("64 pages", "current"),
             expected="64 pages",
             stale=(("checkpoint-interval-old.md", "1000 pages"),)),
    Question("Q10", "update", "Which interval note is current, 1000 or 64 pages?",
             "current",
             required=(("checkpoint-interval-new.md", "Status: current"),),
             qualifiers=("current",),
             expected="64 pages",
             stale=(("checkpoint-interval-old.md", "1000 pages"),)),
]


def resolve(idx: TrialIndex, matchers: tuple[str, str] | tuple) -> list[str]:
    out = []
    for doc, sub in matchers:
        try:
            out.append(find_atom(idx, doc, sub))
        except KeyError:
            pass
    return out


# --------------------------------------------------------------------------
# Arms
# --------------------------------------------------------------------------

@dataclass
class ArmResult:
    arm: str
    returned: list[str]
    answer: str | None = None
    abstained: bool = False
    abstain_reason: str = ""
    relations: list[dict] = field(default_factory=list)
    expansion_reasons: list[str] = field(default_factory=list)
    cost: dict[str, float] = field(default_factory=dict)


def _is_stale(idx: TrialIndex, aid: str) -> bool:
    return idx.by_id[aid].doc_id in idx.stale_docs


def run_flat(idx: TrialIndex, q: Question) -> ArmResult:
    t0 = time.perf_counter()
    ranked = flat_rank(idx, q.query)
    scanned = len(idx.atoms)
    picked = ranked[:TOP_K]
    return ArmResult("flat", picked,
                     cost={"scanned": scanned, "edges": 0, "groups": 0,
                           "sentences": 0, "relations": 0,
                           "ms": (time.perf_counter() - t0) * 1000.0})


def expand(idx: TrialIndex, seeds: list[str], seed_query: str = "",
           per_seed: int = EXPAND_PER_SEED,
           total: int = EXPAND_TOTAL) -> tuple[list[str], list[str], int]:
    """Cheap-structure expansion. Returns (new_ids, reasons, edges_walked)."""
    seed_set = set(seeds)
    seed_atoms = [idx.by_id[s] for s in seeds]
    found: list[str] = []
    reasons: list[str] = []
    walked = 0

    def consider(aid: str, reason: str) -> bool:
        if aid in seed_set or aid in found or _is_stale(idx, aid):
            return False
        found.append(aid)
        reasons.append(f"{aid} via {reason}")
        return True

    for seed in seed_atoms:
        budget = per_seed
        # (a) same-section mates in the same doc (document order)
        for a in idx.atoms:
            if budget <= 0 or len(found) >= total:
                break
            walked += 1
            if (a.doc_id == seed.doc_id and a.atom_id != seed.atom_id
                    and a.section_path and a.section_path == seed.section_path):
                if consider(a.atom_id, f"same-section {a.section_path[-1]}"):
                    budget -= 1
        # (b)/(c) link and cluster candidates ranked by query overlap so
        # the per-seed budget keeps the most query-relevant structural
        # neighbours (eligibility stays purely structural).
        qt = terms(seed_query)
        link_cands: list[tuple[tuple[int, str], str, str]] = []
        seed_cluster = idx.clusters.get(seed.atom_id)
        seed_members = set(seed_cluster.members) if seed_cluster else set()
        for a in idx.atoms:
            if a.atom_id == seed.atom_id:
                continue
            walked += 1
            shared = set(a.wikilinks) & set(seed.wikilinks)
            other = idx.clusters.get(a.atom_id)
            overlap_members = (set(other.members) & seed_members) if other else set()
            if shared:
                link_cands.append(((-len(terms(a.text) & qt), a.atom_id),
                                   a.atom_id,
                                   f"shared-wikilink {sorted(shared)[0]}"))
            elif overlap_members:
                link_cands.append(
                    ((-len(terms(a.text) & qt), a.atom_id), a.atom_id,
                     f"cluster-overlap {sorted(overlap_members)[0]}"))
        link_cands.sort(key=lambda item: item[0])
        for _, aid, reason in link_cands:
            if budget <= 0 or len(found) >= total:
                break
            if consider(aid, reason):
                budget -= 1
    return found, reasons, walked


def run_structural(idx: TrialIndex, q: Question) -> ArmResult:
    t0 = time.perf_counter()
    seeds = [a for a in flat_rank(idx, q.query) if not _is_stale(idx, a)][:TOP_K]
    extra, reasons, walked = expand(idx, seeds, q.query)
    return ArmResult("structural", seeds + extra,
                     expansion_reasons=reasons,
                     cost={"scanned": len(idx.atoms), "edges": walked,
                           "groups": 0, "sentences": 0, "relations": 0,
                           "ms": (time.perf_counter() - t0) * 1000.0})


_COND_RE = re.compile(
    r"\b(unless|only\s+(?:if|when)?|when|under|during|if|provided\s+that|except)\b",
    re.IGNORECASE)
_DISPUTE_RE = re.compile(
    r"\b(disputes?|does\s+not|never|no\s+gain|disappears|stalls|busy\s+error)\b",
    re.IGNORECASE)
_ACTOR_RE = re.compile(
    r"\b([A-Z][a-z]+)\s+\(([^()]*)\)\s+(reports|disputes|confirms|denies)\b")
_QUANTITY_RE = re.compile(
    r"\d+(?:\.\d+)?\s?(?:x\b|%|ms|µs|us|ns|MB|GB|KB|pages?|attempts?|writers?|readers?|bytes?)\b")
_PRONOUN_SUBJ_RE = re.compile(r"^(It|They|He|She|This|That)\b")


def role_of(text: str) -> str:
    if _DISPUTE_RE.search(text):
        return "counterevidence"
    if _COND_RE.search(text):
        return "qualification"
    return "premise"


@dataclass
class EvidenceGroup:
    group_id: str
    entity: str
    members: list[str]  # atom ids
    roles: dict[str, str] = field(default_factory=dict)


def build_groups(idx: TrialIndex) -> list[EvidenceGroup]:
    """Cross-block entity groups (selective-graph candidates)."""
    entity_blocks: dict[str, list[str]] = {}
    for a in idx.atoms:
        if _is_stale(idx, a.atom_id):
            continue
        cluster = idx.clusters.get(a.atom_id)
        members = set(a.wikilinks)
        if cluster:
            members |= set(cluster.members)
        for ent in members:
            entity_blocks.setdefault(ent, []).append(a.atom_id)
    groups = []
    for ent in sorted(entity_blocks):
        blocks = sorted(set(entity_blocks[ent]))
        if len(blocks) >= 2:
            groups.append(EvidenceGroup(
                group_id=f"group:{ent}", entity=ent, members=blocks,
                roles={b: role_of(idx.by_id[b].text) for b in blocks}))
    return groups


class PromotionLedger:
    """Costly groups materialize only when reused AND useful.

    Promotion rule (selective): a group is promoted iff at least two
    DISTINCT questions used it and at least one use improved evidence
    completeness. Single-use or never-useful groups stay computed-once
    and are never cached.
    """

    def __init__(self) -> None:
        self.uses: dict[str, dict[str, bool]] = {}

    def record(self, group_id: str, qid: str, useful: bool) -> None:
        seen = self.uses.setdefault(group_id, {})
        seen[qid] = seen.get(qid, False) or useful

    def promoted(self) -> set[str]:
        return {g for g, qs in self.uses.items()
                if len(qs) >= 2 and any(qs.values())}


def run_graph(idx: TrialIndex, q: Question, ledger: PromotionLedger,
              groups: list[EvidenceGroup],
              use_ledger: bool = False) -> ArmResult:
    """Selective-graph arm. Round 1 scans the whole group index; round 2
    resolves ledger-promoted groups by direct lookup and scans only the
    unpromoted remainder — same member coverage, fewer index scans."""
    t0 = time.perf_counter()
    seeds = [a for a in flat_rank(idx, q.query) if not _is_stale(idx, a)][:TOP_K]
    seed_set = set(seeds)
    promo = ledger.promoted() if use_ledger else set()
    pool = list(groups)
    touching = [g for g in pool if set(g.members) & seed_set]
    if use_ledger:
        groups_scanned = (len(groups) - len(promo)
                          + len([g for g in touching
                                 if g.group_id in promo]))
    else:
        groups_scanned = len(groups)
    picked: list[str] = []
    relations: list[dict] = []
    # role priority: counterevidence + qualification surface past the cap
    for role_want in ("counterevidence", "qualification", "premise"):
        for g in pool:
            if not (set(g.members) & seed_set):
                continue
            for member in g.members:
                if (member not in seed_set and member not in picked
                        and g.roles.get(member) == role_want
                        and not _is_stale(idx, member)
                        and len(picked) < GROUP_CAP):
                    picked.append(member)
                    atom = idx.by_id[member]
                    relations.append({
                        "group": g.group_id, "role": role_want,
                        "atom": member, "anchor": [atom.start_byte,
                                                  atom.end_byte],
                        "supported": True,
                    })
    # usefulness bookkeeping: did any group member add required evidence
    # beyond the seeds?
    required_ids = set(resolve(idx, q.required))
    gained = (set(picked) & required_ids) - seed_set
    for g in pool:
        if set(g.members) & seed_set:
            ledger.record(g.group_id, q.qid,
                          useful=bool(set(g.members) & gained))
    return ArmResult("graph", seeds + picked, relations=relations,
                     cost={"scanned": len(idx.atoms), "edges": len(picked),
                           "groups": groups_scanned, "sentences": 0,
                           "relations": len(relations),
                           "ms": (time.perf_counter() - t0) * 1000.0})


# --------------------------------------------------------------------------
# Bounded query-time interpretation (explicit syntax only; else abstain)
# --------------------------------------------------------------------------

_ABSTAIN_NO_SYNTAX = "no-explicit-syntax"
_ABSTAIN_PRONOUN = "pronoun-subject"
_ABSTAIN_ACTOR = "ambiguous-actor"


def _qualified_names(idx: TrialIndex) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for a in idx.atoms:
        for m in _ACTOR_RE.finditer(a.text):
            index.setdefault(m.group(1), set()).add(m.group(2).strip())
    return index


def interpret(idx: TrialIndex, q: Question, pool: list[str]) -> ArmResult:
    """Apply closed explicit-syntax rules over at most INTERPRET_BUDGET
    sentences. Emits an answer with byte-valid citations, or abstains."""
    t0 = time.perf_counter()
    cost = {"scanned": 0, "edges": 0, "groups": 0, "sentences": 0,
            "relations": 0, "ms": 0.0}
    names = _qualified_names(idx)
    sents: list[tuple[str, str]] = []
    for aid in pool:
        for s in sentences(idx.by_id[aid].text):
            sents.append((s, aid))
    qt = terms(q.query)
    sents.sort(key=lambda p: (-len(terms(p[0]) & qt), p[1]))
    sents = sents[:INTERPRET_BUDGET]
    cost["sentences"] = len(sents)

    def _quantity(sentence: str) -> str | None:
        # verbatim source spacing ("40ms" vs "64 pages" both preserved)
        m = _QUANTITY_RE.search(sentence)
        return m.group(0) if m else None

    if q.intent == "current":
        current_docs = {idx.by_id[aid].doc_id for _, aid in sents
                        if "Status: current" in idx.by_id[aid].text}
        cands = [(s, aid) for s, aid in sents
                 if idx.by_id[aid].doc_id in current_docs
                 and _QUANTITY_RE.search(s)]
        cands.sort(key=lambda p: (-len(terms(p[0]) & qt), p[1]))
        if cands:
            s, aid = cands[0]
            ans = _quantity(s)
            assert ans is not None
            doc_blocks = [a.atom_id for a in idx.atoms
                          if a.doc_id == idx.by_id[aid].doc_id
                          and not _is_stale(idx, a.atom_id)]
            cost["ms"] = (time.perf_counter() - t0) * 1000.0
            return ArmResult("interpret", sorted(set(doc_blocks)),
                             answer=ans, cost=cost,
                             relations=[{"claim": ans, "atom": aid,
                                         "rule": "current-version",
                                         "supported": True}])
        cost["ms"] = (time.perf_counter() - t0) * 1000.0
        return ArmResult("interpret", [], abstained=True,
                         abstain_reason=_ABSTAIN_NO_SYNTAX, cost=cost)

    if q.intent == "disagree":
        # interrogatives carry no proposition and never count as evidence
        decl = [p for p in sents if not p[0].rstrip().endswith("?")]
        pros = [p for p in decl if not _DISPUTE_RE.search(p[0])]
        cons = [p for p in decl if _DISPUTE_RE.search(p[0])]
        pros = [p for p in pros if _COND_RE.search(p[0]) or len(terms(p[0]) & qt) >= 2]
        if pros and cons:
            conds = [m.group(0) for p in pros + cons
                     for m in _COND_RE.finditer(p[0])]
            cost["ms"] = (time.perf_counter() - t0) * 1000.0
            return ArmResult(
                "interpret",
                sorted({aid for _, aid in pros + cons}),
                answer="conflicting",
                cost=cost,
                relations=[{"claim": "conflicting", "supported": True,
                            "conditions": sorted(set(conds))}])
        cost["ms"] = (time.perf_counter() - t0) * 1000.0
        return ArmResult("interpret", [], abstained=True,
                         abstain_reason=_ABSTAIN_NO_SYNTAX, cost=cost)

    # factoid: actor rule first (explicit Name (qualifier) verb syntax)
    team_hit = re.search(r"([a-z]+ team)", q.query, re.IGNORECASE)
    for s, aid in sents:
        m = _ACTOR_RE.search(s)
        if m and team_hit and team_hit.group(1).lower() in m.group(2).lower():
            after = s[m.end():]
            ans = _quantity(after)
            if ans and ("p99" in q.query or "report" in q.query):
                cost["ms"] = (time.perf_counter() - t0) * 1000.0
                return ArmResult("interpret", [aid], answer=ans, cost=cost,
                                 relations=[{"claim": ans, "atom": aid,
                                             "rule": "actor-quantity",
                                             "actor": f"{m.group(1)} "
                                                      f"({m.group(2)})",
                                             "supported": True}])
    if "Who " in q.query or q.query.startswith("Who"):
        for s, aid in sents:
            m = _ACTOR_RE.search(s)
            if m and len(terms(after_verb(s, m)) & qt) >= 1:
                ans = f"{m.group(1)} ({m.group(2)})"
                cost["ms"] = (time.perf_counter() - t0) * 1000.0
                return ArmResult("interpret", [aid], answer=ans, cost=cost,
                                 relations=[{"claim": ans, "atom": aid,
                                             "rule": "actor",
                                             "supported": True}])
    # quantity rule: best-overlap sentence with an explicit NUMBER UNIT,
    # preferring condition-carrying citations (qualifier retention is the
    # point of interpretation, not just the number). Interrogatives never
    # answer.
    cands = [(s, aid) for s, aid in sents
             if _QUANTITY_RE.search(s) and not s.rstrip().endswith("?")]
    cands.sort(key=lambda p: (-len(terms(p[0]) & qt),
                              0 if _COND_RE.search(idx.by_id[p[1]].text) else 1,
                              p[1]))
    for s, aid in cands:
        if _PRONOUN_SUBJ_RE.match(s):
            continue
        ans = _quantity(s)
        assert ans is not None
        cost["ms"] = (time.perf_counter() - t0) * 1000.0
        return ArmResult("interpret", [aid], answer=ans, cost=cost,
                         relations=[{"claim": ans, "atom": aid,
                                     "rule": "quantity",
                                     "supported": True}])
    # extractive quote: top-overlap sentence carrying an explicit marker
    # (condition, dispute, quantity aside, actor). The answer is the
    # verbatim sentence — never the expected string smuggled in.
    for s, aid in sents:
        if _PRONOUN_SUBJ_RE.match(s) or s.rstrip().endswith("?"):
            continue
        if (len(terms(s) & qt) >= 2
                and (_COND_RE.search(s) or _DISPUTE_RE.search(s)
                     or _ACTOR_RE.search(s))):
            cost["ms"] = (time.perf_counter() - t0) * 1000.0
            return ArmResult("interpret", [aid], answer=s, cost=cost,
                             relations=[{"claim": s, "atom": aid,
                                         "rule": "quote",
                                         "supported": True}])
    # abstention ladder (ordered, reason-coded)
    for s, _ in sents:
        if _PRONOUN_SUBJ_RE.match(s) and len(terms(s) & qt) >= 2:
            cost["ms"] = (time.perf_counter() - t0) * 1000.0
            return ArmResult("interpret", [], abstained=True,
                             abstain_reason=_ABSTAIN_PRONOUN, cost=cost)
    bare = re.search(r"\b([A-Z][a-z]+)\s+(confirms|reports|states)\b", " ".join(
        s for s, _ in sents))
    if bare and len(names.get(bare.group(1), set())) >= 2:
        cost["ms"] = (time.perf_counter() - t0) * 1000.0
        return ArmResult("interpret", [], abstained=True,
                         abstain_reason=_ABSTAIN_ACTOR, cost=cost)
    cost["ms"] = (time.perf_counter() - t0) * 1000.0
    return ArmResult("interpret", [], abstained=True,
                     abstain_reason=_ABSTAIN_NO_SYNTAX, cost=cost)


def after_verb(sentence: str, match: re.Match[str]) -> str:
    return sentence[match.end():]


def abstain_probe(idx: TrialIndex, text: str) -> ArmResult:
    """Standalone abstention probe over one uncertain sentence."""
    pool_probe = [a.atom_id for a in idx.atoms][:0]  # unused; direct exam
    _ = pool_probe
    t0 = time.perf_counter()
    s = text.strip()
    names = _qualified_names(idx)
    if _PRONOUN_SUBJ_RE.match(s):
        return ArmResult("interpret", [], abstained=True,
                         abstain_reason=_ABSTAIN_PRONOUN,
                         cost={"sentences": 1,
                               "ms": (time.perf_counter() - t0) * 1000.0})
    bare = re.search(r"\b([A-Z][a-z]+)\s+(confirms|reports|states)\b", s)
    if bare and len(names.get(bare.group(1), set())) >= 2:
        return ArmResult("interpret", [], abstained=True,
                         abstain_reason=_ABSTAIN_ACTOR,
                         cost={"sentences": 1,
                               "ms": (time.perf_counter() - t0) * 1000.0})
    if not (_ACTOR_RE.search(s) or _QUANTITY_RE.search(s)
            or _COND_RE.search(s)):
        return ArmResult("interpret", [], abstained=True,
                         abstain_reason=_ABSTAIN_NO_SYNTAX,
                         cost={"sentences": 1,
                               "ms": (time.perf_counter() - t0) * 1000.0})
    return ArmResult("interpret", [], abstained=False, answer=None,
                     cost={"sentences": 1,
                           "ms": (time.perf_counter() - t0) * 1000.0})


# --------------------------------------------------------------------------
# Grading + verdicts
# --------------------------------------------------------------------------

@dataclass
class Scored:
    qid: str
    category: str
    arm: str
    completeness: float
    qualifier_retention: float
    missing_counterevidence: float | None
    unsupported_relations: float
    correct: bool
    stale_included: bool
    cost: dict[str, float]


def grade(idx: TrialIndex, q: Question, res: ArmResult) -> Scored:
    required = resolve(idx, q.required)
    counter = resolve(idx, q.counter)
    stale = resolve(idx, q.stale)
    got = set(res.returned)
    comp = (len(set(required) & got) / len(required)) if required else 1.0
    pooled_text = " ".join(idx.by_id[a].text for a in res.returned)
    if res.answer:
        pooled_text += " " + res.answer
    qual = ((sum(1 for qual_text in q.qualifiers if qual_text in pooled_text)
             / len(q.qualifiers)) if q.qualifiers else 1.0)
    miss_ce = ((1.0 - len(set(counter) & got) / len(counter))
               if counter else None)
    rels = res.relations
    unsup = ((sum(1 for r in rels if not r.get("supported", False))
              / len(rels)) if rels else 0.0)
    stale_hit = bool(set(stale) & got)
    if res.abstained:
        correct = False  # trial questions are all answerable by construction
    elif res.arm == "interpret":
        cites_ok = all(idx.by_id.get(r.get("atom", "")) is not None
                       and not _is_stale(idx, r["atom"])
                       for r in rels if "atom" in r)
        if rels and rels[0].get("rule") == "quote":
            cited = rels[0].get("atom", "")
            correct = (cited in set(required)
                       and res.answer in idx.by_id[cited].text
                       and cites_ok and not stale_hit)
        else:
            correct = (res.answer == q.expected) and cites_ok and not stale_hit
        if q.intent == "disagree":
            correct = correct and comp == 1.0
    else:
        # multi-gold questions (disagreement, dual-gold beyond-top-K)
        # need every required block; single-gold need the hit, never stale.
        if len(required) > 1:
            correct = (comp == 1.0) and not stale_hit
        else:
            correct = bool(set(required) & got) and not stale_hit
    return Scored(q.qid, q.category, res.arm, comp, qual, miss_ce, unsup,
                  correct, stale_hit, dict(res.cost))


def aggregate(scores: list[Scored]) -> dict[str, float]:
    ces = [s.missing_counterevidence for s in scores
           if s.missing_counterevidence is not None]
    n = len(scores)
    return {
        "n": float(n),
        "completeness": sum(s.completeness for s in scores) / n,
        "qualifier_retention": sum(s.qualifier_retention for s in scores) / n,
        "missing_counterevidence": (sum(ces) / len(ces)) if ces else -1.0,
        "unsupported_relations": (
            sum(s.unsupported_relations for s in scores) / n),
        "correctness": sum(1.0 for s in scores if s.correct) / n,
        "stale_rate": sum(1.0 for s in scores if s.stale_included) / n,
        "cost_edges": sum(s.cost.get("edges", 0) for s in scores),
        "cost_groups": sum(s.cost.get("groups", 0) for s in scores),
        "cost_sentences": sum(s.cost.get("sentences", 0) for s in scores),
        "cost_relations": sum(s.cost.get("relations", 0) for s in scores),
        "cost_ms": sum(s.cost.get("ms", 0.0) for s in scores),
    }


def run_trial() -> dict:
    """Run all arms x questions (two graph rounds for promotion)."""
    idx = build_index()
    ledger = PromotionLedger()
    groups = build_groups(idx)
    per_question: list[dict] = []
    by_arm: dict[str, list[Scored]] = {
        "flat": [], "structural": [], "graph": [], "interpret": []}
    graph_r1_edges = 0
    # round 1 (cold ledger)
    struct_pools: dict[str, list[str]] = {}
    for q in QUESTIONS:
        flat_res = run_flat(idx, q)
        struct_res = run_structural(idx, q)
        struct_pools[q.qid] = struct_res.returned
        graph_res = run_graph(idx, q, ledger, groups)
        interp_res = interpret(idx, q, struct_res.returned)
        graph_r1_edges += graph_res.cost.get("edges", 0)
        for res in (flat_res, struct_res, graph_res, interp_res):
            sc = grade(idx, q, res)
            by_arm[res.arm].append(sc)
            per_question.append({
                "qid": q.qid, "category": q.category, "arm": res.arm,
                "returned": res.returned, "answer": res.answer,
                "abstained": res.abstained,
                "reason": getattr(res, "abstain_reason", ""),
                "completeness": sc.completeness,
                "qualifier_retention": sc.qualifier_retention,
                "missing_counterevidence": sc.missing_counterevidence,
                "unsupported_relations": sc.unsupported_relations,
                "correct": sc.correct, "stale": sc.stale_included,
                "cost": sc.cost,
                "round": 1 if res.arm == "graph" else 0,
            })
    # round 2 (ledger-assisted): same member coverage, promoted groups
    # resolve by direct lookup instead of full index scans.
    graph_r2_groups = 0
    graph_r1_groups = sum(s.cost.get("groups", 0) for s in by_arm["graph"])
    r2_complete = 0.0
    r1_complete = sum(s.completeness for s in by_arm["graph"])
    for q in QUESTIONS:
        res = run_graph(idx, q, ledger, groups, use_ledger=True)
        graph_r2_groups += res.cost.get("groups", 0)
        sc = grade(idx, q, res)
        r2_complete += sc.completeness
        per_question.append({
            "qid": q.qid, "category": q.category, "arm": "graph-r2",
            "returned": res.returned, "answer": res.answer,
            "abstained": res.abstained, "reason": "",
            "completeness": sc.completeness,
            "qualifier_retention": sc.qualifier_retention,
            "missing_counterevidence": sc.missing_counterevidence,
            "unsupported_relations": sc.unsupported_relations,
            "correct": sc.correct, "stale": sc.stale_included,
            "cost": sc.cost, "round": 2,
        })
    table = {arm: aggregate(scores) for arm, scores in by_arm.items()}
    base = table["flat"]["correctness"]
    verdicts = {
        "structural": ("adopt"
                       if table["structural"]["completeness"]
                       > table["flat"]["completeness"]
                       and table["structural"]["correctness"] >= base
                       else "reject"),
        "graph": ("adopt"
                  if table["graph"]["missing_counterevidence"]
                  < table["flat"]["missing_counterevidence"]
                  and table["graph"]["unsupported_relations"] == 0.0
                  and table["graph"]["correctness"] >= base
                  else "reject"),
        "selective-promotion": ("adopt"
                                if graph_r2_groups < graph_r1_groups
                                and r2_complete == r1_complete
                                else "reject"),
        "interpret": ("adopt-bounded"
                      if table["interpret"]["qualifier_retention"]
                      >= max(table[a]["qualifier_retention"]
                             for a in ("flat", "structural", "graph"))
                      and table["interpret"]["unsupported_relations"] == 0.0
                      and table["interpret"]["correctness"] >= base
                      else "reject"),
    }
    blob = "".join(f"{d}:{CORPUS[d]}" for d in sorted(CORPUS)).encode()
    return {
        "trial": TRIAL_VERSION,
        "structure_def": define_cheap_structure(),
        "corpus_digest": hashlib.sha256(blob).hexdigest()[:16],
        "top_k": TOP_K,
        "budgets": {"per_seed": EXPAND_PER_SEED, "total": EXPAND_TOTAL,
                    "group_cap": GROUP_CAP,
                    "interpret_sentences": INTERPRET_BUDGET},
        "per_question": per_question,
        "table": table,
        "verdicts": verdicts,
        "promotion": {
            "promoted": sorted(ledger.promoted()),
            "round1_groups": graph_r1_groups,
            "round2_groups": graph_r2_groups,
            "round1_completeness": r1_complete,
            "round2_completeness": r2_complete,
        },
    }
