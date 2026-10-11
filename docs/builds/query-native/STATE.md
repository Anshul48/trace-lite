# Trace-Lite Query-Native Memory Program — Current state

Updated: 2026-09-12
Current objective/tranche: Execute Packets R00 through R09 (Waves 0 to 8) under Autonomous Coordinator
Current contract revision: TL-QN-2026-09-12.1
Workspace/base/candidate: `c8e6b701dbd86a412ffec228afde319e122d337e` + local uncommitted working tree (untracked `uv.lock` preserved)

## Packet status

| Packet | State | Owner | Candidate | Evidence/review | Blocker or next action |
|---|---|---|---|---|---|
| R00 Baseline and evaluation contract | VERIFIED | Coordinator | `0df044b` + prompt-text-only dirty (F1: STATE.md edit itself also dirty; `src/`/`tests/`/`benchmarks/` confirmed clean) | `evidence/query-native/R00/delivery.md` + `review.md` PASS | Frozen anchor for R01+; follow-ups F2(b–c) due in R02, F5 bisect opportunistic |
| R01 Atomic evidence and projection lifecycle | VERIFIED | Coordinator | `0df044b` + R01 owned set (73/73 green; B3 PASS, pre-existing tests byte-unmodified) | `evidence/query-native/R01/delivery.md` + `review.md` PASS | Integrated baseline for R02; F2(b–c) span measurements due in R02 |
| R02 Source spans and adaptive view contract | VERIFIED | Coordinator | `0df044b`+R01+R02 (112/112; B4 fix + reverify PASS) | `evidence/query-native/R02/delivery.md` + `review.md` PASS | Span contract frozen for R03/R04 |
| R03 Learned retrieval and global index | INTEGRATED | Coordinator | `0df044b`+R01–R04 (155/155 integration recheck green) | `evidence/query-native/R03/delivery.md` + `review.md` PASS | Integrated; learned-encoder path stays fallback-labeled |
| R04 Evidence-linked local graphs | INTEGRATED | Coordinator | `0df044b`+R01–R04 (155/155 integration recheck green; B3 PASS) | `evidence/query-native/R04/delivery.md` + `review.md` PASS | Graph contract frozen for R05 |
| R05 Cross-source reconciliation and holons | INTEGRATED | Coordinator | `0df044b`+R01–R05 (182/182 B5 recheck green; B3 PASS) | `evidence/query-native/R05/delivery.md` + `review.md` PASS | Holon contract frozen for R06/R07 |
| R06 Incremental maintenance and publication | INTEGRATED | Coordinator | `0df044b`+R01–R06 (197/197 B5 recheck; B3 PASS on intent, ratio 1.06x) | `evidence/query-native/R06/delivery.md` + `review.md` PASS | Absolute 15ms gate env-infeasible via threaded TestClient; R09 must measure on real server |
| R07 Evidence dossiers and Cordis adapter | INTEGRATED | Coordinator | `0df044b`+R01–R07 (220/220 B5 recheck; B3 PASS) | `evidence/query-native/R07/delivery.md` + `review.md` PASS | Dossier contract frozen for R08/R09 |
| R08 Predictive refinement experiments | VERIFIED | Coordinator | manifest + R08 set (B3 PASS, PARK concurred) | `evidence/query-native/R08/delivery.md` + `disposition_memo.md` + `review.md` | PARK: structural baseline stands for R09; revival must complete §6 arms (F3) |
| R09 Integrated qualification | NEEDS_REPAIR | B3 FAIL (qualification not achieved; evidence accepted as diagnostics) | manifest + R09 set | `evidence/query-native/R09/review.md` | 2k+10k capacity-characterized CONDITIONAL-diagnostic; C9-3/C9-4 unmet; dossier wording stale, fix per review |
| S0–S6 Repaired execution model | S0 DONE; S1 GREEN; S2 VERIFIED; S3 DONE; S4 DONE; S5 DONE (17 tests + trial, suite 305 green, coordinator-verified); S6 RUNNING | Coordinator | manifest + ledger + blackbox harness | S1–S5 deliveries filed under evidence/.../_integration/S1..S5 | S6 (envelope + app cutover), then commit/push + Praxist |

## Current decisions

- **Accepted changes since previous handoff**:
  - Replaced legacy monolithic filing cabinet model with Query-Native Memory Program.
  - Formatted all project records strictly to `prompt-kit` specifications (`PROJECT.md`, `STATE.md`, `PACKET.md`s).
  - Preserved existing 39/39 passing test foundation; no destructive reset or burial required.
- **Pending user decisions and why they matter**: None. Full blanket authority granted to execute R00–R09 overnight.
- **Assumptions that changed and affected dependents**:
  - Latency targets (<50ms) and zero-ML constraints are demoted from dogmatic rules to empirical benchmarks.
  - Prediction error atomization is structured as an experimental ablation packet (R08) rather than an unverified assumption.

## Continuation

- **Next ready action and exact launch/reference**: Dispatch Wave 0 (`R00-baseline.md`) via Coordinator using B1 readiness and B2 build.
- **Work safe to perform concurrently**: Wave 3 allows R03 (Learned retrieval) and R04 (Local graphs) to proceed concurrently once R02 is verified and schema migrations are frozen.
- **Environment or permission blockers**: Remote git push requires credentials (deferred). Local test execution and SQLite builds fully functional in WSL `.venv`.
- **Remaining budget, if one was set**: Overnight autonomous run until all 10 packets are verified or blocked by root-cause failure.
- **Integration/release state**: Staged integration across waves, culminating in R09 master qualification dossier.

## Known limitations

- 1M scale benchmark in legacy code used latency profiling (`DENSE_POOL=2000`) rather than full 1M vector scoring.
- Flat hybrid 128-d hash-bag vectors exhibit centroid dilution and hash collision noise on overlapping vocabularies.
- Synchronous re-warming under global mutex in legacy `app.py` blocks concurrent API queries during note synchronization.
- Historical BEIR scores (SciFact 0.6589, FiQA 0.2495, NFCorpus 0.3188) reflect saved artifacts awaiting re-pinning in R00.
- R08 predictive atomization remains experimental until compared against R02/R04 structural graph baselines.
