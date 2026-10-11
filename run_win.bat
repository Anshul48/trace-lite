@echo off
setlocal enabledelayedexpansion

:: ============================================================================
:: TRACE-LITE: Production Native Windows One-Click Execution & Qualification Runner
:: Eliminates WSL2 v9fs filesystem latency, locks, and GIL thread convoys
:: ----------------------------------------------------------------------------
:: Contract: TL-QN-2026-09-12.1 | Steering Section 6 (WINDOWS-RUNNER REPAIR)
:: Intent preserved from the original concurrent-worker draft (run_win.bat);
:: the defects below were repaired. Do NOT edit WINDOWS_COORDINATOR_PROMPT.md.
::
:: REPAIR LOG (what changed and why):
::  1. TIMESTAMP: `wmic os get localdatetime` (deprecated/removed on current
::     Win11) replaced by :make_timestamp -> PowerShell Get-Date with a
::     %DATE%/%TIME%-sanitizing cmd fallback. All run outputs are timestamped.
::  2. NO-OVERWRITE: quality + BEIR outputs were one fixed path per run
::     (win_quality.json / win_beir). They now land in a timestamped
::     evidence\query-native\win_quality_<TIMESTAMP>\ dir like the ladder.
::  3. STRUCTURE: `:run_all` used `call :label` against labels ending in
::     `goto menu` / `pause`, so headless chaining could not return cleanly.
::     Work is now split: `:do_*` worker subroutines end ONLY in `exit /b`
::     (safe to `call`), while `:run_*` wrappers own UX (pause / goto menu
::     interactive, `exit /b` headless). Nothing `call`ed ever jumps to menu.
::  4. READINESS GATE (:do_gate, command `gate`): disk-space + BEIR-manifest +
::     venv-health checks run BEFORE the ladder creates anything. Any failure
::     HALTS with a nonzero exit and no advancement. Run dirs live under
::     %LOCALAPPDATA% (local fast storage); the gate reports ACTUAL free
::     bytes. No %TEMP%-guarantees-NVMe claim is made anywhere in this file.
::  5. SMOKE (`smoke`) + FAILPROOF (`failproof`) commands added; native
::     validation steps documented in
::     evidence\query-native\_integration\WIN\VALIDATION-CHECKLIST.md
::     (this environment cannot execute .bat: WSL<->Windows interop is broken).
:: Kept: dedicated .venv_win, exit-code preservation, halt-on-rung-failure,
:: 2k->10k->100k->250k->1M ladder, help/menu parity, no success-on-failure
:: (unknown commands exit nonzero headless; failures never print SUCCESS).
:: ============================================================================

title TRACE-LITE Native Windows Runner
cd /d "%~dp0"

set "HEADLESS=0"
if not "%~1"=="" set "HEADLESS=1"

echo ================================================================================
echo                     TRACE-LITE Native Windows Launcher
echo ================================================================================
echo Repository: %~dp0
echo.

:: 1. Detect Python / uv Toolchain
set "PYTHON_EXE="
where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "USE_UV=1"
    echo [INFO] Found uv package manager: using native Windows toolchain.
) else (
    set "USE_UV=0"
    echo [INFO] uv not found in PATH; checking system Python.
    where py >nul 2>&1
    rem Delayed expansion REQUIRED here: this whole else-block is parsed before
    rem `where` runs, so %ERRORLEVEL% would read the stale pre-block value.
    if !ERRORLEVEL! equ 0 (
        set "PYTHON_EXE=py -3.11"
    ) else (
        where python >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            set "PYTHON_EXE=python"
        ) else (
            echo [ERROR] No Python installation found. Please install Python 3.11+ or uv.
            if "%HEADLESS%"=="0" pause
            exit /b 1
        )
    )
)

:: 2. Setup Dedicated Native Windows Environment (.venv_win)
set "VENV_DIR=%~dp0.venv_win"
if not exist "%VENV_DIR%" (
    echo [INFO] Creating native Windows virtual environment ^(.venv_win^)...
    if "%USE_UV%"=="1" (
        uv venv "%VENV_DIR%" --python 3.11
    ) else (
        %PYTHON_EXE% -m venv "%VENV_DIR%"
    )
    rem Delayed expansion: inside this block %ERRORLEVEL% would be stale (parsed
    rem before venv creation runs); !ERRORLEVEL! reads the live value.
    if !ERRORLEVEL! neq 0 (
        rem Capture first: `pause` behavior on ERRORLEVEL is version-dependent,
        rem so the code must not be re-read after it.
        set "VENV_RET=!ERRORLEVEL!"
        echo [ERROR] Failed to create virtual environment.
        if "%HEADLESS%"=="0" pause
        exit /b !VENV_RET!
    )
)

set "WIN_PYTHON=%VENV_DIR%\Scripts\python.exe"
if not exist "%WIN_PYTHON%" (
    echo [ERROR] Virtual environment python.exe missing at %WIN_PYTHON%.
    if "%HEADLESS%"=="0" pause
    exit /b 1
)

:: 3. Verify SQLite + FTS5 & Install Dependencies
echo [INFO] Validating SQLite FTS5 capability...
"%WIN_PYTHON%" -c "import sqlite3; con = sqlite3.connect(':memory:'); con.execute('CREATE VIRTUAL TABLE t USING fts5(x);'); print('[OK] SQLite', sqlite3.sqlite_version, 'with FTS5 confirmed.')"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Current Python SQLite build does not support FTS5.
    if "%HEADLESS%"=="0" pause
    exit /b 1
)

echo [INFO] Checking dependencies in .venv_win...
if "%USE_UV%"=="1" (
    uv pip install -e ".[dev]" --python "%WIN_PYTHON%" --quiet
) else (
    "%WIN_PYTHON%" -m pip install -e ".[dev]" --quiet
)
set "DEPS_RET=%ERRORLEVEL%"
if %DEPS_RET% neq 0 (
    echo [ERROR] Dependency installation failed.
    if "%HEADLESS%"=="0" pause
    exit /b %DEPS_RET%
)

:: Set environment variables for native Windows execution
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "PYTHONUNBUFFERED=1"

:: Set default BEIR data path on Windows (local fast storage, NOT %TEMP%).
if "%LOCALAPPDATA%"=="" (
    set "BEIR_ROOT=C:\Users\anshu\AppData\Local\trace-bench-data"
) else (
    set "BEIR_ROOT=%LOCALAPPDATA%\trace-bench-data"
)

:: Headless command dispatch. Every target is a `:run_*` wrapper (interactive
:: UX + headless exit) or `:show_help`; workers (`:do_*`) are only `call`ed.
if /i "%~1"=="test" goto run_tests
if /i "%~1"=="concurrency" goto run_concurrency
if /i "%~1"=="quality" goto run_quality
if /i "%~1"=="bench" goto run_ladder
if /i "%~1"=="ladder" goto run_ladder
if /i "%~1"=="all" goto run_all
if /i "%~1"=="gate" goto run_gate
if /i "%~1"=="smoke" goto run_smoke
if /i "%~1"=="failproof" goto run_failproof
if /i "%~1"=="help" goto show_help
if not "%~1"=="" (
    echo [ERROR] Unknown command: %~1
    echo Run "run_win.bat help" for usage.
    if "%HEADLESS%"=="1" exit /b 1
    goto show_help
)

:: Interactive Menu (kept in 1:1 parity with dispatch + help below)
:menu
echo.
echo Select an action to run natively on Windows:
echo.
echo   [1] Run Full Pytest Suite (Native Windows SQLite + WAL)
echo   [2] Run Concurrency Stress Test (Lockout ^& Generation Swaps)
echo   [3] Run Quality ^& BEIR Evaluation (Local Fast Path, timestamped)
echo   [4] Run Staged Scale Ladder (2k -^> 10k -^> 100k -^> 250k -^> 1M)
echo   [5] Run Complete Verification (Tests + Concurrency + Quality)
echo   [6] Run Readiness Gate (disk + BEIR manifest + venv health)
echo   [7] Run Smoke Check (bounded subset + scifact-only BEIR)
echo   [8] Run Failproof Self-Check (induced failure must not advance)
echo   [9] Show Headless CLI Usage
echo   [0] Exit
echo.
set /p "CHOICE=Enter choice [0-9]: "

if "%CHOICE%"=="1" goto run_tests
if "%CHOICE%"=="2" goto run_concurrency
if "%CHOICE%"=="3" goto run_quality
if "%CHOICE%"=="4" goto run_ladder
if "%CHOICE%"=="5" goto run_all
if "%CHOICE%"=="6" goto run_gate
if "%CHOICE%"=="7" goto run_smoke
if "%CHOICE%"=="8" goto run_failproof
if "%CHOICE%"=="9" goto show_help
if "%CHOICE%"=="0" exit /b 0

echo [ERROR] Invalid selection.
goto menu

:: ============================================================================
:: INTERACTIVE WRAPPERS (`:run_*`). Each calls its `:do_*` worker, reports,
:: then EITHER exits with the worker code (headless) OR pauses back to :menu.
:: Workers never pause / never jump to :menu, so `call :do_*` always returns.
:: ============================================================================

:run_tests
echo.
echo ================================================================================
echo Running Full Pytest Suite (Native Windows)...
echo ================================================================================
call :do_tests
set "RET=%ERRORLEVEL%"
if %RET% equ 0 (
    echo [SUCCESS] Full test suite passed!
) else (
    echo [FAILURE] Test suite failed with exit code %RET%.
)
if "%HEADLESS%"=="1" exit /b %RET%
pause
goto menu

:run_concurrency
echo.
echo ================================================================================
echo Running Concurrency Stress Test (Native Windows Multi-threaded)...
echo ================================================================================
call :do_concurrency
set "RET=%ERRORLEVEL%"
if %RET% equ 0 (
    echo [SUCCESS] Concurrency test passed without lockouts!
) else (
    echo [FAILURE] Concurrency test failed with exit code %RET%.
)
if "%HEADLESS%"=="1" exit /b %RET%
pause
goto menu

:run_quality
echo.
echo ================================================================================
echo Running Quality ^& BEIR Evaluation...
echo ================================================================================
call :do_quality
set "RET=%ERRORLEVEL%"
if %RET% equ 0 (
    echo [SUCCESS] Quality checks passed.
) else (
    echo [FAILURE] Quality checks failed with exit code %RET%.
)
if "%HEADLESS%"=="1" exit /b %RET%
pause
goto menu

:run_ladder
echo.
echo ================================================================================
echo Running Staged Scale Ladder: 2k -^> 10k -^> 100k -^> 250k -^> 1M
echo ================================================================================
call :do_ladder
set "RET=%ERRORLEVEL%"
if %RET% equ 0 (
    echo [SUCCESS] Full Staged Scale Ladder 2k -^> 1M completed successfully!
) else (
    echo [FAILURE] Scale ladder halted with exit code %RET%.
)
if "%HEADLESS%"=="1" exit /b %RET%
pause
goto menu

:run_all
echo.
echo ================================================================================
echo Running Complete Verification (Tests + Concurrency + Quality)...
echo ================================================================================
call :do_tests
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [HALT] Complete verification stopped: test suite failed with exit code %RET%.
    if "%HEADLESS%"=="1" exit /b %RET%
    pause
    goto menu
)
call :do_concurrency
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [HALT] Complete verification stopped: concurrency test failed with exit code %RET%.
    if "%HEADLESS%"=="1" exit /b %RET%
    pause
    goto menu
)
call :do_quality
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [HALT] Complete verification stopped: quality checks failed with exit code %RET%.
    if "%HEADLESS%"=="1" exit /b %RET%
    pause
    goto menu
)
echo.
echo [SUCCESS] All verification suites passed natively on Windows!
if "%HEADLESS%"=="1" exit /b 0
pause
goto menu

:run_gate
echo.
echo ================================================================================
echo Running Readiness Gate (disk + BEIR manifest + venv health)...
echo ================================================================================
call :do_gate
set "RET=%ERRORLEVEL%"
if %RET% equ 0 (
    echo [SUCCESS] Readiness gate passed.
) else (
    echo [FAILURE] Readiness gate failed with exit code %RET%. Fix the items above.
)
if "%HEADLESS%"=="1" exit /b %RET%
pause
goto menu

:run_smoke
echo.
echo ================================================================================
echo Running Smoke Check (bounded)...
echo ================================================================================
call :do_smoke
set "RET=%ERRORLEVEL%"
if %RET% equ 0 (
    echo [SUCCESS] Smoke check passed.
) else (
    echo [FAILURE] Smoke check failed with exit code %RET%.
)
if "%HEADLESS%"=="1" exit /b %RET%
pause
goto menu

:run_failproof
echo.
echo ================================================================================
echo Running Failproof Self-Check...
echo ================================================================================
call :do_failproof
set "RET=%ERRORLEVEL%"
if %RET% equ 0 (
    echo [SUCCESS] Failproof self-check passed: failures exit nonzero and advance nothing.
) else (
    echo [FAILURE] Failproof self-check failed with exit code %RET%.
)
if "%HEADLESS%"=="1" exit /b %RET%
pause
goto menu

:show_help
echo.
echo Usage (Non-interactive / Headless):
echo   run_win.bat test         - Run full pytest test suite
echo   run_win.bat concurrency  - Run concurrent query/sync stress test
echo   run_win.bat quality      - Run quality and BEIR benchmarks (timestamped)
echo   run_win.bat ladder       - Run staged capacity ladder (2k -^> 1M, gated)
echo   run_win.bat bench        - Alias for ladder
echo   run_win.bat all          - Run tests, concurrency, and quality
echo   run_win.bat gate         - Readiness gate only (disk + BEIR + venv)
echo   run_win.bat smoke        - Bounded smoke: pytest subset + scifact-only BEIR
echo   run_win.bat failproof    - Self-check: induced failure -^> nonzero, no advance
echo   run_win.bat help         - Show this usage information
echo.
echo Or run with no arguments to enter the interactive menu.
echo.
if "%HEADLESS%"=="1" exit /b 0
pause
goto menu

:: ============================================================================
:: WORKER SUBROUTINES (`:do_*`). Testable units: they take no input, print
:: progress, and return ONLY via `exit /b <code>` (0 = pass). They never
:: `pause`, never `goto menu`, so `call :do_*` chaining in :run_all and
:: :do_ladder always regains control with %ERRORLEVEL% set. Convention: the
:: first statement after `call :do_*` must be `set "RET=%ERRORLEVEL%"`.
:: ============================================================================

:do_tests
"%WIN_PYTHON%" -m pytest tests -q
exit /b %ERRORLEVEL%

:do_concurrency
"%WIN_PYTHON%" -m pytest tests/test_concurrent_query_sync.py -v
exit /b %ERRORLEVEL%

:do_quality
:: Timestamped outputs (no-overwrite): one dir per run, like the ladder.
call :make_timestamp
set "Q_DIR=%~dp0evidence\query-native\win_quality_%TIMESTAMP%"
if not exist "%Q_DIR%" mkdir "%Q_DIR%"
echo [INFO] Quality outputs: %Q_DIR%
:: NOTE: inside (...) blocks, delayed expansion (!VAR!) is REQUIRED: %VAR%
:: would expand once at block-parse time (stale). setlocal enabledelayedexpansion
:: is active (see top of file).
if exist "benchmarks\run_quality_eval.py" (
    "%WIN_PYTHON%" benchmarks\run_quality_eval.py --tag win-native --out "%Q_DIR%\quality.json"
    if !ERRORLEVEL! neq 0 (
        echo [FAILURE] Quality eval failed with exit code !ERRORLEVEL!.
        exit /b !ERRORLEVEL!
    )
) else (
    echo [WARNING] benchmarks\run_quality_eval.py not found - skipping quality eval.
)
call :check_beir_manifest
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [WARNING] BEIR data root incomplete at %BEIR_ROOT% - skipping BEIR benchmark.
    echo [WARNING] Run "run_win.bat gate" for the itemized manifest report.
) else (
    echo [INFO] Found BEIR data root at %BEIR_ROOT%
    if exist "benchmarks\run_beir_unified.py" (
        "%WIN_PYTHON%" benchmarks\run_beir_unified.py --dataset scifact --data-root "%BEIR_ROOT%" --out-dir "%Q_DIR%\beir_scifact"
        if !ERRORLEVEL! neq 0 (
            echo [FAILURE] BEIR eval failed with exit code !ERRORLEVEL!.
            exit /b !ERRORLEVEL!
        )
    ) else (
        echo [WARNING] benchmarks\run_beir_unified.py not found - skipping BEIR benchmark.
    )
)
exit /b 0

:do_gate
:: READINESS GATE. Verifies venv health + free disk + BEIR manifest BEFORE any
:: stage creates databases or reports. ANY failure exits nonzero and the
:: caller (:do_ladder) must halt WITHOUT creating RUN_DIR/REPORT_DIR, so a
:: failed gate provably advances nothing. Order below is deliberate: the
:: cheapest checks (venv) run first, disk second, manifest last.
echo [GATE] 1/3 venv health: %WIN_PYTHON%
if not exist "%WIN_PYTHON%" (
    echo [GATE-FAIL] venv python missing at %WIN_PYTHON%. Recreate .venv_win.
    exit /b 1
)
"%WIN_PYTHON%" -c "import pytest, sqlite3; con=sqlite3.connect(':memory:'); con.execute('CREATE VIRTUAL TABLE t USING fts5(x);')"
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [GATE-FAIL] venv cannot import pytest or lacks FTS5 ^(exit %RET%^). Reinstall .venv_win deps.
    exit /b %RET%
)
echo [GATE-OK] venv python runs with pytest + FTS5.
:: --- disk space on the LOCALAPPDATA drive (actual bytes, reported below) ---
:: Budget behind MIN_FREE_GB (see also VALIDATION-CHECKLIST.md): per-rung DB
:: (payload + FTS5 index + 128-d hash vectors + centroid/learned artifacts,
:: single-digit GB at the 1M rung) + WAL/journal under TRACE concurrency
:: (allow ~1x DB while writers + readers overlap) + all 5 rung DBs retained
:: together in RUN_DIR + benchmark reports + BEIR corpora (~100s of MB) +
:: model/generation artifacts (small: hash-bag has no downloads; learned
:: router artifacts are MBs) + safety headroom. 20 GB satisfies this with
:: margin on the qualification host.
set "MIN_FREE_GB=20"
set "GATE_DRIVE=%LOCALAPPDATA:~0,1%"
set "FREE_BYTES="
for /f "usebackq delims=" %%F in (`powershell -NoProfile -Command "(Get-PSDrive -Name '%GATE_DRIVE%').Free" 2^>nul`) do set "FREE_BYTES=%%F"
if not defined FREE_BYTES (
    echo [GATE] PowerShell probe unavailable; using dir-based fallback ^(English-locale parse^).
    for /f "tokens=3" %%F in ('dir /-c "%LOCALAPPDATA%" ^| findstr /i /c:"bytes free"') do set "FREE_BYTES=%%F"
)
if not defined FREE_BYTES (
    echo [GATE-FAIL] could not measure free space on %LOCALAPPDATA%. Aborting ^(no measurement = not ready^).
    exit /b 1
)
echo [GATE] 2/3 disk: %FREE_BYTES% bytes free on %GATE_DRIVE%: drive hosting %LOCALAPPDATA% (minimum %MIN_FREE_GB% GB).
:: NOTE: plain string append, NOT set /a (cmd arithmetic is 32-bit signed and
:: 20 GB overflows it). check_bytes_ge compares by length then lexically.
set "MIN_FREE_BYTES=%MIN_FREE_GB%000000000"
call :check_bytes_ge "%FREE_BYTES%" "%MIN_FREE_BYTES%"
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [GATE-FAIL] only %FREE_BYTES% bytes free; need at least %MIN_FREE_BYTES% bytes ^(%MIN_FREE_GB% GB^). Free space and re-run.
    exit /b 1
)
echo [GATE-OK] disk headroom sufficient.
:: --- BEIR manifest (byte sizes pinned in evidence/query-native/R00/workload.json;
:: the harness itself re-verifies SHA-256 from checksums.json at run time) ---
echo [GATE] 3/3 BEIR manifest under %BEIR_ROOT%.
call :check_beir_manifest
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [GATE-FAIL] BEIR manifest incomplete. Fix the missing files above and re-run.
    exit /b %RET%
)
echo [GATE-OK] BEIR scifact manifest present (corpus + queries + qrels).
echo [GATE-PASS] venv + disk (%FREE_BYTES% bytes free) + BEIR manifest all satisfied.
exit /b 0

:do_smoke
:: BOUNDED smoke: fast pytest subset + scifact-only BEIR when the manifest is
:: present, skip-with-reason when it is not (unlike :do_gate, a missing
:: manifest must NOT fail smoke). Everything lands timestamped in SMOKE_DIR.
call :make_timestamp
set "SMOKE_DIR=%~dp0evidence\query-native\win_smoke_%TIMESTAMP%"
if not exist "%SMOKE_DIR%" mkdir "%SMOKE_DIR%"
echo [INFO] Smoke outputs: %SMOKE_DIR%
"%WIN_PYTHON%" -m pytest -x -q tests/test_store.py tests/test_source_spans.py > "%SMOKE_DIR%\pytest_smoke.log" 2>&1
set "RET=%ERRORLEVEL%"
type "%SMOKE_DIR%\pytest_smoke.log"
if %RET% neq 0 (
    echo [FAILURE] Smoke pytest subset failed with exit code %RET%. Log kept at %SMOKE_DIR%\pytest_smoke.log.
    exit /b %RET%
)
call :check_beir_manifest >nul 2>&1
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [SKIP] scifact BEIR skipped: BEIR manifest incomplete at %BEIR_ROOT%\beir-scifact\raw\scifact ^(run "run_win.bat gate" for details^). pytest subset above is the smoke result.
    exit /b 0
)
if not exist "benchmarks\run_beir_unified.py" (
    echo [SKIP] scifact BEIR skipped: benchmarks\run_beir_unified.py not found. pytest subset above is the smoke result.
    exit /b 0
)
"%WIN_PYTHON%" benchmarks\run_beir_unified.py --dataset scifact --data-root "%BEIR_ROOT%" --out-dir "%SMOKE_DIR%\beir_scifact"
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [FAILURE] Smoke scifact BEIR failed with exit code %RET%.
    exit /b %RET%
)
exit /b 0

:do_ladder
:: Gated staged ladder. The gate runs FIRST, before RUN_DIR/REPORT_DIR exist,
:: so a failed gate advances nothing. Each rung halts the ladder nonzero on
:: failure (halt-on-rung-failure); later rungs never run after a failure.
call :make_timestamp
if "%TIMESTAMP%"=="" (
    echo [ERROR] Could not establish a run timestamp. Aborting.
    exit /b 1
)
call :do_gate
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [HALT] Ladder blocked: readiness gate failed with exit code %RET%. No runs started, no outputs written.
    exit /b %RET%
)
:: Create persistent timestamped run directory on local fast storage
:: (LOCALAPPDATA, not %TEMP%: no throughput guarantee is claimed for %TEMP%).
set "RUN_DIR=%LOCALAPPDATA%\trace_lite_scale\%TIMESTAMP%"
if not exist "%RUN_DIR%" mkdir "%RUN_DIR%"
echo [INFO] Storing scale databases in local fast storage: %RUN_DIR%

set "REPORT_DIR=%~dp0evidence\query-native\scale_ladder_%TIMESTAMP%"
if not exist "%REPORT_DIR%" mkdir "%REPORT_DIR%"

:: Rung 1: 2k Sanity Probe
echo.
echo --- Rung 1 / 5: 2k Spans Sanity Probe ---
"%WIN_PYTHON%" benchmarks\run_trace_lite_benchmark.py --docs 2000 --queries 100 --db-dir "%RUN_DIR%\db_2k" --out "%REPORT_DIR%\report_2k.json"
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [HALT] Ladder stopped: 2k rung failed with exit code %RET%.
    exit /b %RET%
)

:: Rung 2: 10k Standard Baseline
echo.
echo --- Rung 2 / 5: 10k Spans Standard Baseline ---
"%WIN_PYTHON%" benchmarks\run_trace_lite_benchmark.py --docs 10000 --queries 500 --db-dir "%RUN_DIR%\db_10k" --out "%REPORT_DIR%\report_10k.json"
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [HALT] Ladder stopped: 10k rung failed with exit code %RET%.
    exit /b %RET%
)

:: Rung 3: 100k Extended Scale
echo.
echo --- Rung 3 / 5: 100k Spans Extended Scale ---
"%WIN_PYTHON%" benchmarks\run_trace_lite_benchmark.py --docs 100000 --queries 500 --db-dir "%RUN_DIR%\db_100k" --out "%REPORT_DIR%\report_100k.json"
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [HALT] Ladder stopped: 100k rung failed with exit code %RET%.
    exit /b %RET%
)

:: Rung 4: 250k Intermediate Stress Gate
echo.
echo --- Rung 4 / 5: 250k Spans Intermediate Stress Gate ---
"%WIN_PYTHON%" benchmarks\run_trace_lite_benchmark.py --docs 250000 --queries 500 --db-dir "%RUN_DIR%\db_250k" --out "%REPORT_DIR%\report_250k.json"
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [HALT] Ladder stopped: 250k rung failed with exit code %RET%.
    exit /b %RET%
)

:: Rung 5: 1M Qualification Target
echo.
echo --- Rung 5 / 5: 1M Spans Master Qualification ---
"%WIN_PYTHON%" benchmarks\run_trace_lite_benchmark.py --docs 1000000 --queries 1000 --db-dir "%RUN_DIR%\db_1m" --out "%REPORT_DIR%\report_1m.json"
set "RET=%ERRORLEVEL%"
if %RET% neq 0 (
    echo [HALT] Ladder stopped: 1M rung failed with exit code %RET%.
    exit /b %RET%
)

echo.
echo ================================================================================
echo [SUCCESS] Full Staged Scale Ladder 2k -^> 1M completed successfully!
echo Reports saved in: %REPORT_DIR%
echo ================================================================================
exit /b 0

:do_failproof
:: SELF-CHECK that failure paths behave: (1) a bogus harness invocation must
:: exit nonzero; (2) an unknown run_win command (recursive headless call) must
:: exit nonzero; (3) neither may create new output dirs under evidence
:: (no advancement on failure). Any violation fails this check nonzero.
call :make_timestamp
set "FP_DIR=%~dp0evidence\query-native\win_failproof_%TIMESTAMP%"
if not exist "%FP_DIR%" mkdir "%FP_DIR%"
set "EV_DIR=%~dp0evidence\query-native"
for /f %%C in ('dir /b /a:d "%EV_DIR%" 2^>nul ^| find /c /v ""') do set "DIRS_BEFORE=%%C"
set "DIRS_BEFORE=%DIRS_BEFORE: =%"
if "%DIRS_BEFORE%"=="" set "DIRS_BEFORE=0"
echo [CHECK] 1/3 bogus harness flag must exit nonzero...
"%WIN_PYTHON%" benchmarks\run_trace_lite_benchmark.py --bogus-flag-xyz >nul 2>&1
set "RET=%ERRORLEVEL%"
if %RET% equ 0 (
    echo [FAIL] harness accepted --bogus-flag-xyz with exit 0; exit-code preservation broken.
    exit /b 1
)
echo [OK] harness rejected bogus flag with exit code %RET%.
echo [CHECK] 2/3 unknown run_win command must exit nonzero...
cmd /c ""%~f0" bogus-cmd-xyz" > "%FP_DIR%\unknown_cmd.log" 2>&1
set "RET=%ERRORLEVEL%"
if %RET% equ 0 (
    echo [FAIL] "run_win.bat bogus-cmd-xyz" exited 0; unknown commands must fail. See %FP_DIR%\unknown_cmd.log.
    exit /b 1
)
echo [OK] unknown command exited nonzero (%RET%). Log: %FP_DIR%\unknown_cmd.log.
for /f %%C in ('dir /b /a:d "%EV_DIR%" 2^>nul ^| find /c /v ""') do set "DIRS_AFTER=%%C"
set "DIRS_AFTER=%DIRS_AFTER: =%"
if "%DIRS_AFTER%"=="" set "DIRS_AFTER=0"
:: The only dir this check may add is FP_DIR itself.
set /a "DIRS_EXPECTED=%DIRS_BEFORE%+1"
echo [CHECK] 3/3 no advancement: dirs before=%DIRS_BEFORE% after=%DIRS_AFTER% expected=%DIRS_EXPECTED%...
if not "%DIRS_AFTER%"=="%DIRS_EXPECTED%" (
    echo [FAIL] output-dir count changed unexpectedly; a failed run advanced state.
    exit /b 1
)
echo [OK] no advancement on failure.
exit /b 0

:: ============================================================================
:: LEAF HELPERS. Pure subroutines (`call`, `exit /b` only).
:: ============================================================================

:make_timestamp
:: Robust timestamp WITHOUT wmic (deprecated/removed on current Win11).
:: Primary: PowerShell Get-Date -> yyyyMMdd_HHmmss. Fallback: sanitized
:: %DATE%_%TIME% (locale-dependent shape, but still unique per second, which
:: is all the no-overwrite rule needs). Never leaves TIMESTAMP empty.
set "TIMESTAMP="
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss" 2^>nul`) do set "TIMESTAMP=%%T"
if defined TIMESTAMP exit /b 0
set "TIMESTAMP=%DATE%_%TIME%"
set "TIMESTAMP=%TIMESTAMP:/=%"
set "TIMESTAMP=%TIMESTAMP:-=%"
set "TIMESTAMP=%TIMESTAMP::=%"
set "TIMESTAMP=%TIMESTAMP:.=%"
set "TIMESTAMP=%TIMESTAMP:,=%"
set "TIMESTAMP=%TIMESTAMP: =0%"
set "TIMESTAMP=%TIMESTAMP: =%"
if defined TIMESTAMP exit /b 0
set "TIMESTAMP=notime"
exit /b 0

:check_beir_manifest
:: Verifies the scifact BEIR input manifest under %BEIR_ROOT%. Prints one
:: [OK]/[MISSING]/[SIZE-MISMATCH] line per file. Byte sizes pinned from
:: evidence/query-native/R00/workload.json (checksums); the BEIR harness
:: itself re-verifies SHA-256 from checksums.json at run time.
set "BEIR_DS=%BEIR_ROOT%\beir-scifact\raw\scifact"
call :check_file_bytes "%BEIR_DS%\corpus.jsonl" 8111749
set "RET=%ERRORLEVEL%"
if %RET% neq 0 exit /b %RET%
call :check_file_bytes "%BEIR_DS%\queries.jsonl" 147938
set "RET=%ERRORLEVEL%"
if %RET% neq 0 exit /b %RET%
call :check_file_bytes "%BEIR_DS%\qrels\test.tsv" 5389
set "RET=%ERRORLEVEL%"
if %RET% neq 0 exit /b %RET%
exit /b 0

:check_file_bytes
:: %1 = path, %2 = expected byte size. 0 = present with exact size.
if not exist "%~1" (
    echo [MISSING] %~1
    exit /b 1
)
if not "%~z1"=="%~2" (
    echo [SIZE-MISMATCH] %~1 has %~z1 bytes, expected %~2.
    exit /b 1
)
echo [OK] %~nx1 (%~z1 bytes).
exit /b 0

:check_bytes_ge
:: %1 = actual bytes, %2 = required bytes (decimal digit strings, any size).
:: 0 = actual ^>= required. Length-then-lexical: avoids cmd's 32-bit `set /a`
:: and `if gtr` overflow past ~2.1 GB.
set "CB_A=%~1"
set "CB_B=%~2"
call :strlen CB_A _LA
call :strlen CB_B _LB
if %_LA% lss %_LB% exit /b 1
if %_LA% gtr %_LB% exit /b 0
if "%CB_A%" lss "%CB_B%" exit /b 1
exit /b 0

:strlen
:: %1 = var name holding the string, %2 = var name to receive its length.
:: Deliberately a FOR /L loop, NOT a goto loop: `goto` inside a `call`ed
:: subroutine discards the return context, so the trailing `exit /b` would
:: terminate the whole script instead of returning. FOR preserves it.
setlocal enabledelayedexpansion
set "S=!%~1!"
set "L=0"
for /L %%N in (1,1,32) do (
    if defined S (
        set "S=!S:~1!"
        set /a "L+=1"
    )
)
endlocal & set "%~2=%L%"
exit /b 0
