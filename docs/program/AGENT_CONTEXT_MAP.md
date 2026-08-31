# Trace-Lite Agent Context Map

> **Status:** Draft. The coordinator produces actual task packets from this
> map; workers do not assemble unrestricted context on their own.

## Shared input for every worker

- `../trace/docs/program/00_CHARTER.md`
- `../trace/docs/program/01_INVARIANTS.md`
- `../trace/docs/program/02_EXECUTION_DAG.md`
- the relevant accepted decisions and work-packet template

## Role-specific local input

| Role | Required local material | Expected local output |
| --- | --- | --- |
| Research analyst | Beta workstream, open decision/risk entries, selected source code/doc excerpts. | Memo or experiment proposal; no code. |
| Data steward | Benchmark registry, protocol outline, dataset requirements. | Dataset record, staging instructions, hashes/licenses. |
| Evaluation engineer | Existing benchmark harness, registry, protocol, shared evaluation rules. | Manifest/runner/test proposal and evidence report. |
| TL engineer | Beta workstream, relevant source/tests, fixed acceptance checks. | Isolated TL change plus handoff. |
| Trace engineer | Shared contracts, promotion dossier, Trace work packet. | Isolated Trace change plus handoff. |
| Verifier | Fixed evaluator, implementation handoff, evidence bundle/service contract. | Independent verification report or conformance fixture. |

## Context exclusions

Do not include unrelated implementation history, unreviewed speculative
research, raw credentials, private raw corpus contents, or broad write
authority in a packet unless the task explicitly requires it and the human
owner has authorized it.

## Packet refresh triggers

Regenerate a packet when its base commit, accepted decision, invariant,
evaluation manifest, allowed paths, or service contract changes.

## Context assembly algorithm

The coordinator builds a packet in layers:

1. **Always-on constraints:** charter, invariants, task template, and the
   relevant accepted decisions.
2. **Node context:** exact DAG node, dependencies, risks, expected state
   transition, and claim-level boundary.
3. **Repository context:** base commit, allowed/read-only paths, current code
   anchors, tests, dirty-state note, and platform constraints.
4. **Role context:** the specialist role contract and its workstream brief.
5. **Evidence context:** prior handoffs, manifests, dataset records, fixed
   evaluator, or research memo relevant to the task.
6. **Output context:** named artifact/handoff locations and independent review.

Do not replace this structured packet with a large unfiltered history dump.

## Recommended packet size and contents

| Role | Core context | Optional context | Exclude by default |
| --- | --- | --- | --- |
| Research | Open decision, code/doc anchors, invariants, promotion rubric. | Prior memos and selected datasets. | Broad implementation diffs, secrets, raw private data. |
| Data | Evaluation lane, registry/protocol, rights/storage rules. | Adapter code and prior source records. | Provider credentials, unrelated architecture text. |
| Evaluation | Current harness, data records, protocol, fixed claim boundary. | Candidate handoffs. | Ability to change algorithms or thresholds. |
| TL engineer | Beta workstream, current source/tests, evaluator acceptance. | Local UI/API contracts. | Trace implementation internals unless a contract issue requires read-only context. |
| Trace engineer | Shared contracts, Trace code/docs, promotion dossier, tests. | TL mechanism spec/metrics. | TL database implementation as a copy source. |
| Verifier | Fixed packet, handoff, evaluator, service contract. | Redacted run artifacts. | Authority to repair candidate code or rewrite tests. |

## Packet freshness checks

Before execution, a worker confirms:

- the worktree is at the stated base or has documented divergence;
- input document hashes match or a new packet was issued;
- the allowed path set remains correct;
- datasets/providers/services are available only if authorized;
- no unresolved risk requires a higher-authority decision.

If any check fails, return a blocked handoff before changing code.

## Prompt assembly

For a worker invocation, concatenate:

1. the canonical [global prompt pack](../../../trace/docs/program/prompts/GLOBAL_CONTEXT.md);
2. the role prompt matching the assigned role;
3. the relevant role/workstream description;
4. the complete task packet;
5. only the current source excerpts named by the packet.

The prompt pack defines behavior. The task packet defines authority. Current
source files define observed implementation behavior. None of these layers may
silently override the others.
