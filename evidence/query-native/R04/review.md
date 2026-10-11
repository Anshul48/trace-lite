# R04 — Independent review (B3 verifier)

Role: R04 independent verifier / 2026-09-12
Packet and contract revision: R04-local-graphs.md / TL-QN-2026-09-12.1
Candidate: git HEAD `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` + R01+R02
(VERIFIED) + concurrent R03 set + R04 set under review.
Environment: `.venv/bin/python` 3.11.15 (repo root), SQLite + FTS5.

## 1. Evidence

- C4-1 Relational fidelity — VERIFIED. `relation_fidelity.json` (read, not
  regenerated) records 25/25 role checks, accuracy 1.0, zero invented terms,
  opposing pair preserved, ambiguity preserved. Independent /tmp probes
  (14/14 pass, exit 0) re-confirmed on the R00 verbatim passages: cond-a
  direction Batching→throughput, positive polarity, `under low contention`
  condition, parenthetical kept as verbatim qualifier (never a causal claim);
  cond-a/cond-b coexist as separate positive/negative rows with no
  supports/contradicts/causes edge; negation polarity + `during a write`
  temporal scope; unless/only qualifiers retained with qualifies edges.
- C4-2 Provenance anchoring — VERIFIED. `provenance_check.log` records
  18/18 relations resolving, 0 orphan/unanchored, every run ok=True.
  Independently re-confirmed: every relation of the cond-a/cond-b runs
  resolves via the R02 `excerpt_span` primitive to real bytes, and a bogus
  `subject_mention_id` insert is rejected with `sqlite3.IntegrityError`
  (real FKs to `evidence_span`/`entity_mention`/`assertion`, confirmed in
  `PRAGMA foreign_key_list`), so orphan edges are unwritable, not merely
  detectable.
- C4-3 Condition retention — VERIFIED. `condition_test.json` records
  `condition_retained=true`, `qualifier_edge_exists=true` with
  `condition_text='under high contention'` on the restores frame plus a
  qualifies edge to the assertion. Independently re-confirmed via probe.
- C4-4 Extraction fallback — VERIFIED. `fallback_test.log` records
  partial status with 0 unhandled exceptions. Independently re-confirmed:
  hostile battery (None/42/bytes/dict-without-span/empty) yields
  `status=partial` with surviving valid assertions; empty input yields
  complete; nothing raises.

Test runs (recorded exit codes): `tests/test_local_graph.py` +
`tests/test_assertion_extractor.py` → 27 passed, exit 0. Full suite
`tests/` → 155 passed, exit 0 (matches builder claim; only pre-existing
Starlette deprecation warning). Tautology inspection: fidelity expectations
are hardcoded literal strings (subjects, predicates, conditions, units),
not the extractor's own output; the "zero invented terms" forbidden-word
subcheck (`cache`/`bouncing`, absent from all inputs) is weak in isolation
but is backed by substantive checks — syscall-parenthetical placement,
off-topic salad yielding zero frames, and similarity never emitting edges.

Migration-003 (scratch copies under /tmp, repo untouched): on a
1→2-migrated file the 003 step changes nothing except adding the
`schema_version=3` row (all pre-existing table columns and row counts
byte-identical); on a legacy-shaped file with the six tables dropped, 003
recreates exactly `assertion`, `assertion_argument`, `dependency`,
`entity_mention`, `extraction_run`, `relation` and records version 3;
rerun reports `migration_version=3, observed_version=3`; `.bak-003`
snapshot written; pre-003 writers (`S.publish`) work after migration.

Non-goals confirmed by inspection: no dedup/ontology/cross-document code
(zero matches for dedup|ontology|embedding|llm in `graph/`; `REL_TYPES`
has no supports/contradicts/causes); no open-domain extraction (verbs
outside the frozen vocabulary yield no frame; 200-sentence/50k-char caps);
bounded windows (one span + 1-hop heading context; long-range links only
via top-3 token-overlap candidates with exact-unique-match binding, and
asserted edges never cross spans).

Frozen graph-table contract for R05: documented in `delivery.md`
§Frozen graph contract and verified accurate against `schema.sql` —
six tables with the exact column sets dumped during verification, plus
the R05 constraints (add-only, asserted-not-validated semantics, opposing
rows stay unmerged, unresolved stays unbound, keep writing `dependency`
rows). Sufficient for a reconciler.

## 2. Verdict

PASS.

## 3. Findings

No blocking findings. Notes (non-blocking): (a) the forbidden-word half
of the no-invention check is trivially satisfiable — keep the qualifier-
placement and zero-frame checks as the load-bearing ones; (b) fresh
`Database` files already contain the six graph tables via static DDL, so
on fresh files 003 is a version-row step only — by design, and the
legacy-recreate path was verified separately.

## 4. Execution scope

R04 delta is confined to its owned set: new `src/trace_lite/graph/`
(frames/extractor/store), additive migration-003 step (`schema.sql`
245 insertions / 0 deletions; `migrate_graph_003` + backup helper in
`store/migrate.py`), new `tests/test_local_graph.py` +
`tests/test_assertion_extractor.py`, `evidence/query-native/R04/`.
Zero cross-references either way between `graph/` and `router/`;
R00–R03 evidence, benchmarks, and all pre-existing tests unmodified by
R04 (remaining worktree delta belongs to the VERIFIED R01/R02 and
concurrent R03 sets). No file other than this review was modified by
the verifier; probes ran from /tmp.

## 5. Next action

R04 is clear to be marked VERIFIED; R05 may build against the frozen
six-table contract in `delivery.md` §Frozen graph contract.
