# P06 — Obsidian Vault Synchronization Engine & REST API

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](../../query-native/PROJECT.md), [execution plan](../../query-native/EXECUTION.md), and [state](../../query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Status: READY
Kind: implementation
Contract revision: 2026-09-11.2
Owner/session: Builder Session 6

## Outcome
Implement seamless bidirectional synchronization between Obsidian vaults and Trace-Lite:
- Filesystem watcher monitors vault markdown files with a 500ms debounce buffer to eliminate write thrashing during active typing.
- Extracts YAML frontmatter (`tags`, `aliases`), inline `#tags`, and `[[wikilinks]]`.
- Automatically maps vault folder structures and tags into Hearst multi-parent facets.
- Exposes loopback REST API on port 8420 (`POST /api/query`, `POST /api/sync`, `GET /api/status`, `GET /api/health`) compatible with the preserved companion `obsidian-plugin/`.

Concrete Example: User creates a note `Note.md` in Obsidian with `#gpu` and `[[FlashAttention]]`. Within 500ms, the watcher triggers atomization, maps `#gpu` to `Topics:GPU`, records the wikilink edge, and updates the local FTS5 index. A query from the Obsidian plugin on `:8420` returns the note citation in < 15ms.

Failure Case: Rapid keystrokes generating 20 file-save events within 200ms must be coalesced by the 500ms debounce buffer into exactly 1 re-indexing pass, preventing SQLite concurrency locks.

## Inputs and dependencies
- Required prior packets: P03, P04.
- Client: Companion TypeScript client at `obsidian-plugin/main.ts` (configured for `http://127.0.0.1:8420`).
- Target directory: `src/trace_lite/obsidian/`, `src/trace_lite/api/`.

## Scope and interfaces
- Owned files:
  - `src/trace_lite/obsidian/__init__.py`
  - `src/trace_lite/obsidian/parser.py`
  - `src/trace_lite/obsidian/watcher.py`
  - `src/trace_lite/api/__init__.py`
  - `src/trace_lite/api/app.py`
  - `tests/test_obsidian_sync.py`
- Endpoints:
  - `POST /api/query`: 3-tier search returning citations and note paths.
  - `POST /api/sync`: Force sync trigger for a vault path.
  - `GET /api/status`: Health check, active index status, and atom count.
  - `GET /api/health`: Loopback liveness probe.

## Suggested approach
1. In `parser.py`: Use regex/yaml parser to extract frontmatter tags, inline `#tags`, and `[[link|label]]` syntax with byte spans.
2. In `watcher.py`: Async file watcher using `asyncio` loop with a `threading.Timer` or dictionary debounce of 500ms per file path.
3. In `api/app.py`: FastAPI / Starlette application exposing port 8420, configurable via `--port` or `TRACE_LITE_PORT`.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C01 | Markdown frontmatter & wikilink parsing | Note parsed into canonical atoms with wikilink edges | Parser test pass | Exact byte offsets preserved |
| C02 | Debounced file sync | 10 rapid file writes trigger exactly 1 re-indexing pass | Watcher test pass | Zero SQLite write conflicts |
| C03 | REST API query response | `POST /api/query` returns top-k matching notes with snippet citations | API integration test pass | Response time < 50ms on port 8420 |

## Execution and evidence
- Execution command:
  ```bash
  /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/pytest tests/test_obsidian_sync.py
  ```
- Evidence directory: `evidence/P06/`.

## Recovery and escalation
- Safe rollback: `git checkout HEAD -- src/trace_lite/obsidian/ src/trace_lite/api/ tests/test_obsidian_sync.py`
