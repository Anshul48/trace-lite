# Trace-Lite query-native memory program

Contract revision: TL-QN-2026-09-12.1
Status: planning complete; implementation and qualification not performed by this revision.
Scope: TL only. TRACE is a read-only architectural reference.

## Outcome

Ingest a source once, preserve its evidence, extract a small relational graph, reconcile it with a sparse global graph, maintain overlapping subgraph holons, and answer queries with traceable evidence dossiers. Source documents remain provenance envelopes; query dossiers become the access model.

Read [RESEARCH.md](RESEARCH.md), [ARCHITECTURE.md](ARCHITECTURE.md), [EXECUTION.md](EXECUTION.md), [EVALUATION.md](EVALUATION.md), then [STATE.md](STATE.md).

Review limits and documentation checks are recorded in [DESIGN_REVIEW.md](DESIGN_REVIEW.md).

## Authority and supersession

The 2026-09-12 request authorizes research and updates to TL plans. It prioritizes source-local relations, cross-source composition, adaptive atomization, and algorithmic scalability over dogmatic latency/dependency targets. It removes Obsidian as an architectural priority and confines this work to TL.

This contract supersedes the TL design and future execution instructions in `../filing-cabinet/` and the old cross-project launch prompt. Historical P01–P07 delivery/review records remain historical evidence. Their VERIFIED labels do not qualify this design.

The quoted overnight reset and cross-project instructions are historical context. This revision does not initiate a reset, implementation, model run, benchmark ladder, or changes to TRACE. Future implementation starts from the existing TL foundation using the new packets. Preserve the companion plugin and unrelated work; no plugin development is required.

## Requirements and design decisions

| ID | Requirement or decision | Basis | Acceptance |
|---|---|---|---|
| QN-01 | Query-native evidence access with retained source identity | Current requested direction | Dossier references resolve to exact evidence versions |
| QN-02 | Local relational graph before selective global integration | Current requested direction | Conditions/roles survive extraction and cross-source grouping |
| QN-03 | Native overlapping cross-source holons | Current requested direction | Related/disagreeing sources coexist without text fusion |
| QN-04 | Adaptive segmentation and predictive repair evaluated explicitly | Current requested direction; mechanism remains experimental | R08 ablations; no guarantee from a score alone |
| QN-05 | Atomic evidence updates and observable projection freshness | Engineering requirement supporting QN-01 | Crash, deletion, stale-job and restart gates |
| QN-06 | Independent global lexical/vector retrieval | Design recommendation | Misfiled/unfiled evidence remains eligible |
| QN-07 | Bounded incremental local work and parallel workers | Design recommendation | Update cost and queue-age measurements |
| QN-08 | Single-node first, replaceable ANN and model adapters | Design recommendation | Matched quality/performance evaluation |
| QN-09 | No unmarked generated facts or automatic authority promotion | Evidence contract | Derived claims retain lineage/status |
| QN-10 | Same candidate/configuration for quality and performance | Qualification contract | Complete run manifest and paired reports |

Sub-50 ms, 500 MB RSS, and 1,200 documents/s are historical targets, not mandatory gates for the new semantic workload. Retain them as comparison points. R00 proposes workload-specific envelopes after measuring the host and corpus; freeze envelopes before final qualification.

## Product boundary

TL organizes, retrieves, and compiles evidence. Optional models extract relations, propose groupings, and rank evidence. TL does not autonomously execute actions or declare factual truth because an LLM approved its own output. Cordis consumes dossiers through an adapter; its real runtime integration needs its own live check.

## First useful delivery

Two sources describe a mechanism with conflicting conditions. Ingestion produces source-linked local assertions, cross-source grouping, and a dossier retaining the conflict. Editing a condition makes affected interpretations stale, updates only the relevant neighborhood, and changes the next current-state dossier. A restart preserves the result.
