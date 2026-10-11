# TRACE-LITE Architecture Review — Shared Global Context

Root: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite`
Ref A: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace` (read-only architectural reference)
Ref B: `/mnt/c/Users/anshu/OneDrive/Desktop/trace lite and trillion dreams` (planning/session-archive docs)

## 1. Repo maps

### trace-lite source tree (`src/trace_lite/`)

- `api/app.py` — FastAPI loopback daemon (:8420); write handlers (`ingest_note`, `remove_note`, `sync_notes`, `_ingest_replay`); lifespan starts `MaintenanceWorker` when `TRACE_LITE_SERVED_PERSISTENT=1`; served-snapshot attach (`_ensure_served_persistent_view`); R06 concurrency (one writer `write_lock`, per-thread reader conns, generation-swapped reads).
- `store/database.py` — SQLite engine: canonical atoms, event ledger, FTS5; `atom_delta` change-log DDL + triggers (G1 incremental warm); `sha256_bytes`.
- `store/schema.sql` — canonical DDL (atoms, FTS, revisions, spans, relations, holons, outbox, generations).
- `store/outbox.py` — durable projection-job queue (`enqueue`, leases, `retry_delay_s`).
- `store/generations.py` — projection-generation registry; `guarded_activate` (served-pointer moves only on real projections).
- `store/governor.py` — WAL checkpoint governor (`WalGovernor`, PASSIVE checkpoint per N commits, post-commit only).
- `store/migrate.py` — schema migrations.
- `source/service.py` — R01 atomic source replacement: one writer txn (revision + current pointer + lexical delta + event + outbox job); idempotency `(key, payload_hash)`; `ObsoleteRevisionError`; `_pre_commit_fault` crash seam.
- `source/project.py` — INT-1 projection pipeline: `drain_outbox` claim→segment→extract→reconcile→index; coverage checklist; obsolete-revision skip; retract fast-path; G2-R1 batch grouping (`ensure_grouped` once per pass).
- `source/coverage_state.py` — live coverage computation (`pending_coverage`, freshness).
- `source/views.py` — R02 span/segmentation views; `source/segmentation.py`, `source/boundary_signals.py` — structural segmentation.
- `filing/workers.py` — S3 supervised `MaintenanceWorker` (bounded batches, leases, backoff, backpressure signal, WAL size-triggered checkpoints, `BACKFILL_PACE_S`).
- `filing/holon.py` — R05 `HolonStore`: kind-aware cross-source holons, grouping vs linking vs canonicalizing split, O(k*d) fold path + exact oracle path, `_fold_state_for_add`.
- `filing/reconcile.py` — typed links (`holon_link`), canonical mappings (`holon_canonical`).
- `filing/engine.py` — filing engine, `text_vector`, centroid pack/unpack (`CENTROID_DIM`=128).
- `filing/taxonomy.py` — faceted classification.
- `router/cascade.py` — tier orchestrator T1 lexical → T2 beam → T3 hybrid + abstention; `SERVED_VECTOR_MAX_ATOMS=20000`; `QueryResult.route` branch labels.
- `router/hybrid.py` — T3 `FlatHybrid` BM25 + hashbow dense + RRF; `atom_delta`-watermark incremental warm; `prune_atom_delta_log` (write path only); G2-R3 shared warmed view (`State.shared_hybrid`).
- `router/vector_store.py` — S4 `ServedVectors`: published base + bounded delta/tombstones, frozen hashbow-IDF epoch, atomic CURRENT flip, `open`/`recover`/`ensure_served_persistent`.
- `router/index_adapter.py` — R03 numpy IVF ANN + crash-recoverable publication (segments → manifest → CURRENT → generation row).
- `router/lexical.py`, `router/fusion.py` (RRF), `router/beam.py` (faceted beam), `router/encoder.py`, `router/learned.py`, `router/projection.py`.
- `graph/extractor.py` — deterministic structural relation extractor (causal/compare/define/report/condition keywords; bounded windows; never raises on data).
- `graph/frames.py` — assertion/relation frames (polarity, modality, status vocabularies); `graph/store.py` — extraction-run persistence.
- `cordis/dossier.py` — R07 LLM-free evidence dossier compiler (filter-first, budget overflow explicit, named lexical `SUPPORT_METHOD`); `cordis/compiler.py`, `cordis/gate.py`, `cordis/models.py`, `cordis/plugin.py`, `cordis/tms.py`.
- `obsidian/*` — vault sync (parser, semantics, structure, watcher) — forbidden surface for gate work.
- `experimental/*` — qualifiers, repair, segment, shift, surprise (R08 prediction-error probes).
- `cli.py`, `api/app.py` — user surfaces.

### Tests (`tests/`)

Write/lifecycle: `test_write_path_integration.py`, `test_lanea_write_cost.py`, `test_store.py`, `test_store_lifecycle.py`, `test_migration.py`, `test_source_spans.py`, `test_parser_fidelity.py`, `test_bounded_reads.py`.
Serve/retrieval: `test_s4_vectors.py`, `test_served_delta_regression.py`, `test_served_path_integration.py`, `test_router.py`, `test_router_hybrid.py`, `test_index_adapter.py`, `test_learned_retrieval.py`, `test_incremental_centroids.py`, `test_concurrent_query_sync.py`.
Semantics/org: `test_s5_semantics.py` (graph), `test_holon_reconciliation.py`, `test_filing.py`, `test_local_graph.py`, `test_cross_source_grouping.py`, `test_assertion_extractor.py`, `test_prediction_error.py`.
Envelope/gates: `test_s6_envelope.py`, `test_s1_blackbox.py`, `test_s3_workers.py`, `test_qualification_gates.py` (16 pure-function G2 gate tests), `test_e2e.py`, `test_cli.py`, `test_cordis.py`, `test_context_compiler.py`, `test_dossier_compilation.py`, `test_scaffold.py`.

### Benchmarks & evidence

- `benchmarks/run_integrated_qualification.py` — R09 harness: real uvicorn + HTTP sync ingest, server-vs-e2e latency split, identical served reader stack for quality, lifecycle spots, BEIR scoring; G2 additions (full-Scifact 10k, frozen floor, child env, 600s drain, per-query rankings hook, 20-case capability hook).
- `benchmarks/run_beir_unified.py`, `run_quality_eval.py`, `run_trace_lite_benchmark.py`, `s6_*.py`, `blackbox_native_check.py`.
- `evidence/query-native/R09/` — `delivery.md`, `review.md` (B3 FAIL), `G2_FINAL_RECEIPT.md` (G2 FAIL), `contract_amendments.md` (§Q/§D/§B/§N/§L), `nfcorpus_constraint.md`, `capability_cases.json` (FROZEN 20), `repair_cycle1.json`, `qualification_dossier.json`, `scale_ladder[.attempt1, .pre10k].json`, `unified_eval[.attempt1].json`, `lifecycle_pass[.attempt1, .attempt2].log`, `rerun_10k.log`, `receipt.json`.
- `docs/builds/query-native/` — `PROJECT.md` (QN-01..10, D-01..), `ARCHITECTURE.md` (§1-10), `EVALUATION.md`, `RESEARCH.md`, packets `R00..R09`, `STATE.md`, `EXECUTION.md`, steering/supplement docs.
- `docs/builds/filing-cabinet/` — P01..P07 packets + architecture (legacy filing-cabinet build).

### Reference source A (`../trace`, read-only)

- `docs/architecture.md` → active contract `docs/v2-architecture.md`: FastAPI /v2 → TraceService → private object store + SqliteEventStore (events + outbox, nodes/node_versions, edge_versions/evidence_anchors, hot_inbox) + OutboxWorker; event ledger canonical, projections disposable.
- Also: `docs/CORTICAL_SUBSTRATE.md`, `SCALE_*.md` program docs, `ORIGINAL_REQUEST.md`, `TASK_PACKET_MILESTONE_1.md`.

### Reference source B (`trace lite and trillion dreams`)

- Session-archive planning docs `02..08_*.md` (e.g. `03_..._filing_cabinet_core_architecture.md`: passive filing cabinet, prediction-error atomization, doc-local holons).
- `research_specifications/00..04_*.md` (+ `schemas/`): 4-pillar blueprint — TL filing cabinet (Spine vs Cortex, FTS5, Hearst facets, S^383 centroids, 3-tier routing, RCU leases) + commitment ledger + CBGTC context compiler + step acceptance gate/MCB-TMS.

## 2. EXECUTION_PLAN.md revision .3 contract essentials (`TL-QN-2026-09-13.3`)

Architecture contract: Decoupled Fast Ingest (Atomic Revisions + Immediate FTS5) → Async Durable Outbox → Immutable Pointer-Swapped Vector Snapshots → Resilient Multi-Scale Retrieval. Ship the 250k envelope; C9-4 (1M) DEFERRED.

1. Boundaries: G1 after checkout check; sequential gates; preserve user work; narrow ownership expansion allowed; full suite at final integration; ≤2 repair cycles; no commit/push/deploy/1M.
2. Perf vs correctness: budgets not silently relaxed; G1 = fast ACK (P95 <35ms, 500-job backlog) vs extraction throughput (30s progress observation, completion diagnostic); stop on unhandled locking/resource errors.
3. Snapshot/visibility: zero maintenance-induced vector detachments post-baseline; readers pin immutable snapshot; publish-then-reclaim; post-ACK queries exclude superseded content; prove vector branch via instrumentation; ACK exposes durable identity + honest pending.
4. Frozen workload: record corpus/query hashes, counts, encoder, CPU/RAM, procs, mount; 4 query clients × 800 queries/180s + 120-op mutation client (60/40/20) with pre-existing backlog; e2e HTTP latency incl. errors; prove overlap; same schedule every rung; memory ceilings incl. worker children.
5. Budgets: G2 drain 600s; setup 15min; G3 prep 30min@50k / 60min@100k + 180s window + 180s drain-rate obs (40 jobs/s); G4 prep 120min + 180s window + 1000-job/120s recovery probe; startup 120s, shutdown 10s; HTTP timeout 10s; supervisor deadlines; report INCOMPLETE.
6. Bulk prep is optimization: must establish equivalent durable projections/coverage; verify counts, outbox, graph/holon, vector coverage; never complete jobs just because vectors built; no ANN-only corpus; 250k keeps fixture-limited wording.
7. Capability/quality: 20 frozen cases (≥5 negation/condition, ≥5 multi-sentence), exact citations, no unsupported relations, coverage reported not scored; same encoder/routing/compiler for quality+latency; SciFact floor 0.6400 (target 0.6500); NFCorpus diagnostic-only, separate run; repeat cases at 250k.
8. Recovery/provenance/verdicts: kill owned server only; verify revisions survive, resume, no-dup replay, retractions invisible, lexical+vector+dossier correct; cite immutable revision bytes; hash sources/harness/tests/schemas/deps/config pre+post; unique run dirs; C9-1/2/3/5 evidence verdicts; handoff = verdict + envelope + repro + manifest + blockers; then stop.

Gates: G1 decoupled ingest + non-blocking snapshots @2k → G2 R09 10k integrated qualification → G3 50k/100k ladder → G4 250k release dossier (ext4, concurrent updates, crypto receipt, byte-exact citations).

## 3. Gate 2 failure history (from `G2_FINAL_RECEIPT.md`, `repair_cycle1.json`, R09 review)

- Attempt-1 `G2-10000-20260913-074531` FAIL: RSS 448.9MB halt; drain 11.07 jobs/s; concurrent-4 P95 8878ms with 20/40 timeouts. Root causes: (a) per-thread GIL-bound full re-encodes serializing into ~11s warm storm; (b) O(N²) holon folds — `_fold_state_for_add` re-fetching ALL member text + double digest per `add_members` (~80ms/job at 8k members); (c) per-chunk re-seeds + duplicate caches (310MB setup HWM).
- Repair cycle 1 (COMPLETE): R1 batched grouping in `project.py` (`drain_outbox` stages segment+extract per job, ONE `ensure_grouped` per 64-job pass) → drain 43.9 jobs/s probe; R3 shared warmed view (`State.shared_hybrid` pooled immutable matrix, single rebuilder, zero-copy reader attach) → storm fixed, memory 114–247MB; nDCG A/B bit-identical 0.7380; G1 suites 58+37 green; under-drain P95 still over budget.
- Attempt-2 `G2-10000-20260913-100349` FAIL: drain PASS 529.9s ≤600s; memory REGRESSED 561.8MB (attribution unproven, contention-plausible); 87 unhandled `database is locked` HTTP 500s on POST /api/ingest (R1 batched passes holding SQLite's single write lock for seconds); WAL 3.95GB checkpoint starvation; latency improved ~6× (P95 1579ms); residual S4 catch-up GIL ~750ms/pass scoped out.
- Repair cycle 2 INCOMPLETE: contention discipline (short txns, busy-retry/503 with accounting, checkpoints, seed retry) under tightened integrated acceptance; stopped at final tripwire; partial tree edits in `project.py`/`app.py`/`hybrid.py`/`workers.py` preserved but UNVERIFIED.
- Never executed in any G2 attempt: SciFact nDCG@10 (quality UNKNOWN), 20/20 capability cases (blocked also by `relation` column-name mismatch: harness selects `subject`/`predicate`/`object`, schema has `subject_text`/`rel_type`/`object_text`), crash recovery, replay idempotency, parity. Per-query rankings never produced. G3/G4 not unlocked.
- R09-era antecedents: freshness backlog accounting ~1.4–1.6ms/pending revision dominating e2e (10k e2e p50 ~13.5s vs server ~13ms); ingest per-chunk decay 218→39 docs/s; drain 1–5 jobs/s; denominator-truncation harness artifact (corrected subset-scoped SciFact 0.6603 vs reported 0.6277); NFCorpus ~0.18 vs 0.3188 dispositioned as fallback-representation constraint (128d hashbow clinical-vocab collisions; INT-3 learned-BGE +0.1077).

## 4. Proposed module chunks (7)

- Chunk 1 — Write path & source durability: atomic replacement, idempotency, lexical delta, outbox enqueue. Key files: `source/service.py`, `store/database.py`, `store/schema.sql`, `store/outbox.py`, `store/migrate.py`. Stress: kill -9 mid-commit, idempotent replay storms, conflicting same-key payloads, concurrent edit contention. Epistemic focus: exactly-once effects, canonical-vs-derived separation.
- Chunk 2 — Projection pipeline & background workers: bounded drain, coverage/freshness, leases, backpressure, checkpointing. Key files: `source/project.py`, `source/coverage_state.py`, `filing/workers.py`, `store/generations.py`, `store/governor.py`. Stress: 10k backlog drain under query load, worker kill/resume, WAL growth under pinned readers. Epistemic focus: honest pending state, completion semantics (never complete jobs just because vectors built).
- Chunk 3 — Served vector snapshots & ANN: immutable segments, CURRENT flip, delta/tombstones, IDF epochs, IVF publication. Key files: `router/vector_store.py`, `router/index_adapter.py`, `router/encoder.py`, `router/learned.py`. Stress: per-mutation catch-up cost, seed under write pressure, compaction headroom, reader-hold vs writer-hold attribution. Epistemic focus: stale-vs-complete coverage labels, filter pushdown honesty.
- Chunk 4 — Query routing & retrieval: cascade tiers, hybrid warm, lexical, RRF, beam, abstention. Key files: `router/cascade.py`, `router/hybrid.py`, `router/lexical.py`, `router/fusion.py`, `router/beam.py`, `router/projection.py`. Stress: 800q/180s ×4 clients under drain, warm-storm regression, routing-recall counterexamples (late rowid, unfiled, hub minority). Epistemic focus: branch labels (`route`), vector-adequacy reporting, abstention calibration.
- Chunk 5 — Graph & holon organization: structural extraction, frames, grouping vs linking vs canonicalizing, incremental accumulators. Key files: `graph/extractor.py`, `graph/frames.py`, `graph/store.py`, `filing/holon.py`, `filing/reconcile.py`, `filing/engine.py`, `filing/taxonomy.py`, `source/views.py`, `source/segmentation.py`. Stress: large-holon fold cost, invalidation closures on retract, negation/condition preservation probes. Epistemic focus: grouping ≠ agreement, similarity never mints typed links.
- Chunk 6 — API surface & dossier compilation: HTTP contracts, concurrency model, evidence dossiers, Cordis adapter. Key files: `api/app.py`, `cordis/dossier.py`, `cordis/compiler.py`, `cordis/gate.py`, `cordis/models.py`, `cordis/tms.py`, `cli.py`. Stress: ACK latency under backlog, retraction freshness (0 stale citations), mandatory-overflow and atomic-group budget failures, byte-exact citation checks. Epistemic focus: support-state honesty (heuristic labeled, never NLI), provenance ≠ authorization.
- Chunk 7 — Qualification harness & evidence: integrated ladder, gate semantics, frozen cases, receipts. Key files: `benchmarks/run_integrated_qualification.py`, `tests/test_qualification_gates.py`, `evidence/query-native/R09/*`, `benchmarks/run_beir_unified.py`. Stress: same-schedule rung invariance, denominator/candidate-pool artifacts, crash/parity/replay probes, candidate-manifest binding. Epistemic focus: measured-vs-claimed separation (PASS/CONDITIONAL/INCOMPLETE/FAIL), no vacuous gates.

## 5. Shared glossary

- atom: canonical lexical unit row (id + text) backing FTS and vector indexes; 1 doc = 1 note = 1 atom on synthetic ladder rungs.
- holon: reusable overlapping subgraph grouping (kinds: discourse_span, thematic_cluster, claim_cluster/argument, query_synthesis/query_dossier/derived_synthesis); holds memberships, never merged strings; membership ≠ agreement.
- served snapshot: immutable published generation readers pin for a request lifetime (vector base + delta/tombstones, filing generation); replacements publish via atomic pointer swap; old resources reclaimed after readers release.
- outbox drain: bounded background consumption of `projection_job` rows (claim → project → complete/fail); ≤64 jobs per call; autonomous worker with leases and idempotent completion.
- WAL checkpoint: SQLite write-ahead-log truncation via `wal_checkpoint(PASSIVE)`; governed post-commit by doc count (`WalGovernor`) and size-triggered worker passes; starves under pinned readers or a held write lock.
- nDCG: normalized discounted cumulative gain @10; BEIR retrieval-quality metric with frozen floors (SciFact 0.6400, target 0.6500; NFCorpus diagnostic ~0.19 under fallback hashbow).
- routing recall: fraction of queries where the cascade's chosen branch (lexical/vector/beam/hybrid) retrieves the relevant evidence; measured against counterexamples (late-rowid, unfiled, hub-minority, paraphrase).
- soft vs hard state: soft = rebuildable projections (FTS postings, vectors, holon descriptors, caches — may lag, must be labeled); hard = canonical durable records (source revisions, spans, events, outbox jobs — crash-safe, versioned).
