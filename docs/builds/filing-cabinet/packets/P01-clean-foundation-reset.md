# P01 — Clean Foundation Reset & Modern Packaging

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](../../query-native/PROJECT.md), [execution plan](../../query-native/EXECUTION.md), and [state](../../query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Status: READY
Kind: infrastructure / cleanup
Contract revision: 2026-09-11.2
Owner/session: Builder Session 1

## Outcome
Execute the legacy prototype burial and initialize a clean, production-grade foundation. Exercising the user's explicit freedom directive, all obsolete prototype files, LanceDB scripts, and RAPTOR modules are archived to `archive/v1-legacy-scaffold` (preserving `obsidian-plugin/`). A clean `pyproject.toml` (Python 3.11+, Pydantic 2, Typer, Rich, SQLite), baseline `src/trace_lite/`, and test scaffold are established on `main`.

Concrete Example: Builder runs `./bury_and_reset.sh`. Obsolete exploratory files are purged, legacy history is safely tagged on `archive/v1-legacy-scaffold`, and `pytest tests/test_scaffold.py` passes immediately with `tl version` printing `trace-lite 0.2.0`.

Failure Case: If git user name/email are unconfigured, `bury_and_reset.sh` automatically configures local identity so commit succeeds without stalling.

## Inputs and dependencies
- Target directory: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite`
- Script: `bury_and_reset.sh`
- Prerequisites: None (Wave 1 initial packet).

## Scope and interfaces
- Owned files:
  - `bury_and_reset.sh`
  - `pyproject.toml`
  - `README.md`
  - `src/trace_lite/__init__.py`
  - `src/trace_lite/cli.py`
  - `tests/__init__.py`
  - `tests/test_scaffold.py`
- Preserved directories:
  - `obsidian-plugin/` (companion client)
  - `docs/builds/filing-cabinet/` (planning records)
- Non-goals: Implementing storage engine (deferred to P02).

## Suggested approach
1. Verify `bury_and_reset.sh` preserves `obsidian-plugin` and `docs/builds/filing-cabinet/`.
2. Execute `./bury_and_reset.sh`.
3. Verify git branches: `archive/v1-legacy-scaffold` exists and is tagged `v1.0-scaffold-archived`.
4. Verify tests pass with `tests/test_scaffold.py`.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C01 | Legacy prototype safely archived | `git branch -a` shows `archive/v1-legacy-scaffold` | Git branch output | Clean working tree |
| C02 | Clean modern pyproject.toml | `pytest tests/test_scaffold.py` passes | Pytest stdout PASS | Python >= 3.11 |
| C03 | CLI functional | `tl version` prints `trace-lite 0.2.0` | CLI execution output | Exit code 0 |

## Execution and evidence
- Execution commands:
  ```bash
  cd /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite
  chmod +x bury_and_reset.sh
  ./bury_and_reset.sh
  /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/pytest tests/test_scaffold.py
  ```
- Evidence directory: `evidence/P01/`.

## Recovery and escalation
- Rollback: `git checkout main` or restore from archive branch.
