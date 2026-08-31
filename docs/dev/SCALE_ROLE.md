# Trace-Lite Scale Role

Status: broad-strokes component plan under the canonical Trace scale program.

Workspace-relative canonical entry point:
[`trace/docs/SCALE_PROGRAM.md`](../../../trace/docs/SCALE_PROGRAM.md).

Trace-Lite is not the billion- or trillion-token physical data plane. Its purpose is to remain a small, inspectable, local implementation that makes the behavioral contracts easy to exercise and the failure modes easy to reproduce.

## Role in the Trace program

Trace-Lite has three durable roles:

1. **Embedded/local product.** Provide a useful single-user, local memory product with source-first capture, explicit organization, bounded retrieval, and fail-closed behavior.
2. **Correctness and conformance oracle.** Serve as a readable reference for Spine/Cortex separation, evidence-bearing results, candidate validation, activation, exclusion, and recovery semantics. Trace V2 should export and test contracts against this adapter where practical; it should not copy Trace-Lite's physical storage assumptions.
3. **Mechanism-ablation lab.** Provide controlled experiments for hierarchy, graph activation, summaries, routing, fusion, energy/access effects, and incremental projection strategies. Every mechanism must be compared with a simpler control and must not be promoted merely because it looks coherent on a small corpus.

The canonical direction is: preserve behavior and evidence contracts, replace scale-bound implementations in Trace, and keep Trace-Lite useful as a bounded local/reference system.

## Current scale blockers

The following implementation details prevent Trace-Lite from serving as a large physical data plane. Evidence is from the current source and should be kept attached to future repair or migration work.

- **Corpus-wide query validation:** `src/trace_lite/db.py:1601` invokes `validate_index()` during queries; `src/trace_lite/db.py:1438` and `src/trace_lite/cortex/vector_store.py:235` materialize corpus-wide atoms, nodes, and vectors.
- **Corpus-wide ingestion/status work:** `src/trace_lite/db.py:442` calls status after capture; `src/trace_lite/db.py:1671` and `src/trace_lite/db.py:1690` repeatedly enumerate and validate the full corpus.
- **Full in-memory rebuilds:** `src/trace_lite/db.py:677`, `src/trace_lite/db.py:755`, and `src/trace_lite/db.py:1122` retain complete atom, tree, node, and vector structures while rebuilding.
- **Quadratic co-occurrence graph growth:** `src/trace_lite/engines/graph.py:191` and `src/trace_lite/engines/graph.py:197` emit atom pairs for shared terms, with worst-case cost proportional to the sum of squared term document frequencies.
- **Duplicated hierarchy membership:** `src/trace_lite/engines/raptor.py:349` and `src/trace_lite/engines/raptor.py:425` materialize descendant atom IDs into parent rows and vector metadata; `src/trace_lite/engines/graph.py:154` creates direct summary-to-descendant links.
- **Full-tree incremental rebuilds:** `src/trace_lite/db.py:590` and `src/trace_lite/db.py:605` reload and reprocess an entire tree when adding atoms; default bounds still permit up to `8^5` leaves under one root (`src/trace_lite/config.py:56`, `src/trace_lite/engines/raptor.py:316`).
- **Unbounded tree-directory routing:** `src/trace_lite/engines/router.py:147` and `src/trace_lite/engines/lattice.py:303`/`:322` enumerate trees and re-embed roots rather than using a bounded, indexed directory.
- **Non-indexed leaf membership:** `src/trace_lite/cortex/forest.py:379` scans and decodes leaf rows to find an atom's leaf; `src/trace_lite/engines/lattice.py:245` can repeat this during result hydration.
- **Single local physical layout:** `src/trace_lite/db.py:199` opens one SQLite Spine, one SQLite Cortex, and one local LanceDB directory. This is appropriate for embedded use, not distributed archival storage.
- **Insufficient raw-source preservation:** `src/trace_lite/spine/models.py:53` and `src/trace_lite/spine/store.py:61` retain hashes/metadata but not an independently reconstructible original byte payload; `src/trace_lite/db.py:452` decodes with errors ignored and `src/trace_lite/spine/atomizer.py:34` removes span boundaries before hashing.
- **Minimal replay ledger:** `src/trace_lite/db.py:429` records limited ingestion metadata and `src/trace_lite/spine/store.py:521` loads the complete event table for replay. This is audit support, not a shard replay protocol.

There are also revision-safety gaps that matter even in a reference lane: activation can remove old topology before candidate publication (`src/trace_lite/cortex/forest.py:764`/`:776`); graph rows lack build identity (`src/trace_lite/cortex/forest.py:171`); graph construction follows Cortex activation (`src/trace_lite/db.py:883`); and incremental organization publishes per-tree state rather than one workspace-wide candidate (`src/trace_lite/db.py:590`). These are conformance risks, not reasons to make Trace-Lite distributed.

## Contracts to preserve and export to Trace

Trace-Lite should preserve these externally visible contracts and express them through a narrow shared adapter/evidence-bundle interface:

- Capture source artifacts and atoms before projection work; ingestion must not silently require an LLM (`src/trace_lite/db.py:380`, `:408`, `:437`).
- Keep immutable/source truth separate from rebuildable forest, summaries, vectors, and graph projections; `reset_derived()` must preserve the Spine (`src/trace_lite/db.py:1802`).
- Treat derived builds as candidates: record build identity and state, validate coverage/topology/vector parity, then activate atomically; retain the last verified state and fail closed when trust is absent (`src/trace_lite/cortex/manifest.py:13`, `src/trace_lite/db.py:671`, `src/trace_lite/db.py:789`, `src/trace_lite/db.py:1592`).
- Return evidence, not unsupported semantic claims: source IDs, locations/spans, hashes, traversal paths, per-channel contributions, warnings, sufficiency, and abstention (`src/trace_lite/engines/lattice.py:15`, `src/trace_lite/engines/lattice.py:250`).
- Maintain one canonical summary text per summary projection. Summaries remain optional, derived, and non-authoritative; validated child summary text is the only input to parent summaries (`src/trace_lite/engines/raptor.py:513`, `src/trace_lite/engines/summary.py:180`, `:229`).
- Use typed retrieval budgets: candidate count, seeds, edge-family quotas, hops, iterations, shard fan-out, latency, and token budget. Query-time activation may rank evidence but never establishes factual truth.
- Make exclusions explicit whenever the active verified projection cannot cover the request; never fabricate a fallback index or silently expose an unvalidated candidate.

Trace should implement these contracts with content-addressed source envelopes, revision-scoped projections, durable activation journals and crash reconciliation, indexed provenance, bounded neighbor selection, partition-local retrieval, and federated routing. Trace-Lite is a behavioral reference for those properties, not a storage template.

## 100K reference lane versus ablations

The 100K lane is a correctness/conformance lane for a bounded reference implementation. Its required fixes are narrowly scoped:

- remove corpus-wide validation from the normal query hot path while retaining explicit offline validation;
- replace all-pairs graph construction with deterministic, rarity-aware, top-k neighbors and per-node/per-relation caps;
- attach build/revision identity to graph, forest, membership, and vector rows;
- make candidate activation and rollback genuinely atomic, including crash reconciliation across stores;
- make incremental organization delta-bounded or explicitly label it as a full rebuild;
- index leaf membership and source spans; preserve exact source bytes or an authoritative source reference;
- add bounded replay and evidence-export checks suitable for comparison with Trace V2.

These fixes establish a trustworthy reference lane. They do not require Trace-Lite to acquire distributed sharding, object-storage tiers, cross-shard routing, or trillion-token capacity.

The following remain ablations until evidence promotes them:

- RAPTOR hierarchy versus flat dense/lexical controls;
- PPR and each graph edge family versus no-graph and sparse-graph controls;
- direct summary-to-descendant edges versus parent-child-only edges;
- fixed fusion and three-tier gating versus calibrated, leave-one-channel-out controls;
- energy decay/access-frequency effects, especially long-tail recall;
- semantic multi-tree routing versus source/time or single-tree baselines;
- generated canonical summaries versus no-summary and controlled extractive baselines;
- incremental projection building versus full-rebuild equivalence;
- vector quantization, multi-stage retrieval, and alternative index topologies.

Promotion requires real, pinned, held-out data; reproducible manifests; provenance/coverage evidence; failure and recovery evidence; and measured latency, memory, storage, throughput, and cost. A mechanism is not a scale prerequisite merely because it improves a local synthetic or visual result.

## Expansion backlog for lower-cost agents

Lower-cost agents may expand this file or linked implementation packets in small, reviewable increments. They should preserve the headings and keep every claim tied to source evidence or a named experiment.

1. Define the shared Trace-Lite/Trace ingest, query, evidence-bundle, reset, and export contract.
2. Draft a 100K conformance matrix covering source integrity, coverage, revision activation, rollback, replay, abstention, and evidence fields.
3. Write focused repair proposals for revision-scoped graph rows, atomic activation, crash recovery, and indexed membership.
4. Specify bounded sparse-graph policies and ablation controls with complexity budgets.
5. Add a source-preservation proposal covering exact bytes, encoding, offsets, hashes, and licensing metadata.
6. Map each mechanism ablation to a fixed control, metric family, dataset identity, and promotion rule.
7. Define the adapter boundary so Trace-Lite can act as a local conformance oracle without coupling Trace V2 to SQLite/LanceDB.
8. Keep billion/trillion physical-plane design in Trace's scale program: sharded immutable Spine, tiered archive, partition-local projections, bounded router fan-out, compaction, revocation/deletion, replay watermarks, and operational economics.

No agent should turn this backlog into silent product-wide implementation. Each item should produce a narrow plan, evidence requirement, or isolated change packet for explicit review.
