# Benchmark and Scale Protocols

> **Status:** Planning outline. This does not alter the existing benchmark
> runner or certify any prior or future result.

## Purpose

This directory will hold executable and human-readable protocols that make
benchmark behavior reproducible, fail closed, and suitable for review.

## Intended protocol families

### 1. Release evaluation protocol

Defines required datasets, models, retrieval stages, baselines, allowed
configuration, fallback assertions, output artifacts, and failure behavior.

### 2. Query-class protocol

Defines how direct lookup, broad thematic, multi-hop, temporal/revision,
contradiction, abstention, and downstream task classes are labeled and reported.

### 3. Recovery and lifecycle protocol

Defines candidate-build failure, crash/restart, replay, active-revision,
rollback, stale-edge, and provenance checks.

### 4. 100K real-corpus protocol

Defines corpus admission, scale measurement, cold/warm latency, PPR work,
storage, memory, build/rebuild time, recovery, cost, and quality gates.

### 5. 1M pilot protocol

Created only after reviewed 100K evidence. It will cover partitioning, bounded
deltas, index/rebuild strategy, recovery under load, and economics.

## Non-negotiable behavior

A certification run must fail rather than silently substitute mock providers,
synthetic data, tiny samples, flat retrieval, missing judgments, or disabled
required retrieval stages.

## Ownership

The evaluation engineer authors protocol drafts. The verifier checks that
implemented runners obey them. The human owner accepts thresholds and release
claims.

## Protocol hierarchy

| Protocol | Consumer | Purpose |
| --- | --- | --- |
| Developer smoke | Implementer | Fast local diagnosis; never certification. |
| Regression | TL/Trace maintainers | Detect known behavior change on frozen fixtures. |
| Exploratory research | Research/evaluation | Compare hypotheses before a fixed release boundary. |
| Certification candidate | Evaluator/verifier | Real-data, manifest-pinned evidence for a defined claim. |
| Fault/recovery | Verifier | Prove retained source/active-state behavior under failure. |
| 100K scale | Program gate review | Real-corpus correctness, resource, recovery, and cost proof. |
| 1M pilot | Program gate review | Next-scale partitioning/delta/recovery/economics evidence. |

## Common execution contract

Every protocol run declares:

- protocol version and purpose;
- data and transform record IDs/hashes;
- code revision and dirty-worktree status;
- system/baseline configurations and model/provider fingerprints;
- retrieval profiles and required/prohibited mechanisms;
- environment, warmup, concurrency, and resource capture settings;
- output paths, correlation/run ID, and claim level;
- required exit status and failure taxonomy.

## Release-mode assertions

A certification-candidate protocol must assert before execution:

1. each dataset record is evaluator-admitted;
2. local staged data and checksums are present;
3. the selected model/provider mode is explicit and permitted;
4. mock/synthetic/sample/forced-flat/fallback behavior is disabled;
5. required judgments and baseline settings are available;
6. result artifact locations are writable and not accidental Git inputs;
7. environment capture is available;
8. any failed assertion exits nonzero and marks the run non-certifying.

## Query execution policy

For every query, the run should retain:

- stable query ID and class;
- source/judgment IDs or no-evidence expectation;
- requested retrieval profile and actual controller actions;
- active build/release identity;
- result status/sufficiency state;
- returned evidence IDs/anchors and score/work diagnostics;
- latency and error/failure information.

Aggregates must be derived from these query-level records rather than replacing
them.

## Fault/recovery protocol

The future detailed protocol must choose reproducible interruption points:

- before source commit;
- after source commit but before derived candidate creation;
- after partial candidate objects;
- during validation;
- during activation/handover;
- after activation before cleanup;
- after policy/version/revocation changes.

For each point, compare source coverage, active release identity, query-visible
evidence, pending work, and recovery/replay outcome.

## 100K protocol phases

1. Admit and verify real corpus/transform.
2. Capture clean baseline environment and prior active state.
3. Ingest and measure source fidelity/idempotency.
4. Build candidate derived structures with graph/work telemetry.
5. Validate and activate or demonstrate safe failure retention.
6. Run cold/warm query-class workload with resource capture.
7. Run recovery/rebuild/rollback drills.
8. Produce evaluator and independent verifier reports.

## 1M admission rule

The 1M protocol is not populated or executed until a human-reviewed 100K report
identifies acceptable quality, recovery, latency, storage, and cost behavior.
The 1M packet must state what new partitioning/delta/operational assumption it
tests beyond 100K rather than repeating the same run at a larger number.
