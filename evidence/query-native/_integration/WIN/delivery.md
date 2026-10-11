# WIN runner repair — delivery (TL-QN-2026-09-12.1, steering §6)

Owned files only: `run_win.bat`, `evidence/query-native/_integration/WIN/**`.
`WINDOWS_COORDINATOR_PROMPT.md`, `benchmarks/`, `.venv`, R00–R08/INT
evidence untouched (workload.json read as oracle only).

## Defects fixed in run_win.bat (intent preserved throughout)

1. **Timestamp without wmic** — `:make_timestamp`: PowerShell
   `Get-Date -Format yyyyMMdd_HHmmss`, fallback sanitizes `%DATE%_%TIME%`.
2. **No-overwrite quality/BEIR** — outputs now go to timestamped
   `evidence\query-native\win_quality_<TS>\` (quality.json + beir_scifact/),
   like the ladder. Fixed `win_quality.json` / `win_beir` paths are gone.
3. **Subroutine/testable structure** — `:do_*` workers (`exit /b` only,
   never `pause`/`goto menu`) + `:run_*` wrappers (UX: headless `exit /b`
   vs interactive `pause`/`goto menu`). `:run_all` chains
   `call :do_tests/concurrency/quality` with halt-on-failure.
4. **Readiness gate** (`gate`, runs first inside `:do_ladder` before any
   mkdir): venv health (python + pytest + FTS5) → disk (actual free bytes
   on the `%LOCALAPPDATA%` drive, 20 GB minimum, 32-bit-overflow-safe
   length-then-lexical compare) → BEIR scifact manifest (byte sizes pinned
   from R00/workload.json; harness re-verifies SHA-256 at runtime). Any
   failure halts nonzero with no advancement. No `%TEMP%` claims anywhere.
5. **`smoke`** (bounded `pytest -x -q` store/spans subset + scifact-only
   BEIR, skip-with-reason, all timestamped) and **`failproof`** self-check
   (bogus harness flag + unknown-command probes must exit nonzero with no
   new output dirs). Native steps in `VALIDATION-CHECKLIST.md`.

Kept: `.venv_win`, exit-code preservation, halt-on-rung-failure,
2k→10k→100k→250k→1M, help/menu/dispatch parity, no success-on-failure
(unknown commands now exit 1 headless).

**Bonus defect found by the checker:** the draft's preamble read
`%ERRORLEVEL%` inside parenthesized blocks (toolchain `where` probes,
venv-create check) — batch expands those at block-parse time (stale), so a
present `py`/`python` could be missed and a failed venv create undetected.
Converted to delayed-expansion reads with capture-before-pause.

## Static verification (all in WSL; .bat not executable here)

- `check_run_win.py` → **exit 0, 0 failures** (dispatch/menu/help parity,
  goto/call resolution, worker call-safety, no wmic, no fixed outputs,
  gate-before-mkdir ordering, overflow-free compare, stale-expansion audit,
  rung/BEIR/smoke/failproof content, manifest sizes == workload.json,
  byte-compare unit cases incl. >2³¹). Full log: `check_run_win.log`.
- Python-side probes (same log): smoke subset **36 tests collect**,
  concurrency file **3 tests collect**; `--help` on all three harnesses
  confirms every flag the .bat passes; bogus flag → **exit 2**.
- Final `.bat` re-read line-by-line after all edits (labels, gotos,
  delayed expansion, quoting with spaces in `%LOCALAPPDATA%`).

## Native handoff (operator runs on Windows; see VALIDATION-CHECKLIST.md)

```cmd
run_win.bat gate      &  echo GATE_EXIT=%ERRORLEVEL%
run_win.bat smoke     &  echo SMOKE_EXIT=%ERRORLEVEL%
run_win.bat bogus-cmd-xyz  (expect exit 1, no new output dirs)
run_win.bat failproof &  echo FAILPROOF_EXIT=%ERRORLEVEL%
run_win.bat ladder    &  echo LADDER_EXIT=%ERRORLEVEL%
```
