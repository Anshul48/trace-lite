# P02 Delivery — Canonical Atom Filing Storage & Active WAL Governor

- Packet: `docs/builds/filing-cabinet/packets/P02-canonical-atom-filing-storage.md` (rev 2026-09-11.2)
- Candidate commit: (this commit — store engine + tests)

## Files
- `src/trace_lite/store/schema.sql` — `atom` (AUTOINCREMENT, BLOB hash, byte offsets),
  append-only `events`, `facets`/`memberships` (forward-created for P03), `fts_atoms` FTS5
  with `content='atom', content_rowid='id', tokenize='porter'` (no text duplication).
- `src/trace_lite/store/database.py` — `Database`: WAL mode, `synchronous=NORMAL`,
  `busy_timeout=5000`, FK enforcement; `insert_atom`/`get_atom`/`find_atoms_by_hash`
  (hex or bytes), `bulk_ingest` (single txn + one post-commit governor check),
  `insert_event`/`list_events` (auto `stream_seq`), `search_fts` (BM25-ranked),
  `delete_atom`, `rebuild_fts`, `checkpoint_now`.
- `src/trace_lite/store/governor.py` — `WalGovernor(threshold=5000)`: counts committed
  docs, fires `PRAGMA wal_checkpoint(PASSIVE)` post-commit only, keeps `history`.
- `tests/test_store.py` — 5 durable tests (C01/C02/C03 + CRIT-04 guard + events).

## Verification (builder-run)
- `.venv/bin/python -m pytest tests/test_store.py -q` → **5 passed in ~1s** (attempt 1, no repair).
