# S1 black-box native gate — delivery (2026-09-12)

Contract: TL-QN-2026-09-12.1 + `SUPPLEMENT_S_BINDINGS_2026-09-12.md` B3.
Steering audit targets: retract/invalidate crash window, wrong claimed
revision IDs, interior byte offsets, excluded relation endpoints, invalid
members of atomic evidence groups.

## What was delivered

- `benchmarks/blackbox_native_check.py` (new, stdlib-only HTTP via
  `urllib`): drives a SEPARATE `uvicorn` server process over real HTTP
  (`127.0.0.1`, configurable `--port`), asserts on HTTP responses + fresh
  read-only on-disk SQLite reads. No `TestClient`, no `httpx`/`requests`,
  no in-process product imports (the only `trace_lite` import runs inside
  the child server). Platform branch is the kill primitive only
  (`SIGKILL` vs `taskkill /F`).
- `tests/test_s1_blackbox.py` (new, thin wrapper): read-only HTTP checks
  against a live URL; SKIPS (5 skipped) unless `--native-run` with
  `--native-url=` (or `S1_NATIVE_RUN=1` + `S1_NATIVE_URL`); never starts
  servers from pytest. Validated: 5 skipped by default, 5 passed live.
- `evidence/query-native/_integration/S1/runs/<run-id>/`: per-run
  `blackbox_run.log` (crash-safe incremental JSONL),
  `blackbox_summary.json`, `crash_window.log` (external crash-window
  audit), `server_stdout.log` (child server stderr/stdout).

## Coverage (all black-box: HTTP + on-disk DB)

1. Lifecycle `lifecycle.{ingest,query,edit,replay,delete,restart}`:
   vault + `POST /api/sync` ingest→query→edit→query→replay→delete→query,
   then graceful restart (NEW server proc, same DB) → query. Per-step
   persisted-effects checks (revision history, spans/views/runs/relations,
   corpus membership, jobs drained, `coverage_pending == 0`,
   `index_trusted`). PASS.
2. Stale-worker rejection: (a) `no_resurrection` — re-ingesting an
   OBSOLETE payload over HTTP mints no revision, reports the still-current
   revision, serves new text; (b) `superseded_skip` — rapid v1→v2 serves v2
   text only with v2 current. PASS.
3. Crash injection `crash.recovery`: 4×30 `POST /api/ingest` batch,
   `SIGKILL`/`taskkill` mid-batch, restart on same DB, bounded drain wait
   (≤180 s): no open jobs, no half-revision (every current revision fully
   covered), post-crash dossier cites current revisions byte-exact, honest
   `crash_window.log` (kill tool/pid/timestamps, pre-kill counts, restart,
   recovery). PASS.
4. Audits:
   - `crash.retract_window`: unlink WITHOUT sync, kill, restart. Honest
     two-path outcome recorded in `extra.path`: post-restart sync does NOT
     retract (poll-based watcher only tracks files observed in its
     lifetime) → served state must byte-match the pre-crash revision
     (coherent stale, never half), then the live-watcher path converges
     to gone with history retained. PASS (`coherent_stale_then_live_retract`
     on WSL; window finding documented, not hidden).
   - `audit.injection_gate` (GATE): caller-injected `candidates` /
     `revision_id` claims in `/api/dossier` bodies are ignored — fake rev
     never cited, absent from the raw response, all citations current.
     PASS (this is the HTTP-reachable face of INT-2 F1; the library-level
     silent-correction remains an F1 component fix).
   - `audit.byte_offsets`: uni + long + echo docs; excerpt bytes
     round-trip byte-exact vs raw payloads, valid UTF-8, in-bounds,
     methods proven (`r02_excerpt`/`source_slice`), trimmed excerpts
     anchored (not prefixes), ≥1 interior nonzero offset. PASS.
   - `audit.excluded_relations`: relay-b retracted over the served
     watcher path; post-retraction dossier selects no relay-b, no edge
     cites a relay-b revision, all edges resolve inside the visible
     current set. PASS.
   - `audit.atomic_group` → EXPLICIT GAP (1): `atomic_group_partitioned`
     is unreachable over HTTP (`/api/dossier` builds candidates from
     served atoms; no group bindings accepted from callers). Substitute
     delivered: `audit.omission_accounting` — starved-but-valid budgets
     yield NAMED omissions, never silent truncation. Partition behavior
     stays component-covered by the INT-2 battery.
5. Load `load.sustained`: 4 writers × 20 ingests + reader sampling.
   Record only (no absolute pass/fail). WSL PASS run:
   writes 80/80 ok, p50/p95/p99 = 0.112/0.173/0.178 s;
   reads 9 sampled, p50/p95/p99 = 0.077/0.092/0.092 s; no errors.

## WSL validation (this session)

` .venv/bin/python benchmarks/blackbox_native_check.py --port 8765
--workdir /tmp/s1work` → `PASS (15 steps ok, 1 gap(s))`, exit 0.
Passing run: `runs/s1-20260912T143826Z-58a2d3c1/` (log labeled WSL by
`platform: Linux` in `run.start`). Full suite: see VALIDATION-ops note.
Harness quirk fixed during validation: proxy env vars (`http_proxy` etc.)
hijack `urllib` even for loopback — the harness and wrapper bypass
proxies explicitly (`ProxyHandler({})`); native runs without such vars
are unaffected.

## Gaps (explicit, 1)

1. Atomic-group invalid-member partition not black-box proven (unreachable
   over HTTP by endpoint design; see above). No silent stall: recorded in
   `delivery.md`, run `note`, and summary `gaps`.

## Product findings (observed, src/ untouched — for S-owner disposition)

- F-A: `/api/dossier` with `max_total_tokens` below the fixed
  800 (system) + 200 (goal) + mandatory floor raises an unhandled
  `TokenBudget` `ValidationError` → HTTP 500 (traceback in the failing
  run's `server_stdout.log`). Suggest 422 + honest error; S1 asserts only
  inside the valid envelope.
- F-B: post-restart, a file deleted while the server was down is never
  retracted (`/api/sync` scans existing files only; the poll watcher
  tracks observations from its own lifetime). Served state stays
  coherent-stale until a live watcher event. This is the known
  retract→invalidate crash window, now with a black-box reproduction.
- F-C (harness, accommodated): the poll watcher (0.2 s) must OBSERVE a
  file before it can report its removal — vault write→unlink faster than
  one scan is silently a no-op. Harness settles 3 s between write and
  unlink wherever it retracts through the watcher.
