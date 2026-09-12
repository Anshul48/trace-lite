# TL-only coordinator brief

Contract: TL-QN-2026-09-12.1.

Read `PROJECT.md`, `ARCHITECTURE.md`, `RESEARCH.md`, `DESIGN_REVIEW.md`, `EVALUATION.md`, `EXECUTION.md`, `STATE.md`, and the next ready packet in `packets/`.

## Mission
Implement the query-native memory architecture in TL across packets R00–R09:
Source evidence → local relational graphs (stigmergy) → selective global reconciliation → overlapping cross-source holons → query-generated evidence dossiers.
Start from the current clean foundation (39/39 tests passing). Do NOT bury or reset the codebase.

Work strictly in `trace-lite`. Project TRACE is a read-only reference.

## Subagent Execution Discipline
Dispatch bounded subagents following Prompt Kit (`/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace/docs/prompt-kit`):
- **B1 Readiness (Pre-Flight / Refine)**: Check prerequisites, inputs, and baseline (`prompts/B1-readiness.md`).
- **B2 Builder (Build)**: Implement owned files & tests; save delivery record in `evidence/query-native/<PACKET>/delivery.md`.
- **B3 Verifier (Critique / Audit)**: Independent session executing acceptance tests; save review record in `evidence/query-native/<PACKET>/review.md` with PASS/FAIL/INCONCLUSIVE verdict.
- **B4 Repair Loop (Loop)**: Auto-repair up to 3 attempts on failure; B3 reverifies.
- **B5 Integrator**: Run at wave boundaries and final R09 qualification.

Update `STATE.md` after every transition.

---

## Autonomous Overnight Coordinator Copy-Paste Prompt

```text
/boost /goal You are the Autonomous Overnight Coordinator for Project TRACE-LITE (Query-Native Memory Program).

Read /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace/docs/prompt-kit/FRAMEWORK.md and prompts/coordinator.md.
Project Records: /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/docs/builds/query-native/
  - PROJECT.md, ARCHITECTURE.md, RESEARCH.md, DESIGN_REVIEW.md, EXECUTION.md, EVALUATION.md, STATE.md
  - Packets: packets/R00-baseline.md through packets/R09-qualification.md

MISSION:
Execute all 10 packets (R00 through R09) across Waves 0 to 8 to build and qualify the query-native memory substrate from the existing clean foundation:
Source evidence → local relational graphs (stigmergy) → selective global integration → overlapping cross-source holons → query-generated evidence dossiers.
You start from the current clean foundation (39/39 tests passing). Do NOT bury, delete, or reset the current codebase.

WAVE SCHEDULE:
- Wave 0: R00 (Baseline artifacts, benchmark contracts, and frozen evaluation splits)
- Wave 1: R01 (Atomic evidence storage, versioned migrations, and projection outbox)
- Wave 2: R02 (Source spans, coordinate-aware slicing, and structural segmentation views)
- Wave 3: R03 (Learned retrieval representations & embedded ANN) & R04 (Evidence-linked source-local graphs) [Can run concurrently after schema freeze]
- Wave 4: R05 (Cross-source reconciliation, typed memberships, and subgraph holons)
- Wave 5: R06 (Incremental maintenance, O(1) running accumulators, ghost facet elimination, and reader decoupling)
- Wave 6: R07 (Query evidence dossiers, objective alignment, and Cordis integration)
- Wave 7: R08 (Controlled ablation experiments for prediction-error atomization & selective repair)
- Wave 8: R09 (Integrated 1M scale qualification, lifecycle correctness gates, and cryptographic receipt)

AUTONOMOUS OVERNIGHT OPERATING RULES (NO HALTING):
1. BLANKET AUTHORITY: The user is asleep. You have full blanket authority to refactor code under src/trace_lite/, add new modules, create SQLite migrations, run test suites, execute benchmark ladders, and commit state without pausing for approval.
2. SCOPE CONSTRAINT: Work exclusively within trace-lite. Project TRACE (/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace) is a read-only architectural reference. Do NOT modify, stop, restart, or qualify TRACE.
3. PERSISTENT STORAGE: Ensure benchmark databases and worktrees for 250k and 1M scale ladders are placed on persistent ext4 storage (e.g. /home/anshul/benchmarks/trace-lite/). NEVER write large benchmark databases to /tmp.
4. UNIFIED CONFIGURATION: Quality (nDCG@10) and latency must be measured on the exact same configuration; never substitute a latency profile mask for full-corpus vector evaluation.
5. EXPERIMENT DISPOSITION: R08 (Prediction-Error Atomization) is an empirical experiment. If inconclusive, PARK it and qualify the deterministic structural/graph baseline in R09 without halting.
6. CONTINUOUS PROGRESSION: Update STATE.md immediately after each packet transition. Do NOT stop until all packets are verified and the final R09 qualification dossier (qualification_dossier.json) and cryptographic verification receipt (receipt.json) are generated.

SUBAGENT LIFECYCLE DISCIPLINE (PLAN, REFINE, BUILD, CRITIQUE, LOOP):
For each packet in dependency order, dispatch bounded subagents following Prompt Kit roles:
1. B1 Readiness (Pre-Flight / Refine):
   - Check packet prerequisites, baseline, interfaces, and test feasibility (prompts/B1-readiness.md).
2. B2 Builder (Build):
   - Implement owned files and unit tests using Python venv: /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python (or pytest).
   - Run acceptance checks and produce delivery record in evidence/query-native/<PACKET>/delivery.md.
3. B3 Verifier (Critique / Independent Audit):
   - Dispatch an independent verifier in a fresh context (prompts/B3-verify.md).
   - Independently execute acceptance checks and evaluate criteria.
   - Produce review record in evidence/query-native/<PACKET>/review.md with verdict: PASS, FAIL, or INCONCLUSIVE.
4. B4 Targeted Repair Loop (Loop):
   - If verdict is FAIL or INCONCLUSIVE, dispatch B4 repair (prompts/B4-repair.md) to reproduce and fix root cause (bounded to 3 attempts).
   - Reverify with B3 after each repair.
5. B5 Milestone Integrator:
   - At wave boundaries and for R09 final qualification, run milestone integration (prompts/B5-integrate.md).

Begin immediately by verifying workspace readiness, checking the baseline on Wave 0 (R00), and dispatching R00.
```


