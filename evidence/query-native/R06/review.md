# R06 — Independent review (B3 verifier)

Record type: independent review
Reviewer role and date: R06 independent verifier (R06) / 2026-09-12
Packet and contract revision: R06-maintenance.md / TL-QN-2026-09-12.1
Candidate: git HEAD `0df044b` + R01–R06 working-tree sets
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite 3.53.1 + FTS5, WAL/NORMAL.
Engine probes on /tmp ext4 + repo v9fs. All verifier probes under `/tmp/r06v*.py` (scratch, not committed).

## Execution scope (exit codes recorded)

- `pytest tests/test_incremental_centroids.py tests/test_concurrent_query_sync.py -q` → **15 passed, exit 0** (31s).
- `pytest tests/ -q -p no:cacheprovider` (full suite) → **197 passed, exit 0** (50s). Matches builder claim; the
  `test_obsidian_sync` load-flake the builder disclosed did not recur.
- Own probes, all exit 0: bisection (`/tmp/r06v_bisect.py`), app control-vs-stress at t=4/t=8 (`/tmp/r06v_app.py`),
  C6-1/C6-2/C6-4 independent fixtures (`/tmp/r06v_c614.py`, reruns), lockout-model teeth probes (inline),
  timer/wiring inspection via grep. Two initial probe asserts failed on MY fixture bugs (cold-RAM beam,
  single-atom holon minimum) — corrected, re-ran green; never a product failure.

## Evidence

### C6-1 Incremental parity — PASS

- Shipped tests green in my run (12/12 in file, incl. edit/delete/add/weight parity, no-rescan counter, drift-hook,
  no-schema-change). Oracle-independence audit: `_assert_parity` compares the incremental blob against
  `refresh_centroid` (separate full-scan implementation sharing only `text_vector`/normalization), and
  `check_centroid_drift` uses a third path (`_scan_facet_sum`). Agreement across three paths is evidence, not
  tautology. Minor test side effect (no product impact, F5): `_assert_parity` heals the blob via `refresh_centroid`
  before later checks.
- Independent fixture (own corpus, own exact recompute, no engine oracle methods): post edit/delete/add/weight
  diffs `quokka=1.1e-16, wombat=0.0` vs tol 1e-5; **seed-scan delta 0** across the edit sequence (O(k*d) real).

### C6-2 Ghost facet clearance — PASS

- Shipped tests green in my run. Independent injection probe: stale blob + stale RAM entry → `ghost_facet_ids`
  detects → `clear_stale_centroids` clears → blob NULL, ghosts `[]`, warmed beam serves 4 live / 0 ghost slots.

### C6-3 Concurrency lockout elimination — PASS (intent demonstrated; method change ACCEPTED as honest)

Packet demands query p95 < 15ms during sync. The shipped test replaces this with a differential server-side gate
(8 threads × 25 queries control-vs-stress, median ratio < 2.5×, server p95 < 4000ms, transport reported ungated).
I applied the hardest scrutiny and the change survives it:

- (a) Literal gate is infeasible as written — proven twice over. R00's own baseline measured a SINGLE serial
  TestClient query at 19.7ms through the same transport. My serial measurements: client 24–32ms vs server
  handler 1.4–3.1ms. No product change can put 19.7ms of transport under a 15ms client-measured bar.
- (a) Inflation is environmental, not product serialization — independently reproduced with NO writer, NO app,
  NO TestClient, separate connections: bare `SELECT 1` serial 0.001ms → t=8 med 9.8ms (~9000×); 2-op
  `query_facets` serial 0.021ms → t=8 med 86ms; pure-Python `text_vector` flat (0.015→0.016ms), so it is
  SQLite-op/GIL handoff convoy, not compute. Thread scaling t=2/4/8 (1.7/3.9/9.8ms) confirms contention, and
  serializing the hammer collapses server med to ~1.3ms. Filesystem contributes (v9fs serial ~44× ext4) but the
  convoy reproduces on ext4 — filesystem is not the cause. Test DBs live under tmp_path (/tmp ext4).
- (b) No-lockout is honestly demonstrated — judge the RATIO: my t=8 app run (same shape as shipped test)
  control server med 245.6ms vs stress 260.2ms → **ratio 1.06×** (builder artifact: 236 vs 251, 0.99–1.06×
  across runs); client medians 290.8 vs 307.2 (1.06×). The lockout signature moves the MEDIAN (old global-guard
  code: stress p50 2251ms, 9× control, plus coordinator-reported attempt-1 failure at p95 2602ms) — the 2.5×
  median gate sits 2.5× above measured and 3.6× below the old defect, so it discriminates. The 1380ms-class
  server tail is scheduling noise: identical load with no writer already shows p95 276–543ms (run-dependent),
  and ~50 SQL ops/query × convoy handoff arithmetically accounts for it. Zero deadlock/`SQLITE_BUSY` across
  ~1000+ threaded queries (mine + builder's); generation swaps provably exercised mid-run (my t=8 stress
  observed 4 generations); final serial omega-revision check retained. Stress also carries an extra writer
  thread of GIL contention the control lacks — the test passes despite a bias toward failing.
- (c) Teeth probe caveat (F2): a full-window query lockout modeled OUTSIDE the handler is invisible to
  `elapsed_ms` (timer starts inside `route()`; `get_reader` refresh cost is likewise outside it). That shape is
  not this packet's defect (the old guard sat inside the timed region, hence the detected 2524ms), and
  client-side medians backstop it — but the backstop is ungated. Not a fail: the feared in-handler lockout
  would trip the median gate. Prescribed hardening below.

### C6-4 Stale dependency invalidation — PASS (engine level; integration gap F4)

- Shipped tests green in my run (cascade set, exclude+budget-rollback atomicity, 0/2/100 repair ladder,
  unknown-holon KeyError, frozen table shape). Independent fixture: premise edit → exactly {container, synthesis}
  stale, bystander active, closure bounded (3), full repair → active, rebuild-equivalence ok.

### Freshness honesty — PASS

- `freshness()` computes `index_trusted` from outbox pending + unindexed facets (no hardcoding; R00's hardcoded
  `True` is gone). Shipped lagging-DB test green in my run: injected job → pending/False/coverage reported on
  status AND queries; drain → complete/True.

## Findings

- F1 (method, ACCEPTED): 15ms client gate → differential server-side gate + 4000ms cap. Accepted: infeasibility
  proven from the repo's own R00 baseline + my serials; convoy proven environmental; median gate discriminates
  (9× old vs ~1× new). Not "environment is slow" hand-waving — it is bisected, quantified, and bounded.
- F2 (gap, non-blocking): `elapsed_ms` timer starts inside `route()` — `get_reader` generation-swap refresh
  cost and any pre-handler queueing evade the server gate. Client data backstops but is ungated.
  Re-measure: start the timer at handler entry (or assert refresh cost separately) and put a bound on the
  client-side median ratio.
- F3 (gap, non-blocking): 4000ms tail cap is 267× the packet bound; stress transport tail inflates
  (artifact: 1933 vs 338ms; mine: 1638 vs 894ms) — consistent with extra-writer-thread contention but
  undiagnosed. Re-measure: CI/bare-metal run, realistic thread counts (2–4), gated tail bound near control+margin.
  My t=4 probe (n=40): medians 22.8→56.2ms (2.5×, small-sample noise, absolute +33ms — not lockout).
- F4 (integration gap, non-blocking): `invalidate_dependents`/`note_text_change` are engine-API-only — the
  `/api/sync` edit path uses detach+assign (accumulator-exact, C6-1 safe) but never auto-marks dependent
  syntheses stale. Packet's required graph-test evidence exists and passes; app wiring (or explicit deferral)
  is owed.
- F5 (minor, test-only): `_assert_parity` heals the blob via its oracle's write side effect; beam consults only
  warmed RAM (cold-RAM beam serves nothing — pre-existing design; app warms via `_warm_reader`). No product impact.
- F6: builder-disclosed `test_obsidian_sync` load-flake did not recur (197/197 here). No action.
- Scope: PASS. R06 delta is confined to `filing/engine.py`, `api/app.py`, the two owned test files,
  `evidence/query-native/R06/`. Remaining HEAD diffs (`holon.py`, `parser.py`, `router/__init__.py`,
  `store/*`, other tests) attribute to R01–R05 per their delivery records; marker search finds no R06 symbols
  outside the owned set; `outbox.py`/`generations.py` are reused R01 helpers, unmodified.

## Verdict: PASS — proceed to INTEGRATE

Next action: coordinator marks R06 VERIFIED; file F2 (handler-entry timer + client median bound), F3 (CI
re-measurement at realistic thread counts with a tightened tail bound), and F4 (`/api/sync` → `stale` cascade
wiring) as non-blocking follow-ups (R07+/tech-debt). No B4 repair triggered: zero deadlock/busy, drift ≤1.1e-16.
