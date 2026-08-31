# Program Reference and Synchronization Rule

> **Status:** Draft operational rule.

## Canonical source

The sibling Trace repository owns the canonical cross-project control plane:

```text
../trace/docs/program/
```

It contains the charter, invariants, execution DAG, shared contracts,
evaluation protocol, promotion rubric, risks, agent roles, and templates.

## Trace-Lite ownership

Trace-Lite owns only its local implementation and benchmark workstream
documents, including:

- Beta candidate/active projection lifecycle work,
- bounded graph construction and retrieval-work budgets,
- local durability/provenance and organization evidence,
- local service/API preparation,
- benchmark registry and protocol implementation.

## Synchronization rule

1. A TL worker reads the canonical program pack and this local reference.
2. The coordinator builds a scoped packet with exact document references and
   hashes.
3. TL-local findings are returned through the task handoff.
4. Only the coordinator promotes cross-project findings into canonical
   documents or a decision record.

No worker should maintain a competing copy of global invariants, gate
thresholds, or promotion criteria.

## Reference refresh

When the program pack changes materially, update this file with:

- the relevant decision identifiers,
- a source revision or document hash,
- local workstream consequences,
- any packet that must be regenerated.

## Synchronization procedure

### Before packet creation

1. Read the canonical charter, invariants, DAG, shared contracts, evaluation
   protocol, promotion rubric, decision log, and risk register relevant to the
   task.
2. Capture the canonical document revision or content hashes in the packet.
3. Read the current local Beta workstream and benchmark/protocol documents.
4. Inspect current code before relying on any planning statement.

### During work

- Treat a newly discovered conflict as a packet blocker.
- Do not edit canonical policy from a TL worktree.
- Record local observations in the task handoff, not by silently changing this
  reference.
- Regenerate context if an accepted decision, evaluator manifest, or code base
  changes materially.

### After verification

The coordinator decides whether the result updates:

- a local Beta workstream detail;
- the benchmark registry/protocol;
- a canonical risk/decision/DAG node;
- a future promotion dossier;
- no shared document because the result is negative or task-local.

## Cross-repository terminology map

| Shared term | Trace-Lite local meaning | Trace program meaning |
| --- | --- | --- |
| Spine | Durable source artifacts, atoms, offsets, hashes, and events. | Canonical event/version/evidence/policy truth. |
| Cortex | Trees, summaries, vectors, graph/routing state. | Revisioned derived projections and activation/controller state. |
| Candidate | A staged local build not yet active. | A projection release/run not yet active. |
| Active | Last validated local derived build. | Effective validated workspace projection release. |
| Evidence bundle | Local query/API output with exact source support. | Versioned policy-aware consumer contract. |
| Promotion | Evidence that a local mechanism merits review. | Human-approved adaptation into governed infrastructure. |

## Conflict resolution

If the local code or document appears to conflict with a shared invariant:

1. preserve the safer current behavior;
2. document the observation with file/symbol/command evidence;
3. mark the task blocked or reduced in scope;
4. request a decision or contract amendment;
5. do not resolve the conflict by changing a global document from the local
   worktree.
