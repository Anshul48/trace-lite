# P06 Delivery — Obsidian Vault Sync Engine & REST API

- Packet: `docs/builds/filing-cabinet/packets/P06-obsidian-vault-sync-engine.md`
- Candidate commit: (this commit — Wave 4)

## Files
- `src/trace_lite/obsidian/parser.py` — frontmatter (tags/aliases/type, block+inline lists),
  inline `#tags`, `[[target|label]]` wikilinks, all with UTF-8 byte spans; `facet_hints`
  maps tags→Topics, wikilinks→Entities, type→Types, folder→Projects, vault→Sources.
- `src/trace_lite/obsidian/watcher.py` — poll-based `DebouncedWatcher`: per-path 500ms buffer,
  `scan_once`/`notify_changed`, background thread, sync counter.
- `src/trace_lite/api/app.py` — FastAPI `create_app(db, vault)`: `GET /api/health`,
  `GET /api/status`, `POST /api/query` (3-tier citations), `POST /api/sync` (idempotent
  re-atomization + facet remap + router re-warm). Single connection opened with
  `check_same_thread=False`, serialized by a guard lock (cross-thread ASGI fix).
- `src/trace_lite/cli.py` — `tl serve --port/--db/--vault` (port via `TRACE_LITE_PORT`, 8420).
- `src/trace_lite/store/database.py` — additive `check_same_thread` passthrough (default safe).
- `pyproject.toml` — declared `numpy`, `fastapi`, `uvicorn[standard]` (all in venv).
- `tests/test_obsidian_sync.py` — 3 durable tests (C01/C02/C03).

## Verification (builder-run)
- `.venv/bin/python -m pytest tests/test_obsidian_sync.py tests/test_store.py -q` → **8 passed**.
- 1 repair, real product bug: SQLite thread-affinity under ASGI → cross-thread mode + guard.
