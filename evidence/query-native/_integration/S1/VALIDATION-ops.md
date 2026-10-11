# S1 native validation — operator runbook (Windows) + WSL record

## WSL record (this session, 2026-09-12)

- Harness: `.venv/bin/python benchmarks/blackbox_native_check.py --port 8765 --workdir /tmp/s1work`
  → `PASS (15 steps ok, 1 gap(s))`, exit 0. Passing run dir:
  `evidence/query-native/_integration/S1/runs/s1-20260912T143826Z-58a2d3c1/`
  (`platform: Linux` in `run.start`; `crash_window.log` shows `SIGKILL`).
- Wrapper default: `.venv/bin/python -m pytest tests/test_s1_blackbox.py -q`
  → `5 skipped` (gate: no `--native-run`).
- Wrapper live (same-machine server + seeded a.md/b.md):
  → `5 passed`.
- Full suite: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
  → `261 passed, 6 skipped` (5 S1 gate-skips + pre-existing
  `test_learned_retrieval`/`test_cordis` conditional skips). Suite green.

## Native Windows runbook (operator, EXACT commands)

Run from the repo root (`C:\...\trace-lite`). Native interpreter only —
never `.venv` (Linux binaries). Expected wall time ≈ 4–8 min
(watcher polls + crash-drain wait + ~45 s load step).

1. Full black-box gate (own servers on 127.0.0.1:8765, `taskkill` crash path):
```bat
.venv_win\Scripts\python.exe benchmarks\blackbox_native_check.py --port 8765 --workdir %TEMP%\s1work
```
   Expected: `[s1-...] PASS (15 steps ok, 1 gap(s))`, exit 0. Run
   artifacts auto-copy to `evidence\query-native\_integration\S1\runs\<run-id>\`
   (`crash_window.log` must show `"tool": "taskkill /F"`).

2. Thin wrapper, default (must skip, never starts servers):
```bat
.venv_win\Scripts\python.exe -m pytest tests\test_s1_blackbox.py -q
```
   Expected: `5 skipped`.

3. Thin wrapper, live (start a seeded server FIRST in a second prompt):
```bat
mkdir C:\Temp\s1wrap\vault
set TL_S1_DB=C:\Temp\s1wrap\s1.db
set TL_S1_VAULT=C:\Temp\s1wrap\vault
set TL_S1_PORT=8771
set PYTHONPATH=%CD%\src
.venv_win\Scripts\python.exe -c "import os;from trace_lite.api import create_app;import uvicorn;app=create_app(os.environ['TL_S1_DB'],os.environ.get('TL_S1_VAULT') or None);uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('TL_S1_PORT','8771')),log_level='warning')"
```
   Then seed two docs (third prompt or `curl`), then:
```bat
.venv_win\Scripts\python.exe -m pytest tests\test_s1_blackbox.py -q --native-run --native-url=http://127.0.0.1:8771
```
   Expected: `5 passed`. Stop the server with `Ctrl+C` afterwards.

4. Full-suite green gate (unchanged code paths):
```bat
.venv_win\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider
```
   Expected: `261 passed, 6 skipped` (same skip composition as WSL).

## Notes

- No WSL-only assumptions in the harness: same file, same flags; the
  only platform branch is the kill primitive (`os.name == "nt"` →
  `taskkill /F /PID`, else `SIGKILL`).
- If the lab sets `http_proxy`/`https_proxy`, localhost calls still
  bypass them (harness/wrapper install a no-proxy `urllib` opener).
- Ports must be free before step 1 (`8765`) and step 3 (`8771`).
