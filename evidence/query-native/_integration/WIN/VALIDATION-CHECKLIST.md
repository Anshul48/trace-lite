# run_win.bat — Native Validation Checklist (failproof note)

Contract TL-QN-2026-09-12.1, steering §6. The WSL authoring environment
cannot execute `.bat` (WSL↔Windows interop broken: vsock error), so static
verification was done instead (`check_run_win.py` → exit 0, see
`check_run_win.log`; every invoked Python entry point proven to exist via
`pytest --collect-only` and harness `--help`). The native operator MUST run
the steps below in Windows Terminal from the repo root and report results.

Prerequisite (one time): `run_win.bat` bootstraps `.venv_win` itself
(uv preferred, else `py -3.11` / `python`), validates SQLite FTS5, and
installs `.[dev]`. No manual venv work needed.

## A. Successful bounded run (expect exit 0, timestamped outputs)

```cmd
cd /d C:\Users\anshu\OneDrive\Documents\Code\Utilities\trace-lite
run_win.bat gate
echo GATE_EXIT=%ERRORLEVEL%
```

Expect: `[GATE-PASS]`, `GATE_EXIT=0`, and a line reporting actual free
bytes on the `%LOCALAPPDATA%` drive (minimum 20 GB — budget covers per-rung
DB + WAL under TRACE concurrency + all 5 rung DBs retained + BEIR corpora +
model/generation artifacts + headroom; see `:do_gate` comment).

```cmd
run_win.bat smoke
echo SMOKE_EXIT=%ERRORLEVEL%
```

Expect: `SMOKE_EXIT=0`; `pytest -x -q tests/test_store.py
tests/test_source_spans.py` passes and its log is kept at
`evidence\query-native\win_smoke_<TIMESTAMP>\pytest_smoke.log`; then
scifact-only BEIR runs into the same dir — OR a `[SKIP] scifact BEIR
skipped: ...` line with a reason (missing manifest), which still exits 0.
Every run creates a NEW timestamped dir; no run overwrites another.

## B. Induced failure (expect NONZERO exit, NO advancement)

```cmd
run_win.bat bogus-cmd-xyz
echo BOGUS_EXIT=%ERRORLEVEL%
dir /b /a:d evidence\query-native | find /c /v ""
```

Expect: `[ERROR] Unknown command: bogus-cmd-xyz`, `BOGUS_EXIT=1`, and NO
new `win_*` / `scale_ladder_*` directory under `evidence\query-native`
(the count matches the pre-run count). Alternatively run the automated
equivalent, which asserts all three properties itself:

```cmd
run_win.bat failproof
echo FAILPROOF_EXIT=%ERRORLEVEL%
```

Expect: `FAILPROOF_EXIT=0` meaning the self-check PASSED, i.e. (1) the
harness rejected `--bogus-flag-xyz` nonzero, (2) the recursive
`run_win.bat bogus-cmd-xyz` call exited nonzero, (3) the output-dir count
grew by exactly the failproof dir itself. (A `0` here certifies that
failures fail; a nonzero here means the failure paths are broken.)

## C. Full qualification (after A passes; long-running)

```cmd
run_win.bat ladder
echo LADDER_EXIT=%ERRORLEVEL%
```

Expect: gate first (a gate failure halts BEFORE any `RUN_DIR` is created),
then rungs 2k → 10k → 100k → 250k → 1M with halt-on-first-failure;
`LADDER_EXIT=0` only if all five succeed. Reports land in
`evidence\query-native\scale_ladder_<TIMESTAMP>\`; databases in
`%LOCALAPPDATA%\trace_lite_scale\<TIMESTAMP>\`.

Report back: the four `*_EXIT` values, the `[GATE] 2/3 disk:` free-bytes
line, and the names of any `win_*` / `scale_ladder_*` dirs created.
