# Beta Workstream

> **Status:** Draft broad-stroke plan. It identifies work to verify and
> implement; it does not assert the current checkout already satisfies it.

## 1. Objective

Make Beta a trustworthy local, evidence-preserving reference implementation.
Beta is the active Trace-Lite runtime. Alpha is retained only as archived
ablation evidence, not as a parallel product path.

## 2. Beta architectural boundary

The source Spine is canonical. Trees, summaries, vectors, graph edges,
retrieval routing, and scoring are rebuildable Cortex state. A retrieval
improvement must not weaken exact evidence provenance or the fail-closed
activation contract.

## 3. First hardening wave

### 3.1 Candidate and active projection lifecycle

Verify and, if necessary, repair run-scoped candidate state for hierarchy,
vectors, summaries, and graph edges. Normal queries must resolve through a
verified active build only. Failed work must preserve the prior active state.

### 3.2 Bounded graph construction

Define and enforce deterministic policies for high-frequency co-occurrence,
edge degree, graph-storage growth, and PPR traversal work. Emit diagnostics
that explain applied limits and discarded candidate edges.

### 3.3 Source durability and organization proof

Cover crash/replay, idempotent ingestion, offset/hash reconstruction, source
coverage, topology validity, summary-source coverage, and
incremental-versus-full rebuild parity.

### 3.4 Local service boundary

Prepare a loopback, versioned evidence-bundle service contract with explicit
pending, unavailable, untrusted, and no-evidence outcomes. Downstream
consumers do not obtain direct database or provider-credential access.

## 4. Evaluation connection

The internal 320-case fixture is regression evidence. It is not sufficient for
external superiority or scale claims. Release-quality proof requires the
real-data manifests and fail-closed protocol described in the shared program
pack and local benchmark directories.

## 5. Exit sequence

```text
lifecycle isolation
  -> graph bounds
  -> durability and organization validation
  -> fail-closed real-data evaluation
  -> 100K real-corpus proof
  -> certified local reference release
  -> shadow consumer eligibility
```

## 6. Required handoff evidence

Every Beta task should report:

- base commit and allowed files,
- candidate/active lifecycle implications,
- exact validation/test results,
- source/provenance impact,
- performance or storage implications,
- remaining risks and gate dependencies.

## 7. Current-state audit method

Every Beta implementation packet begins with a narrow audit. It records:

1. the source, pending-work, candidate-build, active-build, vector, graph, and
   query paths actually observed in the current checkout;
2. build/revision identifiers and their persistence locations;
3. which operations run inside a transaction or equivalent handover boundary;
4. which reads can resolve a candidate, prior active, legacy, or hot-inbox
   result;
5. cleanup/retention behavior after success, failure, and interruption;
6. user-visible CLI/web/API status and diagnostics.

The output is an evidence-backed lifecycle map, not a copy of an earlier
architecture summary.

## 8. Detailed lifecycle target

The intended local lifecycle is:

~~~text
source capture
  -> durable pending source record
  -> provider/preflight admission for derived work
  -> named candidate build
  -> candidate trees/summaries/vectors/graph/routing
  -> structural + provenance + quality + vector + graph validation
  -> atomic active-build handover
  -> pending-work consumption for only included source atoms
  -> retained prior build until rollback/retention policy permits cleanup
~~~

### Required properties

- A new capture arriving during a build remains durable and pending; it is not
  accidentally marked organized by the older candidate.
- A failed candidate does not modify the normal query result set or consume
  pending work.
- Graph edges belong to the same lifecycle as trees/vectors/routing. A new tree
  activation must not leave obsolete graph state query-visible.
- Validation observes the exact candidate that will be activated, not a
  separately reconstructed approximation.
- Garbage collection is explicit, retention-aware, and cannot remove the last
  verified active build.

## 9. Graph policy specification

### Relation families

The local graph may contain deterministic structural relations such as sequence
and hierarchy plus soft co-occurrence/affinity. The build/evidence contract
must record relation type, source/target atom IDs, build identity, weight,
provenance, and policy/configuration identity.

### Bounding dimensions

The later detailed policy must set and measure:

| Dimension | Why it exists |
| --- | --- |
| Maximum eligible term/document frequency | Prevent broad common tokens from creating dense all-pair graphs. |
| Maximum partners per term | Bound per-term construction cost. |
| Maximum outgoing/incoming degree | Bound storage and activation fan-out. |
| Deterministic selection rule | Make rebuilds reproducible and explainable. |
| Edge weight/aggregation rule | Avoid duplicate/order-dependent scoring. |
| Candidate/build retention | Prevent stale or cross-build edges. |
| PPR seeds/iterations/frontier | Bound query-time activation work. |
| Query-class activation policy | Avoid graph work for direct/abstention cases. |

### Required diagnostics

For a build and a query, report counts sufficient to explain:

- generated, accepted, capped, and discarded edges by relation type;
- high-frequency terms/atoms affected by caps;
- active build/revision and graph policy hash;
- seed count, nodes/edges examined, iterations, time, and early-stop reason;
- whether graph activation was skipped, and why.

## 10. Beta test and evidence matrix

| Capability | Positive proof | Negative/failure proof |
| --- | --- | --- |
| Source fidelity | Reconstruct atom content from artifact offsets/hashes. | Corrupt/missing mapping is detected and blocks health. |
| Candidate isolation | Candidate validates then activates. | Failed candidate leaves prior active result/evidence intact. |
| Pending semantics | Included atoms are consumed after success. | Mid-build/new atoms remain pending and non-silently indexed. |
| Graph lifecycle | Active build uses matching graph identity. | Stale/candidate edges cannot influence normal query. |
| Organization | Coverage/topology/summary quality/parity checks pass. | Orphans, invalid summaries, or bad topology fail health. |
| Query gating | Trusted active index returns evidence with status. | Pending/untrusted/insufficient state returns explicit safe status. |
| Service boundary | Versioned local response meets conformance fixture. | Provider/validation/policy error stays redacted and actionable. |

## 11. Beta implementation packets

Recommended packet order:

1. **TL-AUDIT-LIFECYCLE:** audit only; no code changes until candidate/active
   path is mapped.
2. **TL-DESIGN-GRAPH-RELEASE:** write a reviewed local design/migration note.
3. **TL-IMPLEMENT-LIFECYCLE:** narrow schema/build/read-path changes plus
   lifecycle tests.
4. **TL-IMPLEMENT-GRAPH-BOUNDS:** edge policy and adversarial tests.
5. **TL-VERIFY-DURABILITY:** recovery/fidelity/parity verification.
6. **TL-EVAL-ADMISSION:** connect only to the fail-closed evaluator manifest.
7. **TL-100K-RUN:** execute the reviewed real-corpus gate.
8. **TL-LOCAL-REFERENCE-REVIEW:** create the evidence package for shadow
   eligibility; no automatic consumer integration.

## 12. Claim boundaries

Internal frozen-fixture improvements can support a C2 regression statement.
They do not establish public superiority, a 100K operational claim, or safe
downstream adoption. The 100K gate and independent verification are required
before describing Beta as a trusted local reference for shadow consumers.
