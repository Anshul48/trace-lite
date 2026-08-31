# Trace-Lite Program Workspace

> **Status:** Draft planning scaffold. This directory does not mark Beta
> hardening, benchmark claims, or integration gates as complete.

## Purpose

This is Trace-Lite's local workstream view of the shared Trace / Trace-Lite
program. It gives TL workers focused Beta contracts and points to the canonical
cross-repository control plane in the sibling Trace repository.

## Canonical program documents

The canonical program pack is the sibling
[Trace program control plane](../../../trace/docs/program/README.md).

Read [PROGRAM_REFERENCE.md](PROGRAM_REFERENCE.md) before creating a task
packet. Do not duplicate or independently edit global charter, invariant,
promotion, or evaluation rules here.

## Local documents

| File | Purpose |
| --- | --- |
| [BETA_WORKSTREAM.md](BETA_WORKSTREAM.md) | Broad TL hardening sequence and evidence expectations. |
| [AGENT_CONTEXT_MAP.md](AGENT_CONTEXT_MAP.md) | Which local and shared documents each role receives. |
| [PROGRAM_REFERENCE.md](PROGRAM_REFERENCE.md) | Canonical-source and synchronization rule. |

## Benchmark staging

Benchmark-specific planning belongs in:

- [benchmarks/registry/README.md](../../benchmarks/registry/README.md)
- [benchmarks/protocols/README.md](../../benchmarks/protocols/README.md)

Raw corpora, private data, caches, and results remain outside version control
under the existing ignore rules unless a later explicit decision says otherwise.

## Local ownership boundary

Trace-Lite local documents answer implementation and experiment questions that
belong to the embedded/local system:

- source ingestion, pending work, organization, graph, vector, and active-build
  lifecycle;
- Beta compatibility and Alpha archival evidence;
- local CLI/web/service behavior and diagnostics;
- benchmark adapter truth, local dataset staging metadata, and 100K readiness.

They do not redefine global policy, promotion thresholds, consumer authority,
or Trace V2 data contracts. Those remain in the canonical program pack.

## Current local anchors for packets

The coordinator should include only the anchors relevant to a task, normally:

| Concern | Useful current anchor |
| --- | --- |
| Source/derived lifecycle | src/trace_lite/db.py, especially organization, reindex, validation, query, and status paths. |
| Graph behavior | src/trace_lite/engines/graph.py and graph-related persistence/query code. |
| Source truth | src/trace_lite/spine/store.py and atom/artifact/event contracts. |
| Vectors and rebuilds | src/trace_lite/cortex/vector_store.py plus active-build handling. |
| Benchmark behavior | benchmarks/runner.py and src/trace_lite/engines/benchmark.py. |
| User surfaces | CLI/server/web files and the targeted UI/API tests named by a packet. |

These are starting points for inspection, not proof that a desired capability
already exists.

## Local task flow

~~~text
shared packet
  -> TL current-state audit
  -> isolated implementation or benchmark/data artifact
  -> fixed evaluator / independent verification
  -> handoff to canonical program inbox
  -> human-reviewed decision or next packet
~~~

## Trace-Lite document update rules

- Update Beta local documents after a verified TL handoff changes lifecycle,
  graph, API, benchmark, or gate assumptions.
- Link to the relevant canonical decision and task ID instead of copying it.
- Mark observations, plans, and verified behavior distinctly.
- Preserve user-facing capabilities until an explicit migration/replacement plan
  has been accepted and verified.
