# R02 — Readiness (B1)

Role: R02 builder (B1 readiness + B2 build)
Packet and contract revision: R02-spans.md / TL-QN-2026-09-12.1
Date (UTC): 2026-09-12
Base commit: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` (verified via `git rev-parse HEAD`)

## 1. Ownership and isolation

Owned (create/modify only these):

- `src/trace_lite/source/segmentation.py` (new), `src/trace_lite/source/views.py` (new)
- `src/trace_lite/source/__init__.py` (additive export extension only; `service.py` NOT rewritten)
- `src/trace_lite/obsidian/parser.py` (adapter edits only: CRLF-tolerant frontmatter,
  byte-offset accounting, frontmatter mapping fields; existing `parse_note` behavior kept)
- `src/trace_lite/store/` additive migration-002 step only (DDL for `evidence_span` +
  `segmentation_view` in `schema.sql`, new step in `migrate.py`; no extraction/graph/
  holon/embedding tables)
- `tests/test_source_spans.py`, `tests/test_parser_fidelity.py` (new, durable)
- `evidence/query-native/R02/**`

Prohibited and untouched: `src/trace_lite/api/`, `router/`, `filing/`, `cordis/`,
`store/` files other than the additive migration-002 step, `benchmarks/`,
`evidence/query-native/R00/`, `evidence/query-native/R01/`, `docs/`, all pre-existing tests.

## 2. Base and prerequisite check (R01 VERIFIED)

- `git status --short` shows only the R01 owned set (untracked `src/trace_lite/source/`,
  `src/trace_lite/store/{generations,migrate,outbox}.py`, `tests/test_migration.py`,
  `tests/test_store_lifecycle.py`, `evidence/query-native/`; modified coordinator
  prompt-text + `store/{__init__,schema.sql}`) on top of base `0df044b`. This is the
  expected R01 base, not foreign drift. No other writer is active (disjoint owned set).
- `evidence/query-native/R01/delivery.md` present (R01 VERIFIED claim); R01
  `migration-001` tables are the frozen base — migration-002 is purely additive.
- `evidence/query-native/R00/fixtures.json` DEV families + BEIR corpus paths present;
  `beir-scifact` raw `corpus.jsonl` (5183 docs) is locally readable for the
  spans/source distribution measurement (bounded sample only).
- Baseline suite green on this tree: `73 passed`
  (`.venv/bin/python -m pytest tests/ -q`, exit 0, ~16 s). No existing test modified.

## 3. Byte-offset risk notes

- UTF-8 half-open byte ranges into the retained raw payload. All span boundaries are
  computed from line-start byte offsets plus in-string byte lengths, so Unicode/emoji
  (multi-byte), CRLF (`\r\n` preserved verbatim in span text), and frontmatter
  stripping (body spans carry `body_byte_base = body_byte_offset`, recorded in the
  view `signal_json` mapping) stay byte-exact. A normalized-text offset never claims
  raw-byte identity: span text is defined as the verbatim raw slice, no normalization.
- Parser gap found: `FRONTMATTER_RE` requires LF (`\n`) line endings, so CRLF notes
  misparse (frontmatter leaks into body). Adapter fix: CRLF-tolerant regex + explicit
  `frontmatter_span` / `newline` / mapping fields on `ParsedNote` (defaults only;
  existing callers and the C01 obsidian test behavior unchanged for LF input).
- Current `span_of` uses `len(raw[:char].encode())` per match (O(n·m) but correct);
  retained, with byte-slice assertions in the new fidelity tests as the guard.
- Structural baseline only: paragraphs, bullets, ≤1200-char cap with sentence-boundary
  split, atomic code fences + markdown tables, conversation-turn/log-event spans where
  applicable. No ML/perplexity boundary detection (R08 non-goal). Unclosed fence →
  rest-of-document atomic span (never an orphaned fragment). Over-cap fence/table
  stays atomic (structural integrity wins over the cap; recorded as a limitation).
- Repeated identical text: spans are keyed by ordinal occurrence, so each occurrence
  gets a separate span ID by construction.
- Failure path: any segmentation exception yields a `status='partial'` view row with
  zero spans for the retained revision — never fabricated offsets.
- Minor packet drift noted (not blocking): packet cites ARCH §3 / EVALUATION §3, but
  the frozen docs carry the evidence_span + segmentation_view rows in ARCH §2, local
  graph construction in ARCH §4, and gate 1 in EVALUATION §4. Building to the actual
  frozen sections.

## 4. Migration-002 shape (additive-only rollback)

- `schema.sql`: `CREATE TABLE IF NOT EXISTS evidence_span` + `segmentation_view` +
  indexes. No version-row insert in the static DDL (existing migration tests assert
  `MAX(schema_version) == 1` after migration-001; a static version-2 row would break
  them — verified by reading `tests/test_migration.py`).
- `migrate.py`: new `migrate_spans_002(db_path)` step (snapshot `.bak-002` via
  `VACUUM INTO` before any write, apply DDL, record `schema_version = 2`, verify;
  rerun-safe). Fresh `Database` opens pick up the tables via `schema.sql` directly.
- Rollback = additive-only: close handles, restore `.bak-002` over the live file
  (or simply keep using the pre-002 file — no existing table is altered or dropped).
- Escalation triggers: schema contradiction with ARCH §2, any existing-test
  regression, or byte fidelity < 100%.

## 5. Command validation

- Interpreter: `.venv/bin/python`, Python 3.11.15. No new dependency (stdlib only).
- New tests: `.venv/bin/python -m pytest tests/test_source_spans.py
  tests/test_parser_fidelity.py -v`; full suite `.venv/bin/python -m pytest tests/ -q`.
- BEIR sample read from the R00-recorded path (no network; bounded to a prefix sample).

## 6. Verdict

NOT BLOCKED. Proceeding to B2 build.
