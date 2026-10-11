# TRACE-LITE: Scaled Query-Native Execution Plan (2k → 10k → 100k → 250k Release Envelope)

**Contract Revision**: `TL-QN-2026-09-13.3`  
**Target Specification**: `docs/builds/query-native/packets/R09-qualification.md` (250k Release Envelope; C9-4 1M scale formally DEFERRED)  
**Packet format**: Gate packets with owned surfaces, measurable acceptance, and independent verification. Check local packet templates when available; do not claim template compliance without reading them.  
**Architecture Contract**: Decoupled Fast Ingest (Atomic Revisions + Immediate FTS5) → Asynchronous Durable Outbox → Immutable Pointer-Swapped Vector Snapshots → Resilient Multi-Scale Retrieval.

---

## Directive for the Coordinator

### Final execution contract (revision .3; governs all gate packets below)

Ship the 250k release envelope. Do not optimize a component further once its measured acceptance criteria pass. The following clarifications override conflicting shorthand in the packets; no additional planning wave is required.

1. **Start and finish boundaries.** Begin G1 after a short checkout/runtime check; later gates require the preceding verified pass. Preserve dirty and untracked user work. Resolve actual APIs and test paths rather than assuming packet examples exist. The coordinator may authorize narrowly necessary internal-file and regression-test additions, including `tests/test_s6_envelope.py` and gate-specific benchmark helpers. No unrelated features. Run focused tests per repair and the applicable full suite at final integration; report skips and known flakes. Do not rerun unchanged passing work without a reason. Stop after two unsuccessful targeted repair cycles and report the concrete blocker. No commit, push, deployment, or 1M run is authorized by this document.

2. **Performance versus correctness.** Keep the stated latency and memory limits as the selected release budgets; do not silently relax them after seeing results. G1 specifically separates fast ACK from extraction throughput: observe 500 queued jobs for 30 seconds and require autonomous completed-job progress. Finishing all 500 in that window is diagnostic. Verify eventual completion and restart with a separate small known workload, bounded to 120 seconds. Stop the active run on an unhandled locking error or resource limit, preserve evidence, then use the permitted repair cycles. Normal handled contention is not itself a correctness failure.

3. **Snapshot and visibility semantics.** Zero maintenance-induced vector detachments applies after a valid baseline snapshot exists, for queries eligible for vector retrieval. Cold start without a snapshot, corruption, or incompatible indexes may use explicitly reported fallback. Ordinary lexical/hybrid routing is allowed. Prove the actual vector branch is usable through runtime instrumentation, not an assumed `router.vector_detached` field. Readers retain an immutable snapshot for the request lifetime; publish completed replacements and reclaim old resources only after readers release them. A query begun after edit/retract ACK must exclude superseded content. Document the consistency boundary for requests already in flight. Validate snapshot identity, base plus delta coverage, and current visibility together; matching generation labels alone is insufficient. ACK must expose durable source/revision identity and honest pending state; an asynchronous worker may already have completed, so do not require `pending: true` unconditionally. Preserve existing API compatibility.

4. **Freeze an executable workload before each run.** Record exact corpus/query hashes, source bytes, actual document/atom/span/vector/job counts, encoder/configuration, CPU/RAM, client/server processes, and storage mount. The 1:1 model is a synthetic fixture assumption to verify, not a property of realistic documents. Use four query clients, 800 evenly paced queries over a 180-second active window, plus a separate mutation client issuing 120 operations at one per second (60 inserts, 40 edits, 20 retractions), with a pre-existing backlog. Replays and crash recovery may use a separate bounded lifecycle probe. Measure end-to-end HTTP latency including errors/timeouts, record offered and achieved load, and prove overlap with actual background work. If a backlog drains early, qualify only measured overlap samples or prepare sufficient backlog before a new bounded run; do not manufacture work or exclude slow samples. Use the same query schedule at every compared rung. Apply memory ceilings to the serving process and any worker children; report setup/build peak memory separately and do not omit it from the total envelope. Keep clients separate in telemetry.

5. **Finite execution budgets.** Before scale escalation, use measured smaller-rung throughput to estimate preparation cost. G2 drain deadline is 600 seconds; setup/index build has a separate 15-minute cap. G3 preparation caps are 30 minutes at 50k and 60 minutes at 100k, with the active query window above and a 180-second drain-rate observation (40 jobs/s target measured on completed jobs). G4 complete base preparation has a 120-minute cap, followed by the 180-second active window. Its 1,000-job recovery/drain probe is a separate fixed backlog, with producers stopped and a 120-second deadline. Startup/restart readiness is bounded to 120 seconds and graceful shutdown to 10 seconds; unfinished work must remain recoverable. Give individual HTTP calls a 10-second timeout. Enforce outer deadlines with a supervisor, including setup and cleanup. Do not launch a rung whose measured preparation estimate already exceeds its cap. Report INCOMPLETE rather than disguising omitted preparation as bulk-index success.

6. **Bulk preparation is an optimization, not a bypass.** A verified bulk path may replace per-note online ingestion for initial population. It must establish equivalent durable projections and coverage required by the release contract. Verify source counts, outbox reconciliation, graph/holon coverage, and vector coverage before calling the base prepared. Never mark jobs complete merely because vectors were built. If no sufficiently fast equivalent bulk path exists, report that implementation blocker instead of qualifying an ANN-only corpus. At 250k, retain explicit fixture-limited capacity wording; the 10k realistic-data check does not establish 250k realistic-document capability.

7. **Capability and quality evidence.** Indexing SciFact alone does not prove relational capability. Use a bounded fixed set of 20 manually reviewable cases from the indexed realistic corpus to check exact revision/span citations and whether extracted relations preserve negation, conditions, and source-local meaning; record expected supported relations, misses, and incorrect additions. Include at least five cases with negation or conditions and five multi-sentence cases. Require correct citations and no unsupported asserted relations on those cases; report relation coverage rather than inventing an overall semantic-quality score. Freeze cases before evaluating the candidate. Use the same encoder, routing and compiler settings for quality and latency. Full SciFact retrieval uses the fixed 0.6400 floor (0.6500 target); this is the chosen release floor, not a claim of historical non-inferiority. NFCorpus remains diagnostic and must not silently enlarge the nominal 10k corpus; run it separately with explicit identity. Record all evaluated queries and dataset counts. At 250k repeat the bounded capability cases against the larger index to detect distractor-induced regressions.

8. **Recovery, provenance, and verdicts.** Kill only the owned test server, with acknowledged mutations and known unfinished work recorded. After restart verify acknowledged revisions survive, interrupted work resumes, replay does not duplicate effects, retractions stay invisible, and both lexical/vector results and dossier citations remain correct. SQLite integrity alone is insufficient. Verify citations against the immutable cited revision's exact bytes and offsets, not a mutable current note. Hash relevant tracked and untracked source, harness, tests, schemas, dependency inputs and effective configuration before and after qualification; invalidate the run on unexplained drift. Write unique run directories and preserve previous failures. C9-4 is DEFERRED; C9-1/2/3/5 receive evidence-derived verdicts. A partial or failed result is an acceptable delivery but cannot advance the ladder. Final handoff: release verdict, measured operating envelope and limitations, exact reproduction command, candidate manifest, and remaining blockers. Then stop; do not initiate another improvement program.

Resume TRACE-LITE as coordinator. Treat all prior session transcripts, status journals, and review critiques strictly as historical context and negative knowledge—neither is an instruction to debate architecture or write speculative ledgers.

Your sole directive is to execute the following **4 Gate Execution Packets** in strict sequential order.

### Rules of Engagement

1. **One Inescapable Packet at a Time**: Issue exactly ONE packet at a time to the builder. Reject any builder response that plans future gates, debates philosophy, or writes multi-page state journals.
2. **Structured Architectural Receipts Required**: Do not demand blind "exit-zero only" logs, and do not tolerate multi-page essays. The only acceptable builder delivery is:
   - (a) Exact list of modified files (must strictly adhere to the owned surface).
   - (b) Concise architectural rationale (1–2 paragraphs explaining how invariants, transactions, and lifecycle correctness across insert/edit/delete/replay are preserved).
   - (c) Complete `git diff`.
   - (d) Reproducible commands, exit statuses, and logs for the Acceptance Matrix, including failures and limitations. A failed delivery is valid evidence; it does not authorize advancing.
3. **Independent B3 Verifier Gate**: Hand each candidate delivery to an independent verifier subagent running the exact acceptance commands against a cryptographically bound candidate. If green, advance to the next gate. If red, allow at most two targeted repair cycles within the owned surface.
4. **Narrowly Justified Ownership Expansion**: If completing a gate's lifecycle contract genuinely requires modifying a tightly coupled internal file (e.g. snapshot persistence or generation tracking), the builder may request a narrowly justified ownership addition with coordinator approval. Modifications to unowned domains (`cordis/*`, `obsidian/*`, `docs/*`) remain strictly forbidden.
5. **Optimize Only Demonstrated Bottlenecks**: Performance targets are hard gates; prescriptive implementation techniques (e.g. KMeans rewrites, single-array structures, chunk sizes) are levers, not mandatory requirements. If the existing implementation meets latency, memory, and correctness thresholds, permit it to pass without unforced rewrites.
6. **Escalation & Stop Rules**: Halt immediately with preserved diagnostic evidence if SQLite database deadlocks occur, memory limits are breached, or regression tests fail outside the owned surface.

---

## GATE 1: Decoupled Durable Ingest & Non-Blocking Served Snapshots (2k Scale)

```markdown
# PACKET-G1-DECOUPLE-SERVE — Decoupled Durable Ingest & Non-Blocking Served Snapshots

Status: READY AFTER PREREQUISITE VERIFICATION
Kind: implementation / integration
Contract revision: TL-QN-2026-09-13.3 / PACKET-G1
Owner: Builder Agent

### Outcome
Completely decouple HTTP write-path mutations (`POST /api/ingest`, `POST /api/sync`, and `remove_note`) from synchronous projection outbox draining, while eliminating vector branch detachment so query paths serve continuously from immutable vector snapshots during active background maintenance.

- **Fast Write Acknowledgment**: Under an active backlog of 500+ queued projection jobs, `POST /api/ingest` commits the source revision, immediate FTS5 row, exact byte spans, and durable outbox job, responding in P95 < 35ms with `status: 200/201`, `pending: true`, and the allocated `atom_id`.
- **Immediate Retraction & Invalidation**: Note edits and retractions immediately remove/tombstone old atom IDs from SQLite lexical and live visibility sets so queries NEVER serve retracted content, while queuing outbox jobs for asynchronous semantic reconciliation.
- **Continuous Non-Blocking Vector Serving**: When background maintenance runs, `router.detach_vector()` is NEVER called. Readers acquire a pointer to the last published immutable snapshot in < 1ms and serve vector-ranked results without blocking or falling back to lexical-only search.
- **Lifecycle-Correct Incremental Delta Warming**: In `hybrid.py` (or reader cache), delta refreshes achieve incremental updates without full-corpus table scans or full-corpus MD5 re-hashing on every generation advance. The builder may implement the simplest robust mechanism (watermark, change log, or revision delta) that provably handles inserts, edits, retractions/deletions, and idempotent replays.
- **Bounded Background Drain**: Background worker autonomously drains 500 queued jobs with a 30-second observation window, proving autonomous progress without deadlocks or write-path stalls. Full drain within 30 seconds is a diagnostic target, not the G1 ACK acceptance gate.
- **Crash Durability**: If the server process is killed (`kill -9`) mid-drain, unprojected jobs remain durable in `projection_job` with lease expiry recovery, resuming upon restart with zero database corruption.

### Inputs and Dependencies
- Pinned candidate: Current repository HEAD + clean virtual environment (.venv Python 3.11).
- Key files: `src/trace_lite/api/app.py`, `src/trace_lite/filing/workers.py`, `src/trace_lite/source/project.py`, `src/trace_lite/source/coverage_state.py`, `src/trace_lite/router/hybrid.py`, `src/trace_lite/router/vector_store.py`.
- Regression test targets: `tests/test_write_path_integration.py`, `tests/test_lanea_write_cost.py`, `tests/test_s6_envelope.py`, `tests/test_s4_vectors.py`.

### Scope and Interfaces
- Owned Files (MODIFICATIONS PERMITTED ONLY HERE):
  - `src/trace_lite/api/app.py`
  - `src/trace_lite/filing/workers.py`
  - `src/trace_lite/source/project.py`
  - `src/trace_lite/source/coverage_state.py`
  - `src/trace_lite/router/hybrid.py`
  - `src/trace_lite/router/vector_store.py`
  - `src/trace_lite/store/generations.py`
  - `src/trace_lite/store/database.py`
  - `tests/test_write_path_integration.py`
  - `tests/test_lanea_write_cost.py`
  - `tests/test_s4_vectors.py`
  - `tests/test_served_delta_regression.py`
- Forbidden Surfaces (DO NOT TOUCH):
  - `src/trace_lite/cordis/*`
  - `src/trace_lite/obsidian/*`
  - `benchmarks/run_integrated_qualification.py`
  - `docs/*` (No new state journals or markdown ledgers)
- Non-Goals:
  - Do NOT modify ranking formulas or external vector encoders.
  - Do NOT rewrite SQLite connection management.
  - Do NOT remove regression assertions; update only expectations that demand synchronous inline drain.

### Required Implementation Rules
1. **Strip Synchronous Drain from Mutation Handlers**:
   - In `src/trace_lite/api/app.py`: Remove all synchronous calls to `projection.drain_outbox` in `ingest_note`, `remove_note`, `sync_notes`, and `_ingest_replay`.
   - On note edits (`supersedes is not None`), replace full `_repair_facet_state(...)` scans with incremental member fold-out or worker-delegated re-centering.
   - Separate **Visibility Invalidation** from **Semantic Publication**: On note edit/retract, immediately update live atom visibility and increment the reader visibility generation so stale content is immediately invisible to queries. Advance semantic vector generation when background worker batches complete.
2. **Unify Worker & Persistent Serving Lifecycles**:
   - In `create_app` lifespan (`app.py`), ensure `MaintenanceWorker` is started whenever `TRACE_LITE_SERVED_PERSISTENT=1` is active, continuously draining outbox jobs in batches (`DRAIN_LIMIT = 64`).
3. **Eliminate Vector Detachment During Maintenance**:
   - In `src/trace_lite/api/app.py` (`_ensure_served_persistent_view`): Delete lines detaching the vector branch when `rt.maintenance_running()` is true or when `rt.lock` is busy.
   - In `ServedRuntime`: Background catchup builds new snapshots in memory and executes an atomic pointer swap (`self.served = new_snap`). Query paths must read the active snapshot pointer under a momentary lock (< 1ms).
4. **Lifecycle-Correct Incremental Delta Warming**:
   - In `hybrid.py` (or reader cache), implement incremental delta loading that eliminates whole-table scans of `atom` on generation swap. Ensure correct handling of:
     - New atom inserts.
     - Note edits (new atom replaces old atom).
     - Note retractions/deletions (old atom removed from index/tombstoned).
     - Idempotent replays (no duplicated or orphaned atoms).
5. **Update Test Expectations**:
   - In `tests/test_write_path_integration.py`: Update tests asserting synchronous inline completion to assert successful outbox job queuing (`pending: true`), then wait for background worker convergence.
   - Add unit test `test_decoupled_ingest_fast_ack_under_backlog` confirming P95 ingest latency < 35ms under 500 queued jobs.

### Acceptance Matrix

| Criterion | Observable Outcome | Command / Check | Pass Threshold |
|---|---|---|---|
| Ingest ACK Latency | Sub-35ms write acknowledgment under heavy backlog | `.venv/bin/pytest tests/test_write_path_integration.py -k test_decoupled_ingest_fast_ack_under_backlog` | P95 latency ≤ 35ms with 500 queued jobs |
| Non-blocking Served Vectors | Zero vector detachments during background catch-up | Run 200 concurrent queries while worker drains 500 jobs | 0 vector detachments (`router.vector_detached == False`); queries return vector candidates |
| Retraction Freshness | Immediate lexical & visibility invalidation | Retract note; immediate `POST /api/query` | Retracted note excluded immediately (0 stale citations) |
| Incremental Correctness | Delta warm correctly reflects all lifecycle events | Unit tests in `tests/test_served_delta_regression.py` | Verified pass on insert, edit, delete, replay |
| Bounded Background Drain | Worker autonomously clears queue within deadline | Ingest 500 notes; monitor queue drain | Positive completed-job progress within 30 seconds; durable remainder reported; full-drain time diagnostic |
| Regression Suite | Core test suite remains green | `.venv/bin/pytest tests/test_write_path_integration.py tests/test_lanea_write_cost.py tests/test_s6_envelope.py tests/test_s4_vectors.py` | All pass, exit 0 |
| Crash Recovery | Survives server kill -9 | Kill server mid-drain; restart; verify outbox | Unprojected jobs resume and complete; `PRAGMA integrity_check` ok |

### Escalation & Stop Rules
- HALT immediately if SQLite database locking (`sqlite3.OperationalError: database is locked`) occurs between HTTP writes and the background worker.
- HALT immediately if any test in `tests/test_s1_blackbox.py` or `tests/test_s6_envelope.py` fails due to unhandled concurrency races.
```

---

## GATE 2: R09 10k Scale Integrated Qualification & Capability Check

```markdown
# PACKET-G2-R09-10K — R09 Integrated Qualification at 10,000 Notes

Status: READY AFTER PREREQUISITE VERIFICATION
Kind: qualification / evaluation
Contract revision: TL-QN-2026-09-13.3 / PACKET-G2
Owner: Builder Agent

### Outcome
Qualify the integrated Query-Native Memory Program on the 10,000-document rung across all applicable criteria in `benchmarks/run_integrated_qualification.py`, eliminating evaluation denominator artifacts, proving capability on realistic multi-span documents, and validating retrieval quality, concurrency, and publication budgets.

- **Capability vs Capacity (Realistic Multi-Span Evaluation)**: Index the full SciFact corpus (5,183 multi-sentence realistic scientific documents) alongside synthetic notes to total 10,000 documents. This tests realistic document handling (multi-span extraction, citation fidelity, relation compilation) within the 10k budget, complementing synthetic scaling.
- **Consistent Quality Acceptance Rule**: Evaluate SciFact retrieval quality against a frozen fixed floor: nDCG@10 ≥ 0.6400 (target ≥ 0.6500) on the full SciFact corpus, eliminating denominator truncation artifacts where the ranker was penalized for unindexed documents.
- **NFCorpus Diagnostic Characterization**: Evaluate NFCorpus and record subset-characterized nDCG in accordance with `evidence/query-native/R09/nfcorpus_constraint.md` (confirming diagnostic fallback behavior of the 128d hashbow encoder without failing the hard gate).
- **Server Lifecycle & Bounded Drain Budget**: Child server launches with `TRACE_LITE_SERVED_PERSISTENT=1` and `TRACE_LITE_WORKER=1`. Background worker drains the 10k publication queue within an explicit deadline of ≤ 600.0s (average drain rate ≥ 16.7 jobs/s).
- **Concurrent Serving Under Active Drain**: Under active 10k drain, concurrent queries achieve P50 ≤ 10ms, P95 ≤ 35ms, with zero vector branch detachments and peak RSS ≤ 300 MB.

### Inputs and Dependencies
- Required prior packet: PACKET-G1-DECOUPLE-SERVE verified PASS.
- Baseline contract: `docs/builds/query-native/packets/R09-qualification.md`.
- Historical references: `evidence/query-native/R00/gates.json`, `evidence/query-native/R09/nfcorpus_constraint.md`.

### Scope and Interfaces
- Owned Files (MODIFICATIONS PERMITTED ONLY HERE):
  - `benchmarks/run_integrated_qualification.py`
  - `tests/test_qualification_gates.py`
  - `src/trace_lite/router/cascade.py`
  - `evidence/query-native/R09/` (generated artifacts)
- Forbidden Surfaces (DO NOT TOUCH):
  - `src/trace_lite/store/*`
  - `src/trace_lite/cordis/*`
  - `docs/*`
- Non-Goals:
  - Do NOT modify ground-truth `qrels/test.tsv` or query files.
  - Do NOT lower the frozen SciFact floor below 0.6400.
  - Do NOT attempt 100k or 250k in this packet.

### Required Implementation Rules
1. **Index Full SciFact on 10k Rung**:
   - In `run_integrated_qualification.py`, ensure that on the 10k scale rung, all 5,183 SciFact documents are indexed into the vault alongside synthetic notes. This completely eliminates denominator truncation artifacts without artificial denominator manipulation.
   - Save detailed per-query rankings in `evidence/query-native/R09/per_query_rankings.jsonl`.
2. **Fixed Quality Acceptance Evaluation**:
   - In `score_beir`, assert `scifact_ndcg >= 0.6400`. If comparing against a baseline, ensure both baseline and candidate run on the exact same indexed 10k dataset and query set.
   - For NFCorpus, report diagnostic subset nDCG against the historical 0.1900 reference without using it as a hard release gate, documenting consistency with `nfcorpus_constraint.md`.
3. **Child Server Process Launch**:
   - In `start_server(...)`, explicitly pass `TRACE_LITE_SERVED_PERSISTENT=1` and `TRACE_LITE_WORKER=1` in the child environment so the child process executes the persistent serving engine and autonomous worker.
4. **Autonomous Drain Monitoring with Explicit Deadline**:
   - In `served_drain(...)`, monitor autonomous queue drainage via `/api/status` until completion, asserting that the queue reaches 0 within ≤ 600.0s (average drain rate ≥ 16.7 jobs/s).
5. **Accurate Dossier Labelling**:
   - Preserve historical refusal records. Append the new run identity and its actual PASS, FAIL, or INCOMPLETE verdict; never pre-label it verified.

### Acceptance Matrix

| Criterion | Observable Outcome | Command / Check | Pass Threshold |
|---|---|---|---|
| Harness Gate Tests | Gate calculation unit tests pass | `.venv/bin/pytest tests/test_qualification_gates.py` | Exit code 0, all passed |
| SciFact Retrieval Quality | Retrieval clears frozen quality floor | In 10k qualification report | nDCG@10 ≥ 0.6400 (target ≥ 0.6500) |
| NFCorpus Retrieval Quality | Diagnostic quality characterized | In 10k qualification report | Report subset, query coverage, and nDCG; diagnostic only |
| Full 10k Lifecycle | Full server lifecycle execution passes | `.venv/bin/python benchmarks/run_integrated_qualification.py --scale 10k --lifecycle-full --output evidence/query-native/R09/` | Exit code 0, all criteria PASS |
| Bounded Publication Drain | 10k backlog drains within deadline | In 10k qualification report | Total drain time ≤ 600.0s (≥ 16.7 jobs/s) |
| Peak Memory RSS | Process memory bounded | Monitored peak RSS | Peak RSS ≤ 300 MB |
| Vector Serving Stability | Vector route active throughout | Telemetry in report | 0 vector branch detachments |

### Escalation & Stop Rules
- HALT immediately if SciFact nDCG@10 < 0.6400 on the full SciFact corpus.
- HALT immediately if memory leaks cause server RSS to exceed 400 MB during the 10k run.
```

---

## GATE 3: 50k & 100k Scale Ladder Qualification

```markdown
# PACKET-G3-SCALE-100K — 50k and 100k Scale Ladder Qualification

Status: READY AFTER PREREQUISITE VERIFICATION
Kind: scale / qualification
Contract revision: TL-QN-2026-09-13.3 / PACKET-G3
Owner: Builder Agent

### Outcome
Advance the integrated Query-Native Memory Program through 50,000 and 100,000 documents/atoms under sustained concurrent query load and background maintenance, verifying that persistent vector serving (`ServedVectors`) and SQLite WAL maintain P95 latency ≤ 25ms and RSS ≤ 350 MB without table scans or memory thrashing.

- **Entity Model**: 1 Document = 1 Note = 1 Atom (in synthetic ladder rungs). 100,000 documents = 100,000 float32 128-d vectors (51.2 MB raw vector data).
- **Eliminate 20k Atom Ceiling**: Remove the hardcoded `SERVED_VECTOR_MAX_ATOMS = 20000` limit in `cascade.py`, ensuring persistent vector serving operates smoothly at 100k atoms without detachment.
- **Performance-Gated Tuning (Optimize Only Demonstrated Failures)**:
  - If member row instantiation in `holon.py` causes GC thrashing on large holons, replace full member loading with SQL-level indexed existence checks (`SELECT 1 FROM holon_members WHERE holon_id = ? AND atom_id = ?`).
  - If KMeans clustering in `index_adapter.py` creates a CPU bottleneck, vectorize centroid assignments with $k = \min(256, \text{round}(\sqrt{N}))$.
  - Permit existing implementations to stand if they satisfy the acceptance thresholds without modification.
- **Index Coverage Audit**: Ensure all join paths on `holon_members`, `atom`, `source_revision`, and `projection_job` hit covering compound indexes.

### Inputs and Dependencies
- Required prior packets: PACKET-G1 and PACKET-G2 verified PASS.
- Key modules: `src/trace_lite/router/cascade.py`, `src/trace_lite/router/vector_store.py`, `src/trace_lite/router/index_adapter.py`, `src/trace_lite/filing/holon.py`, `src/trace_lite/store/schema.sql`.

### Scope and Interfaces
- Owned Files (MODIFICATIONS PERMITTED ONLY HERE):
  - `benchmarks/run_integrated_qualification.py`
  - `src/trace_lite/router/cascade.py`
  - `src/trace_lite/router/vector_store.py`
  - `src/trace_lite/router/hybrid.py`
  - `src/trace_lite/router/index_adapter.py`
  - `src/trace_lite/filing/holon.py`
  - `src/trace_lite/store/schema.sql` (compound index additions if required)
  - `src/trace_lite/store/migrate.py`
- Forbidden Surfaces (DO NOT TOUCH):
  - `src/trace_lite/cordis/*`
  - `src/trace_lite/obsidian/*`
  - `docs/*`
- Non-Goals:
  - Do NOT implement multi-node distributed clustering. Single-node process only.
  - Do NOT train external neural embeddings.
  - Do NOT attempt 250k in this packet (reserved for Gate 4).

### Required Implementation Rules
1. **Remove Atom Count Cap in Cascade**:
   - In `src/trace_lite/router/cascade.py`: Prove the persistent path is not subject to the legacy 20k cap; change the guard only if it blocks the active path. Preserve legacy safety guards when still needed. Ensure `CascadeRouter` routes through `ServedVectors` (S4 persistent index) which scales past 100k without branch detachment.
2. **Prevent Query-Path Atom Table Scans**:
   - In `run_integrated_qualification.py` (`served_stack`): Ensure query readers attach to published persistent views without executing `SELECT id, text FROM atom`.
3. **Scale Ingestion & Drain Budgets**:
   - Maintain sustained bulk ingest rate ≥ 500 docs/s.
   - Maintain sustained background worker drain rate ≥ 40 jobs/s.

### Acceptance Matrix

| Criterion | Observable Outcome | Command / Check | Pass Threshold |
|---|---|---|---|
| Ingest Throughput | Sustained bulk ingest rate | `run_integrated_qualification.py --scale 50k` then `--scale 100k` | ≥ 500 docs/s sustained |
| Background Drain Rate | Continuous background drain | Measured worker throughput via `/api/status` | ≥ 40 jobs/s |
| Query Latency Under Backlog | Low retrieval latency while draining | 800 queries over 180s (4 clients) during 100k run | P50 ≤ 5.0ms, P95 ≤ 25.0ms, P99 ≤ 75.0ms |
| Memory Containment | Strict RSS limit | Telemetry `VmHWM` / Peak RSS | Peak RSS ≤ 350 MB at 100k scale |
| Vector Serving Availability | Continuous vector search | Query telemetry in report | 0 vector branch detachments |
| Database Consistency | SQLite storage integrity | `PRAGMA integrity_check` | 0 errors ("ok") |

### Escalation & Stop Rules
- HALT immediately if process RSS exceeds 400 MB at 100k scale.
- HALT immediately if query P95 latency exceeds 50ms.
- HALT immediately if SQLite WAL file grows beyond 1.0 GB (checkpoint starvation).
```

---

## GATE 4: Definitive 250,000-Scale Release Envelope Dossier

```markdown
# PACKET-G4-QUAL-250K — Definitive 250,000-Scale Release Envelope Dossier

Status: READY AFTER PREREQUISITE VERIFICATION
Kind: qualification / release envelope
Contract revision: TL-QN-2026-09-13.3 / PACKET-G4
Owner: Builder Agent

### Outcome
Achieve definitive qualification of the Query-Native Memory Program at 250,000 documents/atoms on persistent native ext4 storage, fulfilling master mandate criteria C9-1 (Unified config), C9-2 (Lifecycle integrity), C9-3 (250k scale P95 ≤ 35ms, RSS ≤ 450MB), and C9-5 (Cryptographic receipt), with C9-4 (1M scale qualification) formally recorded as DEFERRED to a future phase.

- **Entity & Memory Model**: 250,000 documents = 250,000 atoms = 250,000 float32 128-d vectors (128.0 MB raw vector payload). Total process RSS ≤ 450 MB. Overlapping immutable snapshots (base + delta) are explicitly permitted to utilize separate arrays.
- **Filesystem Verification**: The benchmark database and served index MUST reside on a native ext4 filesystem (e.g. within `$HOME/.trace_bench/` or verified ext4 mount). The harness must programmatically verify the filesystem type (rejecting execution on Windows NTFS mounts like `/mnt/c/` or `/tmp` tmpfs).
- **Bounded Concurrent-Update Serving Window**: Query latency must be measured *during active updates*, not solely on a settled corpus. During query evaluation at 250k, run a concurrent mutation stream (100+ note updates pushed at a steady rate) alongside active background worker drain, verifying that query P95 remains ≤ 35ms and mutation ACK P95 remains ≤ 50ms under write pressure.
- **Bounded Ingest & Drain Budget**: Base 250k corpus is prepared through a verified bulk import/backfill path that establishes source revisions, spans, lexical state, required graph/holon projections, and persistent vectors. Discover actual supported APIs; do not assume a ServedVectors.build method exists. The active mutation backlog (1,000 queued jobs) must drain within a bounded window of ≤ 120.0s (avoiding unbudgeted 2-hour full-corpus redrains).
- **Cryptographic Candidate Binding**: The qualification dossier must cryptographically bind the candidate:
  - Git commit SHA and `git diff` SHA-256 (or clean status).
  - SHA-256 digests of `benchmarks/run_integrated_qualification.py` and `src/trace_lite/store/schema.sql`.
  - SHA-256 digests of all executed Python modules and any untracked source/test files.
  - Platform specifications (CPU, RAM, OS, Python version).
- **Citation Exactness**: Verify 100% byte-exact citation integrity in compiled evidence dossiers against raw source note bytes.

### Inputs and Dependencies
- Required prior packets: PACKET-G1, PACKET-G2, and PACKET-G3 verified PASS.
- Master qualification specification: `docs/builds/query-native/packets/R09-qualification.md`.
- Target criteria: C9-1 (Unified config), C9-2 (Lifecycle integrity), C9-3 (250k scale ladder), C9-4 (1M scale: recorded as DEFERRED), C9-5 (Cryptographic receipt).

### Scope and Interfaces
- Owned Files (MODIFICATIONS PERMITTED ONLY HERE):
  - `benchmarks/run_integrated_qualification.py`
  - `src/trace_lite/router/vector_store.py`
  - `src/trace_lite/store/database.py`
  - `src/trace_lite/store/governor.py`
  - `evidence/query-native/R09/` (generated qualification artifacts)
- Forbidden Surfaces (DO NOT TOUCH):
  - Core HTTP API schemas and endpoints.
  - `docs/*`
- Non-Goals:
  - Do NOT attempt 1,000,000 notes in this packet (1M is explicitly deferred until 250k is certified).

### Required Implementation Rules
1. **Filesystem Validation**:
   - In `run_integrated_qualification.py`, verify that the target benchmark directory resides on a native Linux ext4 filesystem (via resolved mount metadata, such as findmnt -T or /proc/self/mountinfo; os.statvfs alone does not identify filesystem type). Halt if running on Windows drvfs/NTFS or tmpfs.
2. **Governed WAL Checkpointing**:
   - Measure WAL growth and checkpoint progress. Use the existing governor or bounded checkpoints when needed; document busy readers. A checkpoint call does not guarantee truncation. Stop the run at the recorded disk budget; do not add checkpoint machinery if the existing behavior meets it.
3. **Full Lifecycle Qualification Battery with Concurrent Updates**:
   - Execute the complete battery:
     1. Bulk ingest of 250,000 notes on ext4 storage.
     2. Complete and verify required semantic projections and persistent vector base coverage, including any remaining source-publication jobs. An ANN-only build cannot substitute for this step.
     3. Active mutation stream (100+ note updates) + concurrent query load (200 sequential + 100 concurrent queries across 4 threads) while the background worker is draining.
     4. SIGKILL crash recovery test (kill during active serve, restart, verify 0 corruption).
     5. Dossier compilation with byte-exact citation verification.
4. **Cryptographically Bound Receipt**:
   - Generate `evidence/query-native/R09/qualification_dossier.json` and `scale_ladder.json` containing:
     - Git commit hash and `git diff` SHA-256.
     - SHA-256 digests of the harness script, schemas, executed modules, and untracked code files.
     - Platform specifications and memory/latency distributions.
     - Compute C9-1, C9-2, C9-3, and C9-5 independently from evidence; emit PASS only for satisfied criteria, otherwise FAIL or INCOMPLETE. C9-4 remains DEFERRED; report a ceiling only if actually measured.

### Acceptance Matrix

| Criterion | Observable Outcome | Command / Check | Pass Threshold |
|---|---|---|---|
| Master Qualification Run | End-to-end 250k ladder execution | `.venv/bin/python benchmarks/run_integrated_qualification.py --scale 250k --lifecycle-full --output evidence/query-native/R09/` | Exit code 0; C9-1, C9-2, C9-3, C9-5 PASS; C9-4 DEFERRED |
| Scale Latency Under Updates (C9-3) | Retrieval P95 latency during concurrent writes | In `scale_ladder.json` under 250k | P95 ≤ 35.0 ms |
| Mutation Ingest Under Load | Fast write ACK during query traffic | In `scale_ladder.json` under 250k | P95 ≤ 50.0 ms |
| Scale Memory Gate (C9-3) | Process peak memory | In `scale_ladder.json` under 250k | Peak RSS ≤ 450.0 MB |
| Ingestion Rate | Sustained bulk write performance | In `scale_ladder.json` under 250k | Ingestion ≥ 500 docs/s |
| Recovery Gate (C9-2) | Crash recovery after kill -9 | Automated crash probe in harness | 0 corruption; restart successful |
| Citation Exactness | Dossier citations verified | Automated citation check | 100% byte-exact match against cited immutable revision |
| Filesystem Gate | Native Linux filesystem check | Automated environment check | ext4 verified |
| Candidate Binding (C9-5) | Cryptographic candidate receipt | `qualification_dossier.json` | Valid Git SHA + code hash manifest |

### Escalation & Stop Rules
- HALT immediately if process RSS exceeds 450.0 MB at any point during 250k execution.
- HALT immediately if query P95 latency exceeds 35.0 ms during concurrent updates.
- HALT if any citation in generated evidence dossiers fails byte-exact verification against the source note.
```
