# Trace-Lite Query-Native Memory Program — Current state

Updated: 2026-09-12
Current objective/tranche: Execute Packets R00 through R09 (Waves 0 to 8) under Autonomous Coordinator
Current contract revision: TL-QN-2026-09-12.1
Workspace/base/candidate: `c8e6b701dbd86a412ffec228afde319e122d337e` + local uncommitted working tree (untracked `uv.lock` preserved)

## Packet status

| Packet | State | Owner | Candidate | Evidence/review | Blocker or next action |
|---|---|---|---|---|---|
| R00 Baseline and evaluation contract | READY | Pending Dispatch | `c8e6b70` | None | Run B1 readiness, establish frozen workload & baseline |
| R01 Atomic evidence and projection lifecycle | DRAFT | Pending Dispatch | TBD | None | Blocked on R00 VERIFIED |
| R02 Source spans and adaptive view contract | DRAFT | Pending Dispatch | TBD | None | Blocked on R01 VERIFIED |
| R03 Learned retrieval and global index | DRAFT | Pending Dispatch | TBD | None | Blocked on R02 VERIFIED |
| R04 Evidence-linked local graphs | DRAFT | Pending Dispatch | TBD | None | Blocked on R02 VERIFIED |
| R05 Cross-source reconciliation and holons | DRAFT | Pending Dispatch | TBD | None | Blocked on R03 & R04 VERIFIED |
| R06 Incremental maintenance and publication | DRAFT | Pending Dispatch | TBD | None | Blocked on R05 VERIFIED |
| R07 Evidence dossiers and Cordis adapter | DRAFT | Pending Dispatch | TBD | None | Blocked on R06 VERIFIED |
| R08 Predictive refinement experiments | DRAFT | Pending Dispatch | TBD | None | Blocked on R07 VERIFIED |
| R09 Integrated qualification | DRAFT | Pending Dispatch | TBD | None | Blocked on R07; R08 disposition |

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
