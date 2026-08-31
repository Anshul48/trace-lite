# Query-Class Protocol: <protocol-id>

## Scope

| Field | Value |
| --- | --- |
| Purpose/evidence lane | |
| Dataset/query pack | |
| Claim level target | |
| Applicable system/profile | |
| Excluded query types | |

## Query classes

| Class | Definition | Expected evidence behavior | Primary metrics | Required negative cases |
| --- | --- | --- | --- | --- |
| Direct / needle | | | | |
| Semantic lookup | | | | |
| Broad thematic | | | | |
| Multi-hop | | | | |
| Temporal/revision | | | | |
| Contradiction | | | | |
| Abstention | | | | |
| Consumer task | | | | |

## Per-query capture

Record stable query ID, expected evidence/no-evidence state, active release,
actual retrieval profile/controller actions, returned anchor IDs, query status,
scores/work diagnostics, latency, and any exclusion/error reason.

## Review rules

- Aggregates may not hide an unsupported class.
- A query excluded after the run begins remains visible with a reason code.
- Query labels and expected evidence IDs are versioned and hash-pinned.
- A new class requires a baseline and an explicit failure interpretation.
