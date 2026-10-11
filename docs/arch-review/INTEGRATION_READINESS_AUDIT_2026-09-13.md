# Component integration readiness — 2026-09-13

## Verdict

Independent Prompt Kit, SST, and STC audits plus a root TRACE check find substantial reusable implementations. Integration can begin now. A complete live-model architecture-to-code workflow has not been demonstrated. Targeted adapters and acceptance repairs remain; no component rewrite is implied.

## Candidate identities

| Project | HEAD | State |
|---|---|---|
| Prompt Kit | 4f442a6c829bc06b60f9979fdc8d7585e30bac9a | Dirty, including untracked executed code |
| SST | 079f4de6fd43881c2f1b529f0612f1df64a62f32 | Clean |
| STC | 5da7ed931f01fe7fd631c4bceadd7ae0a1034a07 | Six existing invariant-file modifications |
| TRACE | a24a937009fec41e6dcb6d2ca31d3c1783ac97f7 | Dirty V2 files and untracked qualification work |

These identify inspected checkouts, not cryptographically frozen release candidates. Audits made no source changes in these four projects, installed nothing, and made no live model calls.

## Findings

### Prompt Kit

Reusable: Orchestrator, persisted artifacts/state, phase rendering, planner/builder/critic harness interfaces, CLI.

- `src/prompt_kit/orchestrator.py:826`: grill checklist is heuristic, not model-based adversarial architecture investigation.
- `src/prompt_kit/orchestrator.py:1482`: parsed critic PASS advances VERIFIED without mechanically proving acceptance execution and reviewed candidate equality in that function.
- `src/prompt_kit/orchestrator.py:129`: candidate identity omits untracked files.
- Explicit SST/STC integration adapters not found.

Checks: native CLI help passed; five selected no-write handshake/probe tests passed; Node plugin metadata test passed with test isolation disabled. Broader temporary-file tests blocked by sandbox permissions. Pytest absent in its venv. Live DSH not tested.

### SST

Reusable: `SSTSearchController`, typed task/proposal contracts, model gateway, SQLite event persistence/replay, optional context provider.

- `src/sst/sandbox/custody.py:267`: custody executes only the first acceptance command, or reports PASS if none exists.
- Adversarial probe: invalid Python `def broken(:` with no acceptance command returned passed=true, exit_code=0, tests_passed=1.
- `src/sst/search/controller.py:711`: missing/failed critique can default toward approval.
- `src/sst/cli/main.py:64`: CLI selects TEST provider absent OPENAI_API_KEY.
- Budgets checked between iterations; reported live cost defaults to zero. Outer budget enforcement needed.
- Use proposal-returning controller; older build runner can apply directly to original workspace.

Checks: runtime dependencies imported; one-iteration TEST-provider search produced six events, replayed successfully, returned a winner. Fixture plumbing only. Pytest unavailable. No live model effectiveness established.

### STC

Reusable: typed contracts, context compiler, worktree machinery, mechanical gates, execution receipts and human promotion interfaces.

- `src/stc/cli.py:900`: default builder runs ruff format, not model implementation.
- `src/stc/services/engine.py:59`: generic engine handles ping/echo/compute; unknown actions can report executed without corresponding work.
- `src/stc/judiciary/auditor.py:137`: frontier CLI invocation supplies prompt as sandbox option value; inspected path also lacks structured semantic verdict handling.
- `src/stc/orchestrator/dispatch.py:65`: target worktree assumptions reference STC-specific files; parameterize for TL/TRACE.
- Worktree/copy isolation is not OS confinement.

Checks: schema suite 37 passed in 0.08s. Broader run had passing pure tests but filesystem fixture blocks; not a full-suite verdict.

## TRACE and temporary memory

Root ran `tests/test_phase1_local.py` through unittest using Python 3.12, bytecode disabled, redirecting module `_TEST_TEMP_ROOT` to a temporary directory: 11 tests passed in 1.688s. Tests include source preservation, replay/idempotency, import/export, projection parity, and bounded plugin behavior on fixtures. This does not qualify V2 or scale.

Root additionally imported STC TraceClient and TRACE LocalTrace together, ingested an identified source into a temporary LocalTrace, then read it using TraceClient.query_semantic_context(['bounded']). Source was returned; empty query_negative_inhibitors('*') returned []. This proves a narrow real cross-project read path, not full schema/write/lifecycle compatibility. Initial probe lacked required source_uri and was corrected; another file glob missed the .sqlite3 extension and exercised no reads, so only the final asserted probe counts.

STC compiler memory protocol has two list[str] methods: query_semantic_context and query_negative_inhibitors. SST context accepts an optional duck-typed provider. Full TRACE is therefore not a prerequisite for initial integration.

Context plus recursive summaries is a viable proposed adapter, not an implemented/validated replacement. Preserve originals and source references, accepted decisions, unresolved conflicts, and failed approaches outside lossy summaries. Summary navigation must not become acceptance authority.

## Smallest integration work

1. Use Prompt Kit for artifact/session orchestration, SST for bounded proposal search, STC for bundles/execution/gates.
2. Add explicit task/artifact adapters and wire a real builder/critic harness.
3. Require actual complete acceptance checks; errors/missing evidence remain unverified.
4. Bind results to tracked and untracked candidate content.
5. Explicitly configure fixture/live provider identity and outer budgets.
6. Start with optional file-backed memory or the narrowly tested local TRACE read surface.
7. Demonstrate one real end-to-end workflow before granting autonomous acceptance authority.

Current judgment: integration-ready components with bounded gaps, not a finished integrated platform. One-day integration remains plausible but unverified.
