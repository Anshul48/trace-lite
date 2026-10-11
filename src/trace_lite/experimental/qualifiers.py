"""Qualifier-severance probe fixtures (R08, C8-2).

Each fixture is a single structural paragraph in which an antecedent
condition (negation, unless/only-if, high-contention qualifier, actor or
number qualifier) lives in one sentence and its claim in another. A
predictive cut anywhere *inside* the paragraph separates co-qualifying
sentences into different spans — that is a severed condition. The
structural baseline keeps every fixture in one span (asserted by tests),
so any severance is attributable to the predictive signal alone.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualifierFixture:
    fid: str
    kind: str  # negation | unless | only | contention | actor | number
    text: str


def fixtures() -> list[QualifierFixture]:
    return [
        QualifierFixture(
            "Q01-unless",
            "unless",
            "Unless the database uses WAL mode, readers block during a write. "
            "The checkpoint path assumes this ordering for recovery. "
            "Operators who ignore the precondition see spurious read errors.",
        ),
        QualifierFixture(
            "Q02-only-if",
            "only",
            "Only if batching is enabled under low contention does throughput rise. "
            "Operators must verify the contention regime before enabling it. "
            "Blindly enabling batching on a hot lock table regresses throughput.",
        ),
        QualifierFixture(
            "Q03-negation",
            "negation",
            "The legacy rollback journal does NOT allow concurrent readers. "
            "Any migration plan must schedule downtime accordingly. "
            "The outage window must cover the full checkpoint duration.",
        ),
        QualifierFixture(
            "Q04-contention",
            "contention",
            "Batching improves throughput by 30% under low contention. "
            "Under high contention the benefit disappears entirely. "
            "Benchmarks that omit the contention regime are misleading.",
        ),
        QualifierFixture(
            "Q05-actor",
            "actor",
            "Chen of the storage team reports checkpoint latency p99 of 40ms. "
            "Chen of the query team disputes this and blames parse time instead. "
            "The two reports must never be merged into a single attribution.",
        ),
        QualifierFixture(
            "Q06-number",
            "number",
            "Checkpoint latency p99 is 40ms on NVMe drives in our test rack. "
            "That bound holds only for sequential write workloads. "
            "Random writes exceed it by an order of magnitude.",
        ),
        QualifierFixture(
            "Q07-if-when",
            "unless",
            "If the lock table saturates, batching yields no gain whatsoever. "
            "Capacity plans must provision headroom for that regime. "
            "Alert thresholds should fire well before saturation is reached.",
        ),
        QualifierFixture(
            "Q08-except",
            "only",
            "All readers see a consistent snapshot except when a checkpoint "
            "is in progress. Clients must retry reads that span a checkpoint. "
            "Retry budgets must account for back-to-back checkpoints.",
        ),
        QualifierFixture(
            "Q09-negation-claim",
            "negation",
            "WAL mode allows one writer with concurrent readers. "
            "It does not permit two concurrent writers under any setting. "
            "A second writer receives a busy error immediately.",
        ),
        QualifierFixture(
            "Q10-provided",
            "unless",
            "Provided that fsync succeeds, the commit is durable. "
            "Without that guarantee the write may be lost on power failure. "
            "Durability tests must cut power, not just kill the process.",
        ),
        QualifierFixture(
            "Q11-contention-pair",
            "contention",
            "Under low contention fewer syscalls per item raise throughput. "
            "Under high contention lock waits dominate and batching stalls. "
            "The crossover point sits near seventy percent lock utilization.",
        ),
        QualifierFixture(
            "Q12-only-when",
            "only",
            "Checkpoints are cheap only when the WAL is small. "
            "A large WAL makes every checkpoint scan expensive. "
            "Autocheckpoint tuning keeps the WAL under that threshold.",
        ),
    ]


def count_severance(
    text: str, cut_offsets: list[int], *, par_start: int = 0
) -> int:
    """Number of cuts strictly inside the paragraph (any such cut severs)."""
    inner = [c for c in cut_offsets if par_start < c < par_start + len(text)]
    return len(inner)
