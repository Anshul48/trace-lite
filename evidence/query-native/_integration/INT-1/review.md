# INT-1 — Independent integration review (write path)

Contract TL-QN-2026-09-12.1. Verifier scope: path_matrix rows 1,2,3,4,7,8.
Builder claims checked, not re-verified at component level. Method: served
path only (real `TestClient` + real DB files under `/tmp`, `.venv/bin/python`
3.11.15, SQLite 3.53.1). Overall verdict: **PASS**.

## Per-arrow verdicts (vs `path_matrix.md`)

- Row 1 (API ingest/sync → immutable revision): **PASS**. Served sync of 2
  notes → 2 `current` revisions; edit → history `superseded`+`current` (old
  rows retained); replay/duplicate POST → same `revision_id`, no new
  revision/view/job rows (direct `sqlite3` counts after each step).
- Row 2 (revision → spans): **PASS**. Served writes mint `evidence_span` +
  `segmentation_view` rows (4 spans / 2 notes in probe); Unicode/CRLF/emoji
  doc: `byte_len` exact, every span slice valid UTF-8 and byte-exact.
- Row 3 (spans → assertions/relations): **PASS**. Served writes yield
  relations (5 / 2 notes); featureless unicode doc yields a `complete` run
  with 0 outputs — honest partial graph per ARCH §4/§10, coverage complete.
- Row 4 (→ reconciliation/holons): **PASS**. Corpus `thematic_cluster`
  active with 2 members; after edit it holds the new atom only (old
  membership gone); survives restart; post-delete state honest.
- Row 7 (edit/delete invalidation, R06-F4): **PASS**. Premise edit via
  `POST /api/sync` flips a dependent `derived_synthesis` to `stale` with no
  engine/invalidate call in the probe; delete via served watcher path
  removes lexical rows, retains revision history, serves new text only;
  `stale` persists across restart.
- Row 8 (restart/recovery): **PASS**. Restart on same DB serves persisted
  state (spans/relations/synthesis status intact, freshness honest);
  simulated crash-mid-projection (job reset to `pending`, views deleted)
  reports pending pre-restart and lifespan resume-drain completes it
  post-restart (job `done`, views rebuilt, `index_trusted` true).

## Demo tautology check

`tests/test_write_path_integration.py` is **not tautological**: persisted
effects are asserted via fresh `Database` connections reading tables
directly (`source_revision` history, span/view/run counts, `holon_state`,
`holon_members`, corpus membership of the new atom), not the
orchestrator's return values; query assertions check served snippet text;
`grep` confirms no `invalidate_dependents`/engine call in the F4 path
(only a docstring mention; `remove_note` unit test uses `engine`
directly, which is its subject, not the F4-via-API proof).

## Findings (none blocking)

1. Same-doc/different-payload via the API creates a superseding revision,
   not a rejection — ARCH §3.1 "conflict" cannot occur because the derived
   idempotency key hashes the payload. Documented behavior, no violation;
   contract wording may want alignment.
2. `docs/builds/query-native/STATE.md` touched inside the INT-1 time
   window but outside the owned set (status file, non-behavioral).
3. Worktree also holds pre-existing out-of-scope modifications
   (`COORDINATOR_LAUNCH_PROMPT.md`, `COORDINATOR.md`,
   `benchmarks/run_trace_lite_benchmark.py`, `WINDOWS_COORDINATOR_PROMPT.md`,
   `run_win.bat`) — predating INT-1 mtimes, for coordinator disposition.
   No tracked `tests/*.py` file modified (all test changes are new files);
   remaining tracked diffs attribute to R01–R07 per their `delivery.md`.
4. Declared INT-2 carryovers (not defects): R03 ANN unwired (row 5),
   atom-anchored dossier citations (row 6), retract-commit→invalidate
   crash window (ms, events retained).

## Execution scope (all observed this session)

- `pytest tests/test_write_path_integration.py -q` → 3 passed, exit 0.
- `pytest tests/ -q` (full suite, unmodified) → 240 passed, exit 0.
- `pytest test_store.py test_router.py test_obsidian_sync.py test_e2e.py`
  → 33 passed, exit 0 (legacy atom/FTS behavior unchanged).
- Own probe `/tmp/int1_verify_probe.py` (fresh /tmp DB, no repo files):
  39/39 checks OK, exit 0 — full lifecycle with per-step direct table
  inspection (`source_revision`, `evidence_span`, `assertion`/`relation`,
  `holon_members`, `projection_job`, `projection_generation`).
- Resume check `/tmp/int1_resume_check.py` → pending pre-restart,
  complete post-restart, exit 0. Unicode byte-exactness verified in-probe.

## Next action

INT-1 **PASS** — served write path provably uses R01/R02/R04/R05/R06
components with honest freshness. Proceed to INT-2 (rows 5, 6).
