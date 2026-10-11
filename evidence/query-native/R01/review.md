# R01 — Independent review (B3 verifier)

Role: R01 independent verifier (B3) / 2026-09-12
Packet and contract revision: R01-storage.md / TL-QN-2026-09-12.1
Candidate: git HEAD `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` + R01 owned set
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite 3.53.1 + FTS5 (same as builder)

## 1. Scope verification

`git status --short` shows tracked modifications only to
`src/trace_lite/store/schema.sql`, `src/trace_lite/store/__init__.py`
(owned), plus pre-existing coordinator prompt-text dirty files
(`COORDINATOR_LAUNCH_PROMPT.md`, `docs/builds/query-native/COORDINATOR.md`,
`docs/builds/query-native/STATE.md` — out of scope, untouched by me).
New untracked files are exactly the owned set:
`src/trace_lite/source/`, `src/trace_lite/store/{outbox,generations,migrate}.py`,
`tests/test_store_lifecycle.py`, `tests/test_migration.py`,
`evidence/query-native/R01/`.
`git diff HEAD -- tests/` is empty (no pre-existing test modified);
`git diff HEAD -- src/trace_lite/store/database.py` is empty (compat shim
untouched, as claimed); no changes to `benchmarks/` or
`evidence/query-native/R00/` (9 files + `beir/` present, read-only use).
No `.db`/`.bak` artifacts left in the repo; scratch DBs lived in `/tmp`
(ext4) and my probe file at `/tmp/r01_verifier_probe.py` (not in repo).

## 2. Evidence table (builder claim -> independent observation)

| Criterion | Builder evidence | Independent re-execution (exit codes) | Result |
|---|---|---|---|
| C1-1 Atomic replacement | `crash_test.log` PASS; `test_crash_*` x3 | `pytest tests/test_store_lifecycle.py tests/test_migration.py -q`: **20 passed, exit 0**. Probe: fault-after-all-writes keeps old revision current, counts frozen. `integrity_check: ok` re-observed | C1-1 CONFIRMED |
| C1-2 Idempotent submission | `idempotency.json` PASS (3 replays, conflict names `ev-key-1`) | Probe: double-delivery same key returns identical revision id with `created=False`, revision count frozen at 1; same key + different payload raises `IdempotencyConflict` naming `vk-1` | C1-2 CONFIRMED |
| C1-3 Async outbox durability | `outbox_recovery.log` PASS; lease floor, reclaim, restart resume | Probe: pending job visible after close/reopen, claimable and completable (9/9 probe checks, exit 0). Lease floor `>= 30 s` enforced in `outbox.claim` (ValueError observed for 5 s) | C1-3 CONFIRMED |
| C1-4 Migration integrity | `migration_parity.json`: 29 atoms -> 28 revisions, fidelity 1.0, backup + rerun safe | Scratch-copy migration: backup `*.bak-001` holds 18/18 atoms with 0 source/source_revision rows (valid pre-migration snapshot); rerun reports `migrated=False`, fidelity 1.0. **Independent byte audit** (raw sqlite3, `legacy_atom_map` never read): all 16 docs byte-exact incl. multi-atom `"\n"`-join doc, sha256 + `legacy_atom_text` label verified | C1-4 CONFIRMED |
| Full suite, no regressions | builder: 73 passed | `pytest tests/ -q`: **73 passed (53 pre-existing + 20 new), exit 0**, ~16 s, one pre-existing starlette warning only | CONFIRMED |

## 3. Test-quality inspection (no tautologies found)

- **Crash test is genuinely mid-transaction**: `_pre_commit_fault` is invoked
  in `source/service.py` after every write in the txn (atom, revision,
  pointer, event, outbox job, idempotency key) and before `commit`; the test
  asserts all four table counts unchanged plus current-pointer stability.
  Not a pre-write exception. The first-publish failure case additionally
  proves "never partial deletion" (zero rows everywhere).
- **FTS parity is MATCH + integrity-check, not COUNT(*) alone** (per
  EVALUATION §4): crash test compares MATCH results probe-by-probe against a
  clean rebuild; `verify_store` runs `integrity-check` plus per-revision
  token MATCH probes. Gate 10's "not merely row counts" requirement is met.
- **Obsolete-worker test uses a genuinely stale pinned revision**: v1 is
  superseded by a real v2 publish first, then the v1-pinned publish is
  rejected; `retract` with a stale pin is rejected too. A second variant
  covers migrated (legacy) sources. Gate 5 met.
- **Migration parity independence**: the in-repo `_parity_report` is
  map-assisted (offsets from `legacy_atom_map`), but the tests additionally
  assert per-doc full-text equality against the fixture independent of the
  map, and my probe's fully map-free audit (16/16 docs incl. multi-atom
  offsets, char-vs-byte handling via `"\n".join`) corroborates fidelity 1.0.
  No hidden fallbacks: `grep` for `or True` / `except.*pass` / `assert True`
  across both test files and all three new modules returns nothing.
- Lease-expiry simulation via direct SQL (no 30 s wall wait) is disclosed in
  delivery.md and is an acceptable bounded-fixture stand-in; the lease-floor
  constant itself (`MIN_LEASE_SECONDS = 30.0`) is enforced, not simulated.
- Minor observation (not a finding): `verify_store`'s FTS `integrity-check`
  statement runs uncommitted on the caller's connection; harmless for the
  read-only verification use, worth a comment if R02 reuses it.

## 4. Independent findings table

| # | Severity | Finding |
|---|---|---|
| F-1 | none | No scope violations: owned set only; database.py, pre-existing tests, benchmarks/, R00/ untouched |
| F-2 | none | No non-goal tables: schema yields exactly `atom, events, facets, memberships, idempotency_keys, legacy_atom_map, projection_job, projection_generation, schema_version, source, source_revision` (+ `fts_atoms`); the words "graph/holon/extraction" occur once, in a comment prohibiting those tables |
| F-3 | note | My first probe run flagged the backup check, but the defect was in my probe's condition (required absence of the `source` table, which fresh-DB DDL legitimately pre-creates empty). Direct inspection showed 0 migrated rows in the backup; probe corrected to assert emptiness rather than absence, then 9/9 green. No product change needed or made |

## 5. Verdict

**PASS.** C1-1, C1-2, C1-3, C1-4 all independently reproduced; 73/73 tests
green with 53 pre-existing tests byte-unmodified; scope, non-goal, and
rerun-safety/backup requirements verified on scratch copies. Bounded-fixture
limits (injected fault rather than power loss; no benchmark-scale loads) are
as stated in the packet and do not block R01.

## 6. Criteria mapping (EVALUATION §4 gates in R01 scope)

Gates 1 (byte-exact excerpts incl. Unicode/emoji/CRLF), 2 (idempotency),
3 (atomic replacement), 5 (obsolete worker), 7 (rebuild equivalence), 10
(restart hash/FTS/manifest verification) each have a dedicated passing test
plus an independent probe corroboration above. Gate 8 namespace isolation is
additionally covered and passing. Gates 4, 6, 9 belong to later packets.

## 7. Exact scope of independent execution

1. `.venv/bin/python -m pytest tests/test_store_lifecycle.py tests/test_migration.py -q` -> 20 passed, exit 0
2. `.venv/bin/python -m pytest tests/ -q` -> 73 passed, exit 0
3. `.venv/bin/python /tmp/r01_verifier_probe.py` (9 checks: cross-connection stale-write loss, double-delivery, conflict naming, byte round-trip x5, restart-pending outbox, backup validity, rerun safety, map-free byte audit) -> 9/9, exit 0
4. Read: both new test files, `source/service.py`, `store/{outbox,generations,migrate}.py`, `schema.sql`, `store/__init__.py` diff, all five R01 evidence files
5. Wrote no repo files except this review; made zero product-code changes

## 8. Next action

R01 is cleared as the DDL/base layer for R02, R04, R05. Dependents may build
on `source`/`source_revision`/`projection_job`/`projection_generation` and
the migration-001 contract. No repair (B4) routing needed.
