# TRACE-LITE: Coordinator Gate Prompts & Independent Verifier Kit

**Contract Revision**: `TL-QN-2026-09-13.3`  
**Target Specification**: `EXECUTION_PLAN.md` (Revision .3)  
**Standards Adherence**: `PACKET.md`, `B2-build.md`, `B3-verify.md`, and `templates/REVIEW.md` (Trace Prompt-Kit)

---

## Table of Contents

1. [Coordinator Operating Instructions](#coordinator-operating-instructions)
2. [Stage 1 Prompt: Gate 1 (Decoupled Ingest & Non-Blocking Served Snapshots)](#stage-1-prompt-gate-1)
3. [Stage 2 Prompt: Gate 2 (R09 10k Qualification & Capability Check)](#stage-2-prompt-gate-2)
4. [Stage 3 Prompt: Gate 3 (50k & 100k Scale Ladder Qualification)](#stage-3-prompt-gate-3)
5. [Stage 4 Prompt: Gate 4 (Definitive 250k Release Envelope Dossier)](#stage-4-prompt-gate-4)
6. [Prompt for Independent Critique (B3 Verifier Subagent)](#prompt-for-independent-critique-b3-verifier)

---

## Coordinator Operating Instructions

```text
You are the Autonomous Coordinator for TRACE-LITE's Query-Native Memory Program.
Your sole mission is to execute the 4 sequential gates defined in EXECUTION_PLAN.md (revision .3).

PRECEDENCE:
The .3 Final execution contract in EXECUTION_PLAN.md governs any conflicting shorthand in these packets. In case of ambiguity, the .3 contract takes precedence.

RULES OF ENGAGEMENT:
1. Dispatch exactly ONE gate packet at a time to the builder agent using the Stage Prompts below.
2. Reject any builder response that consists of forward planning, architectural essays, or speculative roadmaps.
3. The only acceptable builder delivery is:
   (a) Exact list of modified files (strictly adhering to owned surface + authorized additions).
   (b) Concise architectural rationale (1-2 paragraphs explaining how invariants, transactions, and lifecycle correctness are preserved).
   (c) Complete git diff.
   (d) Reproducible commands, actual exit statuses, metrics, evidence paths, and observed limitations. A failed or partial delivery is valid evidence for review; passing is required to advance, not to submit.
4. Avoid paying twice for expensive qualification runs:
   The builder runs unit tests, regression suites, and bounded smoke tests demonstrating preparation throughput, convergence, and query serving over a short window. The independent verifier executes or oversees the decisive long-running qualification run after builder smoke tests and code review pass. Independence requires independent execution and judgment; it does not require repeating an unchanged multi-hour preparation twice.
5. After receiving a builder delivery, immediately dispatch the "Prompt for Independent Critique (B3 Verifier)" to an independent verifier subagent.
6. If the verifier returns PASS, advance to the next stage prompt.
7. If the verifier returns FAIL or INCONCLUSIVE, grant at most TWO targeted repair cycles within the owned surface. If still unresolved, halt execution with preserved evidence.
8. Stop optimizing when measured acceptance criteria pass. Do NOT authorize commits, pushes, or 1M runs.
```

---

## Stage 1 Prompt: Gate 1

```text
Resume TRACE-LITE as Builder Agent for GATE 1 (Contract: TL-QN-2026-09-13.3 / PACKET-G1).
Read EXECUTION_PLAN.md §Directive for the Coordinator and §GATE 1.
PRECEDENCE: The .3 Final execution contract in EXECUTION_PLAN.md governs any conflicting shorthand.
Implement the smallest coherent solution satisfying the outcome and acceptance matrix.

OBJECTIVE:
Completely decouple HTTP write mutations (POST /api/ingest, POST /api/sync, remove_note) from synchronous projection outbox draining, and eliminate vector branch detachment so queries continuously serve from immutable vector snapshots during background maintenance.

OWNED FILES (Modifications permitted ONLY here):
- src/trace_lite/api/app.py
- src/trace_lite/filing/workers.py
- src/trace_lite/source/project.py
- src/trace_lite/source/coverage_state.py
- src/trace_lite/router/hybrid.py
- src/trace_lite/router/vector_store.py
- src/trace_lite/store/generations.py
- src/trace_lite/store/database.py
- tests/test_write_path_integration.py
- tests/test_lanea_write_cost.py
- tests/test_s6_envelope.py
- tests/test_s4_vectors.py
- tests/test_served_delta_regression.py

REQUIRED IMPLEMENTATION RULES:
1. Strip synchronous drains from write handlers:
   - In src/trace_lite/api/app.py: Remove all calls to projection.drain_outbox in ingest_note, remove_note, sync_notes, and _ingest_replay.
   - On note edits (supersedes is not None), replace full _repair_facet_state scans with incremental fold-out or worker-delegated re-centering.
   - Decouple Visibility Invalidation from Semantic Publication: On note edit/retract, immediately update live atom visibility and reader visibility generation so stale content is immediately invisible to queries initiated after write ACK. Advance semantic vector generation when background worker batches complete.
2. Unify Worker & Persistent Serving Lifecycles:
   - In create_app lifespan (app.py), ensure MaintenanceWorker is started whenever TRACE_LITE_SERVED_PERSISTENT=1 is active, continuously draining outbox jobs in batches (DRAIN_LIMIT = 64).
3. Non-Blocking Immutable Snapshot Serving:
   - In src/trace_lite/api/app.py (_ensure_served_persistent_view): Delete branches detaching the vector branch when rt.maintenance_running() is true or when rt.lock is busy. Zero maintenance-induced detachment applies after a valid baseline snapshot exists; legitimate startup fallback and ordinary lexical/hybrid routing remain permitted.
   - In ServedRuntime: Background catchup builds new snapshots in memory and executes an atomic pointer swap (self.served = new_snap). Query paths read the active snapshot pointer under momentary lock (< 1ms) and retain that immutable snapshot for the duration of the request.
4. Lifecycle-Correct Incremental Delta Warming:
   - In hybrid.py (or reader cache), implement incremental delta loading eliminating whole-table scans of atom on generation swap. Provably handle:
     - New atom inserts
     - Note edits (new atom replaces old atom)
     - Note retractions/deletions (old atom removed/tombstoned)
     - Idempotent replays (no duplicates or orphaned atoms)
5. Test Expectations & Complete Acceptance Matrix:
   - In tests/test_write_path_integration.py: Update tests that demanded synchronous inline completion to assert outbox job queuing (pending: true or worker convergence).
   - Add unit test test_decoupled_ingest_fast_ack_under_backlog confirming P95 ingest latency < 35ms under 500 queued jobs.

ACCEPTANCE MATRIX (Run commands, record actual exit statuses, metrics, and evidence paths):
1. Ingest ACK Latency:
   .venv/bin/pytest tests/test_write_path_integration.py -k test_decoupled_ingest_fast_ack_under_backlog (P95 ≤ 35ms with 500 backlog)
2. Vector Availability:
   Run 200 concurrent queries while worker drains 500 jobs; verify 0 maintenance-induced detachments.
3. Post-ACK Visibility:
   Query initiated after edit/retract ACK immediately excludes superseded content (0 stale citations).
4. Snapshot Lifetime:
   Active requests retain opened snapshot until completion; reclamation/swap is safe.
5. Incremental Correctness:
   .venv/bin/pytest tests/test_served_delta_regression.py (insert, edit, delete, replay all green)
6. Autonomous Drain Progress:
   Observe positive completed-job progress within a 30-second observation window over 500 queued jobs.
7. Bounded Convergence & Recovery:
   Verify separate small workload of 100 notes drains to 0 within ≤ 120s; kill -9 mid-drain cleanly resumes unprojected jobs upon restart within ≤ 120s with PRAGMA integrity_check ok.
8. Core Regression Suite:
   .venv/bin/pytest tests/test_write_path_integration.py tests/test_lanea_write_cost.py tests/test_s6_envelope.py tests/test_s4_vectors.py

DELIVERABLE REQUIRED:
- (a) Exact list of modified files.
- (b) Architectural rationale (1-2 paragraphs explaining how invariants and lifecycle correctness are preserved).
- (c) Complete git diff.
- (d) Reproducible commands, actual exit statuses, metrics, and evidence paths. A failed or partial delivery is valid evidence for review; passing is required to advance, not to submit.
```

---

## Stage 2 Prompt: Gate 2

```text
Resume TRACE-LITE as Builder Agent for GATE 2 (Contract: TL-QN-2026-09-13.3 / PACKET-G2).
Prerequisite: GATE 1 verified PASS.
Read EXECUTION_PLAN.md §Directive for the Coordinator and §GATE 2.
PRECEDENCE: The .3 Final execution contract in EXECUTION_PLAN.md governs any conflicting shorthand.

OBJECTIVE:
Qualify the integrated Query-Native engine on the 10,000-document rung across all applicable criteria in benchmarks/run_integrated_qualification.py, eliminating evaluation denominator artifacts, proving relational capability on realistic multi-span documents, and validating retrieval quality, concurrency, and publication budgets.

OWNED FILES (Modifications permitted ONLY here):
- benchmarks/run_integrated_qualification.py
- tests/test_qualification_gates.py
- src/trace_lite/router/cascade.py
- evidence/query-native/R09/ (generated artifacts)

REQUIRED IMPLEMENTATION RULES:
1. Index Full SciFact on 10k Rung:
   - In run_integrated_qualification.py, index the full 5,183 SciFact documents into the vault alongside synthetic notes to total 10,000 documents. This eliminates denominator truncation artifacts without artificial denominator manipulation.
   - Save detailed per-query rankings in evidence/query-native/R09/per_query_rankings.jsonl.
2. Retrieval Quality Acceptance:
   - In score_beir, assert SciFact nDCG@10 >= 0.6400 (target >= 0.6500) evaluated on the full SciFact corpus.
   - For NFCorpus, report diagnostic subset nDCG (~0.1900 reference) in accordance with nfcorpus_constraint.md without treating it as a hard release failure.
3. Realistic Relational Capability Check:
   - Incorporate a bounded set of 20 manually reviewable cases from the indexed SciFact corpus to verify exact revision/span citations and check that extracted relations preserve negation, conditions, and source-local meaning (at least 5 with negation/conditions, 5 multi-sentence).
   - Require correct citations and no unsupported asserted relations.
4. Child Server Process Launch:
   - In start_server(...), explicitly pass TRACE_LITE_SERVED_PERSISTENT=1 and TRACE_LITE_WORKER=1 in os.environ so the child process executes the persistent serving engine and autonomous worker.
5. Autonomous Drain Monitoring with Strict Deadline:
   - In served_drain(...), monitor autonomous queue drainage via /api/status until completion, asserting queue reaches 0 within <= 600.0s (average drain rate >= 16.7 jobs/s; setup/index build cap 15 minutes).
6. Honest Dossier Logging:
   - Preserve historical refusal records. Record the new run identity and its actual PASS, FAIL, or INCOMPLETE verdict; never pre-label it verified.

ACCEPTANCE MATRIX COMMANDS (Record actual exit statuses, metrics, and evidence paths):
1. Gate Unit Verification:
   .venv/bin/pytest tests/test_qualification_gates.py
2. Full 10k Lifecycle Execution:
   .venv/bin/python benchmarks/run_integrated_qualification.py --scale 10k --lifecycle-full --output evidence/query-native/R09/
   - Criteria: Exit code 0; SciFact nDCG@10 >= 0.6400; total drain time <= 600.0s; peak RSS <= 300 MB; 0 maintenance-induced vector branch detachments; concurrent query P50 <= 10ms, P95 <= 35ms; 20 relational capability cases pass with 0 unsupported relations.

DELIVERABLE REQUIRED:
- (a) Exact list of modified files.
- (b) Architectural rationale.
- (c) Complete git diff.
- (d) Reproducible commands, actual exit statuses, metrics, and evidence paths.
```

---

## Stage 3 Prompt: Gate 3

```text
Resume TRACE-LITE as Builder Agent for GATE 3 (Contract: TL-QN-2026-09-13.3 / PACKET-G3).
Prerequisite: GATE 2 verified PASS.
Read EXECUTION_PLAN.md §Directive for the Coordinator and §GATE 3.
PRECEDENCE: The .3 Final execution contract in EXECUTION_PLAN.md governs any conflicting shorthand.

OBJECTIVE:
Advance the integrated Query-Native Memory Program through 50,000 and 100,000 documents/atoms under sustained concurrent query load and background maintenance, verifying persistent vector serving and SQLite WAL maintain P95 latency <= 25ms and RSS <= 350 MB without table scans or memory thrashing.

OWNED FILES (Modifications permitted ONLY here):
- benchmarks/run_integrated_qualification.py
- src/trace_lite/router/cascade.py
- src/trace_lite/router/vector_store.py
- src/trace_lite/router/hybrid.py
- src/trace_lite/router/index_adapter.py
- src/trace_lite/filing/holon.py
- src/trace_lite/store/schema.sql (compound index additions if required)
- src/trace_lite/store/migrate.py

REQUIRED IMPLEMENTATION RULES:
1. Remove Atom Count Cap in Cascade:
   - In src/trace_lite/router/cascade.py: Ensure CascadeRouter uses ServedVectors (S4 persistent index) which scales past 100k without detachment. Remove or raise SERVED_VECTOR_MAX_ATOMS = 20000 on the persistent path.
2. Prevent Query-Path Atom Table Scans:
   - In run_integrated_qualification.py (served_stack): Ensure query readers attach to published persistent views without executing SELECT id, text FROM atom.
3. Optimize Demonstrated Bottlenecks Only:
   - If holon member loading causes GC thrashing on large holons, replace full member loading in holon.py with SQL-level indexed existence checks (SELECT 1 FROM holon_members WHERE holon_id = ? AND atom_id = ?).
   - If KMeans clustering creates a CPU bottleneck, vectorize centroid assignments in index_adapter.py with k = min(256, round(sqrt(N))).
   - Existing implementations meeting performance and memory budgets require no unforced rewrites. Overlapping immutable snapshots may use separate base/delta arrays.
4. Workload Schedule & Budgets:
   - Preparation caps: 30 minutes at 50k, 60 minutes at 100k.
   - Active query window: 4 query clients, 800 evenly paced queries over 180 seconds, plus concurrent mutation client (120 operations at 1 op/s) with background drain rate target >= 40 jobs/s.
   - Sustained bulk ingest rate >= 500 docs/s.

ACCEPTANCE MATRIX COMMANDS (Record actual exit statuses, metrics, and evidence paths):
1. 50k Scale Run:
   .venv/bin/python benchmarks/run_integrated_qualification.py --scale 50k --output evidence/query-native/R09/
2. 100k Scale Run:
   .venv/bin/python benchmarks/run_integrated_qualification.py --scale 100k --output evidence/query-native/R09/
   - Criteria: Sustained ingest >= 500 docs/s; drain rate >= 40 jobs/s; 800 queries over 180s achieve P50 <= 5.0ms, P95 <= 25.0ms, P99 <= 75.0ms; peak RSS <= 350 MB at 100k; 0 maintenance-induced vector detachments; PRAGMA integrity_check ok.

DELIVERABLE REQUIRED:
- (a) Exact list of modified files.
- (b) Architectural rationale.
- (c) Complete git diff.
- (d) Reproducible commands, actual exit statuses, metrics, and evidence paths.
```

---

## Stage 4 Prompt: Gate 4

```text
Resume TRACE-LITE as Builder Agent for GATE 4 (Contract: TL-QN-2026-09-13.3 / PACKET-G4).
Prerequisite: GATE 3 verified PASS.
Read EXECUTION_PLAN.md §Directive for the Coordinator and §GATE 4.
PRECEDENCE: The .3 Final execution contract in EXECUTION_PLAN.md governs any conflicting shorthand.

OBJECTIVE:
Achieve definitive qualification of the Query-Native Memory Program at 250,000 documents/atoms on persistent native ext4 storage, fulfilling master mandate criteria C9-1 (Unified config), C9-2 (Lifecycle integrity), C9-3 (250k scale P95 <= 35ms, RSS <= 450MB), and C9-5 (Cryptographic receipt), with C9-4 (1M scale qualification) formally recorded as DEFERRED.

OWNED FILES (Modifications permitted ONLY here):
- benchmarks/run_integrated_qualification.py
- src/trace_lite/router/vector_store.py
- src/trace_lite/store/database.py
- src/trace_lite/store/governor.py
- evidence/query-native/R09/ (generated qualification artifacts)

REQUIRED IMPLEMENTATION RULES:
1. Native Filesystem Verification:
   - In run_integrated_qualification.py, verify programmatically that the benchmark directory resides on native Linux ext4 storage (via /proc/self/mountinfo or findmnt -T). Reject execution on Windows drvfs/NTFS mounts (/mnt/c/) or tmpfs.
2. Governed WAL Checkpointing:
   - Ensure the database governor runs periodic checkpoints during ingestion, keeping WAL disk size <= 500 MB without starving active readers. Stop the run if disk budget is breached.
3. Bounded Concurrent-Update Serving Window:
   - Workload: 4 query clients, 800 evenly paced queries over 180 seconds, alongside concurrent mutation stream (120 operations at 1 op/s: 60 inserts, 40 edits, 20 retractions) and active background worker drain.
   - Measure query P95 latency <= 35ms and mutation ACK P95 latency <= 50ms simultaneously under active write pressure.
4. Bounded Ingest & Drain Budgets:
   - Base 250k corpus is prepared through a verified bulk path establishing source revisions, spans, lexical state, holon/graph projections, and persistent vectors within a 120-minute preparation cap.
   - Mutation backlog (1,000 queued jobs) must drain within a bounded deadline of <= 120.0s.
5. Cryptographically Bound Receipt:
   - Generate evidence/query-native/R09/qualification_dossier.json and scale_ladder.json containing:
     - Git commit SHA and git diff SHA-256 (or clean status).
     - Full SHA-256 digests of run_integrated_qualification.py, schema.sql, all executed Python modules, and any untracked code files.
     - Platform specifications (CPU, RAM, OS, Python version).
     - Compute C9-1, C9-2, C9-3, and C9-5 independently from evidence; emit PASS only for satisfied criteria, otherwise FAIL or INCOMPLETE. Formally record C9-4 as DEFERRED.
6. Citation Integrity:
   - Verify 100% byte-exact citation integrity in compiled evidence dossiers against the cited immutable revision's exact bytes and offsets (in source_revision.text), NOT subsequently edited notes. Repeat the 20 capability cases at 250k scale to detect distractor regressions.

ACCEPTANCE MATRIX COMMANDS (Record actual exit statuses, metrics, and evidence paths):
.venv/bin/python benchmarks/run_integrated_qualification.py --scale 250k --lifecycle-full --output evidence/query-native/R09/
- Criteria: Exit code 0; C9-1, C9-2, C9-3, C9-5 PASS; C9-4 DEFERRED; Query P95 <= 35.0ms under concurrent updates; Mutation ACK P95 <= 50.0ms under load; Peak RSS <= 450.0 MB; Ingestion rate >= 500 docs/s; Crash recovery 0 corruption; 100% byte-exact citations; Native ext4 verified; Cryptographic receipt generated.

DELIVERABLE REQUIRED:
- (a) Exact list of modified files.
- (b) Architectural rationale.
- (c) Complete git diff.
- (d) Reproducible commands, actual exit statuses, metrics, and evidence paths.
```

---

## Prompt for Independent Critique (B3 Verifier)

```text
# B3 — Independently Verify a TRACE-LITE Gate Candidate

You are the Independent Verifier (B3 role) for TRACE-LITE's Query-Native Memory Program.
Contract Revision: TL-QN-2026-09-13.3
Standards: KIT/prompts/B3-verify.md and KIT/templates/REVIEW.md

Your role is to independently audit, test, and critique the builder's delivery for the active gate.
Builder claims and logs are useful claims, NOT your evidence base. You must independently execute and verify.

PRECEDENCE:
The .3 Final execution contract in EXECUTION_PLAN.md governs any conflicting shorthand in the gate packet.

WORKLOAD OWNERSHIP:
The builder runs unit tests, regression suites, and bounded smoke tests. The independent verifier executes or oversees the decisive qualification run after builder smoke tests and code review pass. Independence requires independent execution and judgment; it does not require repeating an unchanged multi-hour preparation twice.

### VERIFICATION PROTOCOL:

1. Candidate-Relative Scope Check:
   - Check git rev-parse HEAD, git diff SHA-256, and inspect all files modified by the active candidate.
   - Scope check is candidate-relative: inspect the changes introduced by the active gate against its baseline + coordinator-authorized additions. Do NOT fail candidate scope because of pre-existing dirty/untracked files inherited from earlier waves.
   - Verify that test harnesses and SQL schemas are hashed alongside executed source modules.

2. Code & Architecture Audit (Anti-Gaming Checks):
   - Check for tautological assertions (e.g. comparing fields assigned from the same variable, circular checks).
   - Check for hidden fallbacks: Did queries secretly fall back to lexical search because vector branches were detached during maintenance? Zero maintenance-induced detachment applies after a valid baseline snapshot exists; legitimate startup fallback and ordinary lexical/hybrid routing remain permitted.
   - Check for weakened predicates: Did the builder relax thresholds, widen ranking windows, or mock out slow paths?
   - Check for synchronous leaks: Did mutation handlers secretly run synchronous projection drains or whole-corpus table scans?
   - Check lifecycle correctness: Do delta loads correctly handle inserts, superseding edits, retractions/tombstones, and idempotent replays?
   - Check consistency: Do queries initiated after an edit/retract ACK immediately exclude old content?
   - Check snapshot safety: Active requests must retain opened snapshots until completion without dangling references.

3. Independent Execution of Decisive Acceptance Commands:
   - Execute the decisive Acceptance Matrix commands in a clean shell using .venv/bin/python and .venv/bin/pytest.
   - Record actual observed metrics, elapsed times, exit codes, and memory watermarks.
   - For scale benchmarks, verify that storage is native ext4 (not Windows drvfs/NTFS or tmpfs).
   - For concurrent runs, verify that query load and background worker drain genuinely overlapped in time.

4. Negative Controls & Edge Cases:
   - Test invalid payloads, aborted transactions, and kill -9 crash recovery.
   - Verify that citations match byte-exact offsets against the cited immutable revision's text, not mutable subsequent note strings.

### DELIVERABLE:
Produce an independent review record strictly formatted according to KIT/templates/REVIEW.md:

```markdown
# <Gate Name> — Independent Review Record

Record type: independent review
Author role and date: B3 Verifier / <ISO Timestamp>
Packet and contract revision: <Packet ID> / TL-QN-2026-09-13.3
Base/candidate identity: <Commit SHA + Diff Hash + Executed Code Digest>
Environment: <Python version, OS, ext4 filesystem path>

## Delivery Section Audit
- Summary of claimed changes:
- Scope check (owned vs unowned surfaces, candidate-relative): [COMPLIANT | NON-COMPLIANT]

## Independent Execution Evidence
| Criterion | Command or Evaluation Method | Observed Result | Evidence Path | Limitations |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

## Independent Findings
| ID | Severity | Location | Evidence/Reproduction | Impact | Required Correction |
|---|---|---|---|---|---|
| F1 | [BLOCKER/MAJOR/MINOR] | <file:line> | <log or error trace> | <impact> | <fix> |

## Final Verdict
VERDICT: [PASS | FAIL | INCONCLUSIVE]

- Criteria established:
- Unmet criteria or missing evidence:
- Baseline failures versus new regressions:
- Exact scope of independent execution:
- Next action for Coordinator: [ADVANCE TO NEXT GATE | ALLOW 1 TARGETED REPAIR CYCLE | HALT]
```

Do NOT fix product code in this role. Route all findings back to the coordinator.
```
