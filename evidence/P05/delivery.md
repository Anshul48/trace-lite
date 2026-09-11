# P05 Delivery — Cordis / DSH Executive Plugin Suite

- Packet: `docs/builds/filing-cabinet/packets/P05-cordis-dsh-plugin-suite.md`
- Candidate commit: (this commit — Wave 4)

## Files
- `src/trace_lite/cordis/models.py` — Pydantic 2 mirror of the formal spec: `AuthorityLevel`
  (USER 4 > ARCH_SPEC 3 > AGENT_DECISION 2 > TOOL_OUTPUT 1, `dominates`/`strictly_dominates`),
  commitments/exemptions/receipts, trace/atom/query/anchor/response, evidence/token-budget/
  compiled-context, proposal/verdict/receipt/inhibitor, dependency/revocation/cascade models,
  `InvariantVetoError`, `MAX_CONTEXT_TOKENS=3500`.
- `src/trace_lite/cordis/tms.py` — `ModularContractBoundaryTMS`: commitment register/query/
  predicate-evaluate, authority-gated `supersede_commitment` + `register_exemption` (veto on
  lower-over-higher), scope-cover matching, Tarjan `detect_cycles`, cycle-rejecting edges,
  membrane-quarantined `propagate_revocation`.
- `src/trace_lite/cordis/compiler.py` — `TraceLiteContextCompiler`: authority-ordered mandatory
  invariants first, salience-ranked greedy evidence packing inside `TokenBudget`, D1/D2
  `evaluate_salience`, RPE-adaptive `update_gating_threshold`.
- `src/trace_lite/cordis/gate.py` — `StepAcceptanceGate`: Tier 0 substrate → Tier 1
  invariant/inhibitor screen → Tier 2 TMS predicate re-check; HMAC verdict receipts;
  RPE +1.0 accept / −1.0 veto; inhibitor minting; `rollback_transaction`.
- `src/trace_lite/cordis/plugin.py` — `TraceLiteMemoryPlugin` (init/event/ingest/query/
  RCU leases/flush over store+router) + `TraceLiteLedgerService` (TMS delegate).
- `tests/test_cordis.py` — 6 durable tests incl. live conformance vs Desktop `interfaces.py`.

## Verification (builder-run)
- `.venv/bin/python -m pytest tests/test_cordis.py -q` → **6 passed**.
- 2 repairs, both test-harness side: missing `CommitmentStatus` re-export; `sys.modules`
  registration for the spec module's pydantic forward refs. Product untouched.
