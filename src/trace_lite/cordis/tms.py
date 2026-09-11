"""Modular Contract-Boundary TMS: authority lattice + dependency DAG truth maintenance."""

from __future__ import annotations

import time

from .models import (
    AuthorityLevel,
    CommitmentRecord,
    CommitmentStatus,
    DependencyNode,
    EvaluationVerdict,
    InvariantEvaluationResult,
    InvalidationCascadeResult,
    InvariantVetoError,
    JustificationReceipt,
    NodeStatus,
    RevocationEvent,
    ScopeExemption,
)


def scope_covers(commitment_scope: str, target: str) -> bool:
    if commitment_scope == "global":
        return True
    if commitment_scope.endswith("*"):
        return target.startswith(commitment_scope[:-1])
    return target == commitment_scope or target.startswith(commitment_scope + "/")


def _eval_predicate(predicate: str, delta: dict, env: dict | None) -> bool:
    """Evaluate a formal predicate against the proposed delta in a builtins-free namespace."""
    namespace = {"delta": delta, "env": env or {}}
    return bool(eval(compile(predicate, "<predicate>", "eval"), {"__builtins__": {}}, namespace))


class ModularContractBoundaryTMS:
    """Authority-gated commitments + contract-boundary invalidation cascades."""

    def __init__(self) -> None:
        self.commitments: dict[str, CommitmentRecord] = {}
        self.nodes: dict[str, DependencyNode] = {}

    # -- ledger ------------------------------------------------------
    def register_commitment(self, record: CommitmentRecord) -> str:
        if record.id in self.commitments:
            raise KeyError(f"commitment {record.id} already registered")
        self.commitments[record.id] = record
        return record.id

    def get_active_commitments(
        self, target_scopes: list[str], current_step: int = 0
    ) -> list[CommitmentRecord]:
        active = []
        for record in self.commitments.values():
            if record.status != CommitmentStatus.ACTIVE:
                continue
            if record.superseded_at_step is not None and current_step >= record.superseded_at_step:
                continue
            if any(
                scope_covers(scope, target)
                for scope in record.target_scopes
                for target in target_scopes
            ):
                active.append(record)
        active.sort(key=lambda r: (-r.authority.rank, r.id))
        return active

    def evaluate_invariants(
        self,
        target_scopes: list[str],
        proposed_delta: dict,
        current_step: int = 0,
        env: dict | None = None,
    ) -> list[InvariantEvaluationResult]:
        results = []
        for record in self.get_active_commitments(target_scopes, current_step):
            for target in target_scopes:
                if not any(scope_covers(s, target) for s in record.target_scopes):
                    continue
                if self._is_exempt(record, target, current_step, env):
                    results.append(
                        InvariantEvaluationResult(
                            record_id=record.id, target_scope=target,
                            verdict=EvaluationVerdict.EXEMPT,
                        )
                    )
                    continue
                if not record.formal_predicate:
                    verdict, message = EvaluationVerdict.SAT, "no formal predicate; upheld by authority"
                else:
                    try:
                        holds = _eval_predicate(record.formal_predicate, proposed_delta, env)
                    except Exception as exc:
                        holds = False
                        message = f"predicate error: {exc}"
                    else:
                        message = "predicate holds" if holds else "predicate violated by delta"
                    verdict = EvaluationVerdict.SAT if holds else EvaluationVerdict.VIOLATED
                results.append(
                    InvariantEvaluationResult(
                        record_id=record.id, target_scope=target, verdict=verdict,
                        diagnostic_message=message,
                        repair_hint=None if verdict == EvaluationVerdict.SAT else record.statement,
                    )
                )
        return results

    @staticmethod
    def _is_exempt(
        record: CommitmentRecord, target: str, current_step: int, env: dict | None
    ) -> bool:
        for exemption in record.exemptions:
            sub = exemption.sub_scope
            applies = (
                sub == "global"
                or target == sub
                or target.startswith(sub.rstrip("/") + "/")
                or (sub.endswith("*") and target.startswith(sub[:-1]))
            )
            if not applies:
                continue
            if exemption.expiry_step is not None and current_step > exemption.expiry_step:
                continue
            try:
                if _eval_predicate(exemption.condition_predicate, {}, env):
                    return True
            except Exception:
                continue
        return False

    def supersede_commitment(
        self,
        target_id: str,
        new_record: CommitmentRecord,
        receipt: JustificationReceipt,
        at_step: int = 0,
    ) -> CommitmentRecord:
        """Authority-gated supersession. Lower authority over higher raises InvariantVetoError."""
        current = self.commitments.get(target_id)
        if current is None:
            raise KeyError(f"unknown commitment {target_id}")
        if not receipt.authority.dominates(current.authority):
            raise InvariantVetoError(
                f"{receipt.authority.value} (rank {receipt.authority.rank}) cannot supersede "
                f"{current.authority.value} (rank {current.authority.rank}) invariant {target_id}"
            )
        if receipt.commitment_id != target_id:
            raise ValueError("receipt commitment_id must match supersession target")
        self.commitments[target_id] = current.model_copy(
            update={
                "status": CommitmentStatus.SUPERSEDED,
                "superseded_at_step": at_step,
                "superseded_by": new_record.id,
                "version": current.version + 1,
            }
        )
        self.commitments[new_record.id] = new_record
        return new_record

    def register_exemption(self, commitment_id: str, exemption: ScopeExemption) -> bool:
        record = self.commitments.get(commitment_id)
        if record is None:
            raise KeyError(f"unknown commitment {commitment_id}")
        if not exemption.authorized_by.dominates(record.authority):
            raise InvariantVetoError(
                f"{exemption.authorized_by.value} cannot exempt {record.authority.value} invariant"
            )
        self.commitments[commitment_id] = record.model_copy(
            update={"exemptions": [*record.exemptions, exemption]}
        )
        return True

    # -- dependency graph ---------------------------------------------
    def register_node(self, node: DependencyNode) -> str:
        if node.node_id in self.nodes:
            raise KeyError(f"node {node.node_id} already registered")
        self.nodes[node.node_id] = node
        return node.node_id

    def add_dependency_edge(self, source_id: str, target_id: str) -> bool:
        """Assert source-depends-on-target; rejects edges that would close a cycle."""
        if source_id not in self.nodes or target_id not in self.nodes:
            raise KeyError("both edge endpoints must be registered")
        if source_id == target_id:
            raise ValueError("self-dependency forbidden")
        if self._reaches(target_id, source_id):
            raise ValueError(f"edge {source_id}->{target_id} would close a dependency cycle")
        source = self.nodes[source_id]
        if target_id not in source.outgoing_dependencies:
            self.nodes[source_id] = source.model_copy(
                update={"outgoing_dependencies": [*source.outgoing_dependencies, target_id]}
            )
        target = self.nodes[target_id]
        if source_id not in target.incoming_dependents:
            self.nodes[target_id] = target.model_copy(
                update={"incoming_dependents": [*target.incoming_dependents, source_id]}
            )
        return True

    def _reaches(self, start: str, goal: str) -> bool:
        seen, stack = set(), [start]
        while stack:
            node_id = stack.pop()
            if node_id == goal:
                return True
            if node_id in seen:
                continue
            seen.add(node_id)
            node = self.nodes.get(node_id)
            if node:
                stack.extend(node.outgoing_dependencies)
        return False

    def detect_cycles(self) -> list[list[str]]:
        """Tarjan SCC; singletons with self-loops also reported (none possible by construction)."""
        index_of: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        cycles: list[list[str]] = []
        counter = [0]

        def strongconnect(node_id: str) -> None:
            index_of[node_id] = low[node_id] = counter[0]
            counter[0] += 1
            stack.append(node_id)
            on_stack.add(node_id)
            for dep in self.nodes[node_id].outgoing_dependencies:
                if dep not in index_of:
                    strongconnect(dep)
                    low[node_id] = min(low[node_id], low[dep])
                elif dep in on_stack:
                    low[node_id] = min(low[node_id], index_of[dep])
            if low[node_id] == index_of[node_id]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node_id:
                        break
                if len(component) > 1:
                    cycles.append(sorted(component))

        for node_id in self.nodes:
            if node_id not in index_of:
                strongconnect(node_id)
        return cycles

    def prune_contract_delta(
        self, node_id: str, old_contract: dict, new_contract: dict
    ) -> bool:
        """True when the public contract membrane mutated (Delta C != 0)."""
        if node_id not in self.nodes:
            raise KeyError(f"unknown node {node_id}")
        return old_contract != new_contract

    def propagate_revocation(self, event: RevocationEvent) -> InvalidationCascadeResult:
        """BFS over dependents; halts a branch when the membrane delta is null."""
        start = time.perf_counter()
        if event.target_node_id not in self.nodes:
            raise KeyError(f"unknown node {event.target_node_id}")
        tainted = [event.target_node_id]
        invalidated: list[str] = []
        pruned: list[str] = []
        queue: list[tuple[str, int]] = [(event.target_node_id, 0)]
        visited = {event.target_node_id}
        max_depth = 0
        while queue:
            node_id, depth = queue.pop(0)
            max_depth = max(max_depth, depth)
            node = self.nodes[node_id]
            # A node with an empty membrane delta quarantines the cascade here.
            if node.contract_membrane == {}:
                pruned.append(node_id)
                continue
            if node.status != NodeStatus.INVALID:
                self.nodes[node_id] = node.model_copy(update={"status": NodeStatus.INVALID})
            invalidated.append(node_id)
            for dependent in self.nodes[node_id].incoming_dependents:
                if dependent not in visited:
                    visited.add(dependent)
                    tainted.append(dependent)
                    queue.append((dependent, depth + 1))
        return InvalidationCascadeResult(
            initiating_event_id=event.revocation_id,
            tainted_nodes=tainted,
            invalidated_nodes=invalidated,
            pruned_nodes=pruned,
            propagation_depth_reached=max_depth,
            execution_time_ms=(time.perf_counter() - start) * 1000.0,
        )
