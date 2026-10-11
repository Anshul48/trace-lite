"""Selective-repair comparison fixtures (R08 experimental, EVALUATION §6).

Compares R06-style deterministic dependency-closure invalidation against a
predictive scope estimator (hashbow embedding-residual threshold: skip
repair when the edited assertion's residual is small). Dataset: ordered
source revisions with meaning-changing and meaning-preserving edits plus
the expected affected assertions — the §6 diagnostic in miniature.

The thetas below are a priori reporting points, not tuned values: all
three are reported, none selected on outcomes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..filing.engine import text_vector

#: A priori residual thresholds reported (never selected between).
THETAS = (0.02, 0.05, 0.10)


def _residual(old: str, new: str) -> float:
    a, b = text_vector(old), text_vector(new)
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if denom == 0.0:
        return 0.0 if old == new else 1.0
    return max(0.0, 1.0 - sum(x * y for x, y in zip(a, b)) / denom)


@dataclass(frozen=True)
class Assertion:
    aid: str
    text: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class EditCase:
    edit_id: str
    target_aid: str
    old_text: str
    new_text: str
    meaning_changed: bool
    kind: str  # negation | actor-reversal | number-unit | qualifier-drop |
    #            paraphrase | typo | whitespace


@dataclass
class RepairComparison:
    edit_id: str
    kind: str
    meaning_changed: bool
    residual: float
    deterministic_invalidated: list[str]
    predictive_invalidated: dict[float, list[str]]
    expected_invalidated: list[str]


def base_assertions() -> list[Assertion]:
    """One miniature source: a claim, its qualifier, and dependents."""
    return [
        Assertion("A0", "WAL mode allows one writer with concurrent readers."),
        Assertion(
            "A1",
            "Unless the database uses WAL mode, readers block during a write.",
            depends_on=("A0",),
        ),
        Assertion(
            "A2",
            "Chen (storage team) reports checkpoint latency p99 of 40ms on NVMe.",
        ),
        Assertion(
            "A3",
            "Given A2, checkpoints are not the tail-latency bottleneck.",
            depends_on=("A2",),
        ),
        Assertion("A4", "Batching improves throughput by 30% under low contention."),
        Assertion(
            "A5",
            "Under high contention the batching benefit disappears entirely.",
            depends_on=("A4",),
        ),
    ]


def edit_cases() -> list[EditCase]:
    """Ordered revisions: meaning-changing micro-edits vs preserving edits."""
    by_id = {a.aid: a.text for a in base_assertions()}
    return [
        EditCase(
            "E1-negation",
            "A0",
            by_id["A0"],
            "WAL mode allows one writer with no concurrent readers.",
            True,
            "negation",
        ),
        EditCase(
            "E2-actor",
            "A2",
            by_id["A2"],
            "Chen (query team) reports checkpoint latency p99 of 40ms on NVMe.",
            True,
            "actor-reversal",
        ),
        EditCase(
            "E3-number",
            "A2",
            by_id["A2"],
            "Chen (storage team) reports checkpoint latency p99 of 400ms on NVMe.",
            True,
            "number-unit",
        ),
        EditCase(
            "E4-qualifier-drop",
            "A1",
            by_id["A1"],
            "Readers block during a write.",
            True,
            "qualifier-drop",
        ),
        EditCase(
            "E5-paraphrase",
            "A4",
            by_id["A4"],
            "Under low contention, batching raises throughput by about thirty percent.",
            False,
            "paraphrase",
        ),
        EditCase(
            "E6-typo",
            "A4",
            by_id["A4"],
            "Batching improves throughput by 30% under low contention..",
            False,
            "typo",
        ),
        EditCase(
            "E7-rewrite",
            "A5",
            by_id["A5"],
            "Operators observe that once contention saturates the lock table, "
            "batching yields no throughput gain whatsoever and can even regress.",
            True,
            "paraphrase",
        ),
    ]


def deterministic_closure(
    assertions: list[Assertion], target_aid: str
) -> set[str]:
    """R06-style conservative invalidation: edited assertion + every assertion
    that transitively depends on it (dependency closure, never skipped)."""
    children: dict[str, list[str]] = {}
    for a in assertions:
        for parent in a.depends_on:
            children.setdefault(parent, []).append(a.aid)
    invalidated = {target_aid}
    frontier = [target_aid]
    while frontier:
        current = frontier.pop()
        for child in children.get(current, []):
            if child not in invalidated:
                invalidated.add(child)
                frontier.append(child)
    return invalidated


def predictive_scope(
    assertions: list[Assertion], case: EditCase, *, theta: float
) -> set[str]:
    """Residual-threshold predictor: when the edit's embedding residual is at
    most ``theta``, the edit is judged meaning-preserving and no repair is
    scheduled (work avoided); otherwise the deterministic closure applies."""
    if _residual(case.old_text, case.new_text) <= theta:
        return set()
    return deterministic_closure(assertions, case.target_aid)


def expected_affected(assertions: list[Assertion], case: EditCase) -> set[str]:
    """Oracle: meaning-changing edits affect target + dependents; preserving
    edits affect nothing."""
    if not case.meaning_changed:
        return set()
    return deterministic_closure(assertions, case.target_aid)


def compare_all(assertions: list[Assertion]) -> list[RepairComparison]:
    """Run every edit case through both repair policies."""
    out: list[RepairComparison] = []
    for case in edit_cases():
        out.append(
            RepairComparison(
                edit_id=case.edit_id,
                kind=case.kind,
                meaning_changed=case.meaning_changed,
                residual=_residual(case.old_text, case.new_text),
                deterministic_invalidated=sorted(
                    deterministic_closure(assertions, case.target_aid)
                ),
                predictive_invalidated={
                    theta: sorted(
                        predictive_scope(assertions, case, theta=theta)
                    )
                    for theta in THETAS
                },
                expected_invalidated=sorted(
                    expected_affected(assertions, case)
                ),
            )
        )
    return out


@dataclass
class RepairSummary:
    theta: float
    missed_meaningful: int
    false_invalidations: int
    touched_edits: int
    total_edits: int

    @property
    def touched_fraction(self) -> float:
        return self.touched_edits / max(1, self.total_edits)


def summarize(
    comparisons: list[RepairComparison], assertions: list[Assertion]
) -> dict[str, RepairSummary]:
    """Per-theta tallies + the deterministic baseline under key ``det``."""
    summaries: dict[str, RepairSummary] = {}
    for theta in THETAS:
        missed = false = touched = 0
        for comp in comparisons:
            pred = set(comp.predictive_invalidated[theta])
            expected = set(comp.expected_invalidated)
            if expected - pred:
                missed += 1
            false += len(pred - expected)
            if pred:
                touched += 1
        summaries[f"pred@{theta}"] = RepairSummary(
            theta, missed, false, touched, len(comparisons)
        )
    missed = false = touched = 0
    for comp in comparisons:
        det = set(comp.deterministic_invalidated)
        expected = set(comp.expected_invalidated)
        if expected - det:
            missed += 1
        false += len(det - expected)
        if det:
            touched += 1
    summaries["det"] = RepairSummary(-1.0, missed, false, touched, len(comparisons))
    return summaries
