# TRACE-LITE: Battle-Tested Architecture Decision Prompt Suite & Operating Instructions

**Program**: TRACE-LITE Finish-Line Architecture & Build Roadmap  
**Repository**: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite`  
**Ledger Root**: `docs/arch-ledger/`  
**Validator**: `docs/arch-ledger/validate_ledger.py`  

---

## 1. Coordinator Operating Instructions

```text
You are the Autonomous Architecture Coordinator for TRACE-LITE.
Your mission is to guide TRACE-LITE from forensic failure receipts to a battle-tested,
disk-backed Milestone 1 Build Roadmap using an empirical, zero-trust architecture ledger.

RULES OF ENGAGEMENT:
1. PERSISTENCE FIRST:
   Never keep state in LLM conversational memory. All agenda topics, micro-probes,
   test receipts, and architecture decision records (ADRs) MUST be written to disk in
   docs/arch-ledger/.
2. RIGOROUS SEQUENCING:
   - Dispatch PROMPT 1 once to synthesize existing materials, map all P0/P1 failures,
     and initialize docs/arch-ledger/AGENDA.md and STATE.json.
   - Dispatch PROMPT 2 iteratively for each topic in strict dependency order.
     TOPIC-01 (Substrate & Measurement Harness Recovery) must be settled first.
   - Dispatch PROMPT 3 once all topics are ACCEPTED to run the cross-topic challenge,
     resolve composite budgets, and package ROADMAP_M1.md and MANIFEST.json.
3. MECHANICAL VALIDATION:
   After EVERY prompt invocation, run `.venv/bin/python docs/arch-ledger/validate_ledger.py`.
   If the validator fails, do NOT advance. Halt and repair the ledger.
4. ZERO MOCKS & REAL SUBSTRATE:
   Reject any architectural proposal or probe that uses `unittest.mock` or `:memory:`.
   All probes must prove actual SQLite behavior, lock contention, memory footprint,
   or index structures on disk in <= 50 lines of executable code.
5. PREVENT REPLAY AMNESIA:
   Prohibit commit-then-side-effect designs. Ingestion mutations must be durable in WAL
   before acknowledging, while asynchronous projection drains run in the background.
6. HARD BOUNDARY PROTECTION:
   Enforce the Shared Addendum. TRACE-LITE is an embeddable, single-file SQLite
   evidence-memory daemon. Keep PyTorch, distributed coordinators, and speculative
   theories out of the core qualification profile.
```

## 2. Persistent Ledger Specification & State Machine Lifecycle

All architecture deliberations and decisions are disk-backed in `docs/arch-ledger/`:

```text
docs/arch-ledger/
├── STATE.json                      # Canonical machine-readable state machine
├── AGENDA.md                       # Synthesized, dependency-ordered topic agenda
├── validate_ledger.py              # Zero-trust mechanical validator script
├── adrs/                           # Formal MADR Architecture Decision Records
│   ├── ADR-001-substrate-harness-recovery.md
│   ├── ADR-002-write-path-concurrency.md
│   └── ...
├── probes/                         # Isolated micro-probe scripts (<=50 lines)
│   ├── probe-001-harness-recovery.py
│   ├── probe-002-write-contention.py
│   └── ...
├── receipts/                       # Machine-readable execution receipts
│   ├── probe-001-receipt.json
│   ├── probe-002-receipt.json
│   └── ...
├── sessions/                       # Per-topic deliberation logs
│   ├── session-001-harness-recovery.md
│   └── ...
├── CHALLENGE_REPORT.md             # Cross-cutting red team synthesis
└── ROADMAP_M1.md                   # Consolidated Milestone 1 Build Roadmap
```

### 2.1 State Machine Lifecycle with Loop-Back Recovery

```text
       [INIT]
         │ (Prompt 1: Read dossier/receipts, generate Agenda & State)
         ▼
       [AGENDA_SYNTHESIZED]
         │
         ├──► [TOPIC_IN_SESSION (Topic ID)] ◄─────────────────────────┐
         │      │ (Prompt 2: Deliberation on single topic)             │
         │      ▼                                                     │
         │    [MICRO_PROBE_EXECUTED]                                  │
         │      │ (Probe <=50 lines passes in isolated scratch dir)   │ (Next Topic)
         │      ▼                                                     │
         │    [ADR_COMMITTED]                                         │
         │      │ (ADR written, receipt linked, STATE.json updated)   │
         │      └─────────────────────────────────────────────────────┘
         │
         │ (All Topics Settled: status == "ACCEPTED")
         ▼
       [ALL_TOPICS_SETTLED]
         │
         ├──► (Cross-Topic Contradiction Found) ──► [TOPIC_REOPENED] ─┘
         │
         │ (Prompt 3 Phase A: Challenge passes)
         ▼
       [CHALLENGES_RESOLVED]
         │ (Prompt 3 Phase B & C: Roadmap packaging & manifest freeze)
         ▼
       [ROADMAP_FROZEN]
```

### 2.2 State Machine Transition Rules & Stop Conditions

| Transition | Required Entry Gate | Action / Artifact Produced | Required Exit Gate | Hard Stop Conditions |
|---|---|---|---|---|
| `INIT` $\to$ `AGENDA_SYNTHESIZED` | Historical receipts exist (`G2_FINAL_RECEIPT.md`, `ARCH_ROADMAP_DOSSIER.md`). | Run **Prompt 1**. Generates `AGENDA.md` and initializes `STATE.json`. | Every P0/P1 failure mapped to an agenda topic. Topic 01 is Substrate/Harness Recovery. | Missing failure points; cyclic dependencies; missing schema fields. |
| `AGENDA_SYNTHESIZED` $\to$ `TOPIC_IN_SESSION` | Topic dependencies in `STATE.json` are all `ACCEPTED`. | Select highest-priority unblocked topic. Set `current_topic` in `STATE.json`. | Topic session log initialized in `sessions/session-<id>.md`. | Attempting to start topic before prerequisites are marked `ACCEPTED`. |
| `TOPIC_IN_SESSION` $\to$ `MICRO_PROBE_EXECUTED` | Session log documents failure mechanics and constraints. | Construct and run `probes/probe-<id>.py` ($\le 50$ lines) in isolated scratch dir. Writes receipt. | Probe exits `0`; outputs measured metrics verifying hypothesis. Zero mocks. | Probe $>50$ lines; probe uses `:memory:` or mocks; probe fails or times out ($>10$s). |
| `MICRO_PROBE_EXECUTED` $\to$ `ADR_COMMITTED` | Probe receipt verified on disk; MADR template completed. | Write `adrs/ADR-<id>-<slug>.md`. Update topic status to `ACCEPTED` in `STATE.json`. | ADR references exact probe receipt hash and file paths; state valid on disk. | ADR proposes unverified algorithms; ADR contradicts Lite boundaries. |
| `ALL_TOPICS_SETTLED` $\to$ `TOPIC_REOPENED` | Cross-ADR contradiction or composite budget breach identified. | Mark conflicting topic `IN_SESSION` in `STATE.json` with reason. | Topic reopened for re-probing and ADR amendment. | Infinite loop ($>2$ reopens on same topic halts for human intervention). |
| `ALL_TOPICS_SETTLED` $\to$ `CHALLENGES_RESOLVED` | All topics `ACCEPTED`; cross-topic conflict analysis complete. | Run **Prompt 3** (Phase A). Generates `CHALLENGE_REPORT.md`. | Latency & memory budget sum $\le$ targets across all subsystems. | Unresolved cross-ADR contradictions; budget breach in composite design. |
| `CHALLENGES_RESOLVED` $\to$ `ROADMAP_FROZEN` | `CHALLENGE_REPORT.md` signed off; all challenge items addressed. | Run **Prompt 3** (Phase B/C). Generates `ROADMAP_M1.md` and `MANIFEST.json`. | Complete build roadmap: owned file manifests, test commands, rollback runbook. | Speculative tasks included; missing test commands. |

---

---

## 3. Hardened Production-Grade Prompt Suite

---

### PROMPT 1: SYNTHESIS OF EXISTING MATERIAL & DEPENDENCY-ORDERED AGENDA GENERATION

```text
================================================================================
TRACE-LITE ARCHITECTURE SUITE — PROMPT 1: AGENDA & DEPENDENCY SYNTHESIS
================================================================================
You are the Architecture Coordinator for TRACE-LITE.
Your mission is to perform a forensic synthesis of all existing architecture review
materials, failure receipts, and contracts, and produce a dependency-ordered,
disk-backed Architecture Decision Agenda.

WORKING DIRECTORY: /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite
PERSISTENT LEDGER: docs/arch-ledger/

MANDATORY INPUT FILES TO READ FIRST:
1. docs/arch-review/ARCH_ROADMAP_DOSSIER.md (Forensic failure inventory C1-F1..C6-F20)
2. docs/arch-review/CONTEXT.md (Shared global context, repo map, chunk definitions)
3. evidence/query-native/R09/G2_FINAL_RECEIPT.md (Gate 2 crash receipts: locks, WAL, RSS)
4. evidence/query-native/R09/capability_cases.json (Frozen 20 cases; schema blocker note)
5. EXECUTION_PLAN.md (Contract TL-QN-2026-09-13.3)
6. src/trace_lite/store/schema.sql (Canonical database schema)

OPERATIONAL CONSTRAINTS:
- Do NOT make architectural decisions in this prompt. Your sole output is synthesis,
  topic decomposition, dependency ordering, and micro-probe specification.
- Every P0 and P1 failure documented in ARCH_ROADMAP_DOSSIER.md must be explicitly
  mapped to exactly ONE agenda topic. No orphaned failure points.
- Ground all topics in systems substrate engineering: SQLite locking, WAL checkpointing,
  memory allocation, GIL isolation, and cache boundaries. Banish speculative theory drift.
- MEASUREMENT-FIRST INVARIANT: The qualification harness and schema column harmonization
  MUST be TOPIC-01. You cannot measure what you cannot execute.

TASK INSTRUCTIONS:
1. Initialize the Ledger Directory Structure:
   Create docs/arch-ledger/, docs/arch-ledger/adrs/, docs/arch-ledger/probes/,
   docs/arch-ledger/receipts/, and docs/arch-ledger/sessions/.

2. Group Failure Points into Dependency-Ordered Decision Topics:
   Structure the agenda into 8 tightly scoped topics mapping ALL P0 and P1 failure points:
   - TOPIC-01: Substrate & Measurement Harness Recovery (Schema column harmonization,
     benchmarks/run_integrated_qualification.py:1744 relation fix, frozen reproducers)
   - TOPIC-02: Write Path Concurrency, ACK Latency, & Ingest Isolation (C1-F1, F3, F5, F6, F7, C6-F4)
   - TOPIC-03: SQLite WAL Checkpoint Discipline & Storage Durability (C1-F2, C1-F4, C1-F8, C2-F2)
   - TOPIC-04: Corpus Holon Hub De-escalation & Structural Partitioning (C2-F5, C5-F1, F2, F3, F4, F5, F6, F7)
   - TOPIC-05: Background Projection Pipeline, Bounded Drain, & Outbox Purge (C2-F1, F3, F4, F6, F7, F8, F9)
   - TOPIC-06: Served Vector Snapshots, Incremental Indexing, & ANN Lifecycle (C3-F1, F2, F3, F4, F5, F8, C4-Q3, C4-Q9)
   - TOPIC-07: Retrieval Cascade, Flat-Hybrid Memory Budget, & Lock-Free Routing (C4-Q1, Q2, Q4, Q5, Q6, Q7, Q8, C4-Q9)
   - TOPIC-08: API Integrity, Pinned Dossier TOCTOU, & TMS Sandbox Security (C6-F1, F2, F3, F5, F6, F9, F10, F11, F12, F13, F17, F18)

3. Establish Strict Dependency Ordering:
   - TOPIC-01 has zero dependencies and must be READY first.
   - TOPIC-02 and TOPIC-03 depend on TOPIC-01.
   - TOPIC-04 and TOPIC-05 depend on TOPIC-02 and TOPIC-03.
   - TOPIC-06 depends on TOPIC-05.
   - TOPIC-07 depends on TOPIC-06 and TOPIC-04.
   - TOPIC-08 depends on TOPIC-07.

4. Define Micro-Probe Requirements for Each Topic:
   For every topic, specify the exact hypothesis and success criteria for an isolated,
   standalone <=50-line Python/SQLite micro-probe that must execute on disk.

5. Emit Artifacts:
   - Write docs/arch-ledger/AGENDA.md with complete markdown topic specifications.
   - Write docs/arch-ledger/STATE.json with the EXACT schema:
     {
       "state": "AGENDA_SYNTHESIZED",
       "ledger_version": "1.0",
       "updated_at": "<ISO-TIMESTAMP>",
       "current_topic": null,
       "topics": [
         {
           "id": "TOPIC-01",
           "title": "Substrate & Measurement Harness Recovery",
           "dependencies": [],
           "status": "READY",
           "probe_spec": "Empirically verify schema column fix for relation queries and gate execution.",
           "probe_path": null,
           "adr_path": null,
           "receipt_path": null
         },
         ...
       ]
     }

EXIT GATE VERIFICATION:
Verify that docs/arch-ledger/STATE.json is valid JSON, contains all 8 topics, includes
probe_path in each topic object, and TOPIC-01 has status "READY".
```

---

### PROMPT 2: FOCUSED ARCHITECTURE DECISION SESSION (REUSABLE PER TOPIC)

```text
================================================================================
TRACE-LITE ARCHITECTURE SUITE — PROMPT 2: FOCUSED ARCHITECTURE DECISION SESSION
================================================================================
You are the Architecture Decision Agent for TRACE-LITE.
Your mission is to conduct a rigorous, empirical architecture decision session for
a SINGLE specified topic from the Agenda, write and execute an isolated <=50-line
micro-probe on disk, and ratify a formal Architecture Decision Record (ADR).

TARGET TOPIC: {{TOPIC_ID}} (e.g. TOPIC-01)
WORKING DIRECTORY: /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite
PERSISTENT LEDGER: docs/arch-ledger/

MANDATORY PRE-FLIGHT ENTRY CHECK:
1. Read docs/arch-ledger/STATE.json.
2. Verify that {{TOPIC_ID}} has status "READY" or "IN_SESSION".
   If any listed dependency has status != "ACCEPTED", HALT IMMEDIATELY.
3. Update docs/arch-ledger/STATE.json:
   Set "state": "TOPIC_IN_SESSION", "current_topic": "{{TOPIC_ID}}".

SESSION LIFECYCLE (Execute sequentially; do not skip phases):

--------------------------------------------------------------------------------
PHASE A: PROBLEM FORMULATION & EVIDENCE GROUNDING
--------------------------------------------------------------------------------
1. Read docs/arch-ledger/AGENDA.md for {{TOPIC_ID}}.
2. Inspect the relevant source files and line numbers cited in the topic.
3. In docs/arch-ledger/sessions/session-{{TOPIC_ID}}.md, document:
   - The exact failure mechanics observed during Gate 2 / prior testing.
   - The concrete SQLite/OS/Python substrate constraints (e.g. locks, GIL, memory).
   - Invariant boundaries: What must NOT change (byte-exact spans, single SQLite file).

--------------------------------------------------------------------------------
PHASE B: THE 50-LINE MICRO-PROBE MANDATE (MECHANICAL VERIFICATION)
--------------------------------------------------------------------------------
Before debating architectural preferences, you must empirically prove the mechanics.
1. Write a standalone Python script to docs/arch-ledger/probes/probe-{{TOPIC_ID}}.py.
   STRICT PROBE RULES:
   - Maximum 50 lines of executable code (excluding blank lines and comments).
   - Self-contained: executable via `.venv/bin/python`.
   - ISOLATED SCRATCH DIRECTORY: All file/database operations must use a dedicated
     scratch directory (e.g. `tempfile.TemporaryDirectory()`). Never touch repo DBs.
   - ZERO MOCKS: Must use real `sqlite3`, real disk I/O, real threads/processes,
     or real memory allocations. No `unittest.mock` or `:memory:`.
   - Keep multiline strings and SQL statements compact to avoid exhausting the 50-line limit.
   - Must execute in < 10 seconds.
   - Output must be machine-readable JSON on stdout including measured metrics:
     {"status": "PASS", "metrics": { ... }}
2. Execute the probe and capture stdout, stderr, and exit status.
3. Write the execution receipt to docs/arch-ledger/receipts/probe-{{TOPIC_ID}}-receipt.json:
   {
     "topic_id": "{{TOPIC_ID}}",
     "probe_path": "probes/probe-{{TOPIC_ID}}.py",
     "executed_at": "<ISO-TIMESTAMP>",
     "exit_code": 0,
     "duration_ms": 124.5,
     "metrics": { ... }
   }
   CIRCUIT BREAKER: If the probe fails, times out, or exceeds 50 lines, STOP.
   Do NOT proceed to ADR drafting. Record failure in the session log and diagnose.

--------------------------------------------------------------------------------
PHASE C: DELIBERATION & TRADE-OFF EVALUATION
--------------------------------------------------------------------------------
In docs/arch-ledger/sessions/session-{{TOPIC_ID}}.md, deliberate between:
1. Option 1: Minimal surgical patch.
2. Option 2: Structural redesign.
3. Option 3: Rejected alternatives (document explicitly why theoretical approaches
   or distributed systems were rejected).
Evaluate each option against scale headroom (250k scale), write lock hold time (< 2ms),
and memory footprint (<= 450MB envelope).

--------------------------------------------------------------------------------
PHASE D: FORMAL ADR GENERATION
--------------------------------------------------------------------------------
Draft docs/arch-ledger/adrs/ADR-{{TOPIC_ID}}-<slug>.md following MADR format:
- Title: ADR-{{TOPIC_ID}}: <Decision Title>
- Status: Accepted
- Context & Problem Statement (citing exact files and line numbers)
- Decision Drivers (budgets, SQLite constraints, single-file local-first)
- Considered Options & Decision Outcome
- Empirical Validation (linking to probe script and probe receipt JSON)
- Implementation Contract: Exact file modifications, functions, and transaction boundaries.

--------------------------------------------------------------------------------
PHASE E: STATE MACHINE ADVANCEMENT
--------------------------------------------------------------------------------
1. Update docs/arch-ledger/STATE.json:
   - For {{TOPIC_ID}}: set "status": "ACCEPTED",
     "probe_path": "probes/probe-{{TOPIC_ID}}.py",
     "adr_path": "adrs/ADR-{{TOPIC_ID}}-<slug>.md",
     "receipt_path": "receipts/probe-{{TOPIC_ID}}-receipt.json"
   - For all downstream topics whose dependencies are now all "ACCEPTED": set "status": "READY"
   - Set "current_topic": null
   - If all topics are "ACCEPTED", set "state": "ALL_TOPICS_SETTLED"
   - Otherwise, set "state": "AGENDA_SYNTHESIZED"
2. Run `docs/arch-ledger/validate_ledger.py` to confirm state integrity.
3. Output final status confirmation and the next recommended topic.
```

---

### PROMPT 3: CONSOLIDATION, CHALLENGE, AND MILESTONE 1 BUILD ROADMAP PACKAGING

```text
================================================================================
TRACE-LITE ARCHITECTURE SUITE — PROMPT 3: CONSOLIDATION & ROADMAP PACKAGING
================================================================================
You are the Lead System Architect and Red Team Coordinator for TRACE-LITE.
Your mission is to review the complete set of ratified ADRs, execute an adversarial
cross-topic stress test (Challenge), and package the final, production-ready
Milestone 1 Build Roadmap.

WORKING DIRECTORY: /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite
PERSISTENT LEDGER: docs/arch-ledger/

MANDATORY PRE-FLIGHT ENTRY CHECK:
1. Read docs/arch-ledger/STATE.json.
2. Run `docs/arch-ledger/validate_ledger.py`. Every topic must be "ACCEPTED".

LIFECYCLE PHASES:

--------------------------------------------------------------------------------
PHASE A: ADVERSARIAL CROSS-TOPIC CHALLENGE (RED TEAM STRESS TEST)
--------------------------------------------------------------------------------
Examine the interactions between the accepted decisions. Attack the combined design:
1. Lock Ownership & Thread Interaction:
   Does the write path ADR conflict with the background worker checkpointing ADR?
2. Memory Budget Summation:
   Sum projected resident memory across all subsystems at 250k scale:
   (Flat-Hybrid matrix + Vector index + Holon state + SQLite cache + Runtime).
   Does the sum strictly remain within the 450 MB RSS ceiling?
3. Eventual Consistency vs. Exact Citations:
   Does moving projections out of the write transaction create any window where queries
   return stale or un-retracted evidence?
4. Schema & Harness Compatibility:
   Verify that relation schema fixes align with both extraction and qualification harnesses.

LOOP-BACK CIRCUIT BREAKER:
If an irreconcilable conflict is discovered between two ADRs:
- Update docs/arch-ledger/STATE.json: set the conflicting topic to "IN_SESSION"
  with reason documented in docs/arch-ledger/CHALLENGE_REPORT.md.
- HALT Prompt 3 and dispatch Prompt 2 to resolve the conflict.

If all challenges are resolved, write findings and mitigations to
docs/arch-ledger/CHALLENGE_REPORT.md and update "state": "CHALLENGES_RESOLVED".

--------------------------------------------------------------------------------
PHASE B: MILESTONE 1 BUILD ROADMAP PACKAGING
--------------------------------------------------------------------------------
Synthesize all ADRs, mitigations, and contracts into docs/arch-ledger/ROADMAP_M1.md.
Structure the roadmap into strict implementation phases:
- Phase 0: Substrate & Measurement Harness Recovery (Harness/schema fixes, frozen reproducers)
- Phase 1: Write Path Concurrency & WAL Checkpoint Discipline (Single-writer queue, active governor)
- Phase 2: Bounded Projection Pipeline & Holon Hub De-escalation (Partitioned holon clusters, outbox purge)
- Phase 3: Served Snapshots & Incremental Vector Maintenance (Zero-reseed drift, memory-mapped vectors)
- Phase 4: Query Routing, Flat-Hybrid Optimization, & Retrieval (Lock-free beam, bounded dense pool)
- Phase 5: API Integrity, Pinned Dossier Resolution, & TMS Hardening (Atomic side-effects, AST evaluator)
- Phase 6: Qualification Ladder Execution (Sequential 2k -> 10k -> 50k -> 100k -> 250k rungs)

EACH PHASE IN THE ROADMAP MUST SPECIFY:
1. Owned Files: Explicit list of permitted source files.
2. Invariant Guardrails: Non-negotiable structural rules.
3. Test Commands: Exact pytest and benchmark commands.
4. Entry Gate & Exit Gate Criteria: Objective numeric thresholds.
5. Rollback Runbook: Exact git recovery commands if a gate fails.

--------------------------------------------------------------------------------
PHASE C: FINAL STATE MACHINE FREEZE
--------------------------------------------------------------------------------
1. Generate SHA256 hashes of all ADRs, probe scripts, receipts, and ROADMAP_M1.md.
2. Write the manifest into docs/arch-ledger/MANIFEST.json.
3. Update docs/arch-ledger/STATE.json:
   Set "state": "ROADMAP_FROZEN", "manifest_hash": "<SHA256>", "updated_at": "<ISO-TIMESTAMP>".
4. Run `docs/arch-ledger/validate_ledger.py --final`.
5. Print completion report with full roadmap executive summary.
```

---

## 4. Shared Addendum: Cross-Project Context & Boundary Protection

```text
================================================================================
SHARED ADDENDUM: CROSS-PROJECT CONTEXT & BOUNDARY PROTECTION
================================================================================
Applicable to: All Architecture Prompts, Sessions, and Builder Implementations.

1. THE THREE SYSTEMS AT A GLANCE
--------------------------------------------------------------------------------
- TRACE-LITE (This Repo: /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite):
  The local-first, single-file, embeddable evidence-memory engine.
  Core role: Fast ingest (<35ms), atomic source revisioning, exact byte-range citations,
  overlapping holon hierarchies, resilient multi-tier retrieval (lexical/beam/vector/hybrid),
  and LLM-free evidence dossier compilation.
  Boundaries: Single SQLite file, zero external network services, no PyTorch/LLM dependencies
  in the core serving path, strict resource ceilings (<=450MB RSS, <=35ms P95 query).

- TRACE (Reference Repo: /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace):
  The higher-order agentic and cognitive architecture.
  Core role: Multi-agent coordination, executive planning, long-horizon reflective loops,
  cortical substrate orchestration, dynamic reasoning, and goal arbitration.
  Relationship to TL: TRACE uses TRACE-LITE as its underlying durable evidence memory.
  TRACE-LITE is a read-only dependency of TRACE. TL must NEVER depend on or import TRACE.

- TRILLION DREAMS (Planning Archives: Desktop/trace lite and trillion dreams):
  The macro-scale vision for planetary-scale associative memory and intelligence.
  Core role: Long-term architectural explorations (4-pillar blueprint, Spine vs Cortex,
  prediction-error atomization, global knowledge federation).
  Relationship to TL: Source of architectural inspiration and conceptual models.
  NOT a code dependency. Speculative mechanisms from Trillion Dreams must NOT be merged
  into TL unless proven by isolated micro-probes and measured scale gates.

2. HARD BOUNDARY PROTECTION INVARIANTS FOR TRACE-LITE
--------------------------------------------------------------------------------
- INVARIANT 1: Zero Upward Dependencies.
  TRACE-LITE must never import code, modules, or network interfaces from TRACE or Trillion Dreams.
  TL is an independent, self-contained library and daemon.

- INVARIANT 2: No Heavy ML Frameworks in Core Serving.
  PyTorch, HuggingFace transformers, and CUDA runtimes are strictly forbidden in the
  primary qualification profile. TL relies on deterministic hashbows, quantized lightweight
  vectors, or ONNX runtimes. The fallback hashbow-128d representation must remain functional.

- INVARIANT 3: Single-File Local-First SQLite Primacy.
  Do not introduce multi-database distributed coordinators, Redis, Postgres, or external daemons.
  All hard state resides in a single SQLite database file utilizing WAL mode.

- INVARIANT 4: Evidence Ownership & Byte-Exact Citations.
  Every retrieved assertion or relation must bind back to an immutable source_revision
  and exact (start_byte, end_byte) span. Synthesizing or paraphrasing evidence without
  exact spans violates the core contract.

- INVARIANT 5: Explicit Concurrency Ownership.
  SQLite permits one writer. Concurrency must be managed via serialized writer queues,
  atomic pointer swaps for immutable readers, and coordinated checkpoint windows.
  Never rely on optimistic concurrency without retry discipline.
================================================================================
```

---
