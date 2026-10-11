# R07 — Readiness (B1)

Role: R07 builder (B1 readiness + B2 build)
Packet and contract revision: R07-dossiers.md / TL-QN-2026-09-12.1
Date (UTC): 2026-09-12
Base commit: `0df044b` (verified via `git log --oneline -1`)
Interpreter: `.venv/bin/python` from repo root (Python 3.11.15), SQLite 3.53.1 + FTS5.

## 1. Ownership and isolation

Owned (create/modify only these):

- `src/trace_lite/cordis/compiler.py` (objective-text ranking fix + explicit
  budget failures + anchored excerpts)
- `src/trace_lite/cordis/dossier.py` (new: dossier context models + compiler)
- `src/trace_lite/cordis/__init__.py` (additive exports only)
- `src/trace_lite/api/app.py` — ADDITIVE ONLY: new `POST /api/dossier` route +
  request model. R06 owns the lock/WAL/generation/status logic
  (`write_lock`, `get_reader`/`route_pinned`, `publish_filing_generation`,
  `freshness`, `/api/status` keys): none of those bodies will be touched.
- `src/trace_lite/cordis/models.py` — ADDITIVE ONLY if needed: optional
  `status`/`omissions` fields with defaults on `CompiledContext`
  (not in the conformance case map; formal-dump construction keeps working).
- `tests/test_context_compiler.py`, `tests/test_dossier_compilation.py`
  (new, durable)
- `evidence/query-native/R07/**`

Prohibited and untouched: `store/`, `graph/`, `router/`, `source/`,
`filing/`, `cordis/gate.py` (adapter-honesty boundary — read, not rewritten),
`obsidian/`, pre-existing tests, `benchmarks/`, R00–R06 evidence, `docs/`.
No other writer active (single-writer dispatch; `git status` shows only the
R01–R06 integrated sets plus this packet's new files).

## 2. Base and prerequisite check (R06 INTEGRATED)

- Base suite green on this tree before R07 edits:
  `.venv/bin/python -m pytest tests/ -q` → **197 passed**
  (one pre-existing Starlette deprecation warning only).
- R06-prereq check (frozen contracts — consumed, not changed):
  `evidence/query-native/R06/delivery.md` freezes the writer `write_lock`,
  per-thread generation-swapped readers, in-transaction `filing` generation
  publication, and honest `freshness` (`semantic_pending` metadata, never
  stale-as-complete). R07 reads through `route_pinned`/`freshness` and pins
  dossier snapshots to the same generation ids; no app.py logic bodies change.
- R05-prereq check (frozen holon contract, delivery.md §Frozen holon
  contract): `holon_state`/`holon_version`/containment DAG/`dependency`
  reverse index; opposing roles unmerged; membership never agreement. R07
  builds TRANSIENT cross-source virtual holons at query time only
  (`query_dossier` kind, derived status + lineage) and never writes holon
  tables.
- R04-prereq check (frozen graph contract): local `rel_type` vocabulary is
  asserted-not-validated; opposing assertions coexist unmerged;
  `unresolved` rows stay unbound. Dossier retains qualification edges and
  explicit disagreements verbatim.
- R02-prereq check (span contract): `excerpt_span(conn, span_id)` is the
  byte-citation oracle (EVALUATION gate 1). Dossier evidence selections
  carry `(revision_id, span_id, start/end_byte)` or `(doc_id, atom
  start/end_byte)` and every citation is verified before inclusion;
  corruption routes to B4, never to silent inclusion.
- R00 fixtures present (`evidence/query-native/R00/fixtures.json`):
  `conditions` batching pair (C7-2/C7-3 core), `negation`, `entity-ambiguity`
  (both sides surface), `unfiled`, `off-topic-abstention` (insufficient, not
  fabricated support).

## 3. Cordis schema / tokenizer facts

- Consumer tokenizer: `estimate_tokens(text) = max(1, len(text) // 4)`
  (`src/trace_lite/cordis/models.py`); verified live:
  `tok("hello world") == 2`, `tok(400 chars) == 100`. Dossier token
  accounting uses this function over content PLUS citation wrappers and
  relation metadata (wrappers counted, not free).
- Budget ceiling: `MAX_CONTEXT_TOKENS = 3500`; `TokenBudget` validator
  rejects `system + mandatory + goal + allocated > max`. Reservation order
  in R07: mandatory constraints + goal context FIRST, evidence with the
  remainder.
- Schema conformance (verified live this session): the formal Desktop spec
  at `research_specifications/schemas/interfaces.py` is reachable AND
  importable; authority ranks equal (`test_conformance_against_formal_interfaces`
  green at base). Conformance is recorded as schema-shape only — it is NOT
  harness loading/use, and the dossier adapter report states both
  separately (Cordis honesty boundary).
- `gate.py` Tier-1 (`execute_tier1_nli`) is a deterministic lexical screen
  (`_contradicts` keyword guards). R07 keeps it labeled **heuristic** in
  every dossier/API surface: lexical overlap never marketed as NLI, memory
  provenance never authorizes execution, generated synthesis always
  derived-status with lineage.

## 4. Known baseline defects to fix (owned files only)

1. `compiler.py` lines ~92–98 derive `goal_terms` from the `goal_id`
   string — the packet's legacy disconnect. Fix: rank against
   `goal_state['objective']` text; `goal_id` never feeds ranking
   (kept as an opaque label; legacy callers passing objective-as-id keep
   working with that value treated as the objective only when no
   `goal_state` is supplied).
2. Mandatory reservation silently drops invariants
   (`if used + cost > budget... and mandatory: break`). Fix: mandatory
   overflow → explicit `insufficient_budget` status with every omitted
   invariant named; zero silent drops.
3. Evidence packing uses arbitrary prefixes (`content[:cost*4]`). Fix:
   query-anchored, source-linked excerpts with recorded byte ranges; a
   complete evidence group that cannot fit → explicit coverage failure.

## 5. Rollback

Code-only revert: `git checkout -- src/trace_lite/cordis/
src/trace_lite/api/app.py` plus deletion of the two new test files. No DDL
change is made anywhere (no migration, no table/column change), so there is
no data migration to roll back. Evidence under
`evidence/query-native/R07/` is additive. `/api/dossier` is a new route;
removing it restores the exact R06 API surface.

## 6. Verdict

NOT BLOCKED. Proceeding to B2 build. Escalation triggers armed:
existing-test regression, citation corruption, silent-drop pressure.
