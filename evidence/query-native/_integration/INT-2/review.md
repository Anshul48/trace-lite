# INT-2 — Independent integration review (served retrieval + dossier integrity)

Contract TL-QN-2026-09-12.1. Verifier scope: path_matrix rows 5, 6.
Builder claims checked on the served path only (real `TestClient` + real
DB files under `/tmp`, `.venv/bin/python` 3.11.15). Overall verdict: **PASS**
(row 5 VERIFIED; row 6 VERIFIED with one minor non-blocking finding).

## Per-arrow verdicts (vs `path_matrix.md`)

- Row 5 (retrieval/index publication — served ANN): **VERIFIED**.
  Served `/api/query` on a novel 3-doc corpus returns `route=vector`,
  `vector_generation_id == generation_id`, `vector_adequate is True`,
  premise anchored, every anchor route-labeled; pin stable across repeat
  queries. Gibberish abstains (`insufficient_evidence`, no anchors, named
  route). Unattached router on the same file serves named legacy fallback
  (`lexical`/`beam`/`hybrid`, `vector_generation_id None`) — fallback
  stays selectable. Per-reader in-memory build cost is disclosed
  (`delivery.md` §4: O(corpus) k-means per generation swap) and bounded
  in code (`SERVED_VECTOR_MAX_ATOMS = 20000`, empty-corpus detach to named
  fallbacks; `cascade.py:24,146-175`). Task-quality spot checks through
  the served path pass here (relevant-first on novel GC/arena vs
  fsync/WAL docs) and in the battery (6-doc probe, relevant-first,
  pinned, all citations resolved, relations revision-proven).
- Row 6 (query dossier + citations): **VERIFIED with finding F1**.
  Served dossiers resolve citations server-side (endpoint passes `conn`,
  strips caller-claimed namespace/access/version/bytes — confirmed by
  inspection of the `/api/dossier` handler). Independent re-read of raw
  revision bytes confirms byte-exact windows, valid UTF-8, no bare
  `atom_self`, unicode/CRLF exact. Replacement supersedes via API (new
  revision cited, old revision/text never cited as current, all citations
  current). Delete retracts from served results, history retained.
  Proven-wrong atom/span texts → `citation_invalid`, excluded and named.
  Unknown spans → `atom_self:degraded`, surfaced, conservative support
  (`supported_count 0`, `insufficient`). Out-of-scope candidates withheld
  with relations/disagreements dropped as named `relation_filtered` /
  `disagreement_filtered` omissions; served relations all carry
  `revision_id` inside the resolved selection set (no leak from invisible
  revisions on the served path). Historical retained only with explicit
  `current_only=False`. Atomic-group partition named, no completeness
  claim. Checker is `lexical-heuristic-v1` with "not NLI" note; no
  support state claims semantic verification from lexical overlap.

## Battery tautology check

`tests/test_served_path_integration.py` is **not tautological**: citation
correctness is asserted against independently re-read raw revision payloads
via fresh `Database` connections (`_payload`, `_current_revisions` — byte
containment, UTF-8 validity, revision currency), not the endpoint's own
fields. Limitation noted: `route=vector` is self-reported by the cascade,
mitigated by generation-pin equality, premise anchoring, fallback
contrast, and independent replication with novel fixtures in this review
(26/29 own-probe checks pass; the 3 non-passes are diagnosed below, none
product defects except F1).

## Findings

- F1 (minor, non-blocking, library-only): a span-anchored candidate naming
  a nonexistent `revision_id` with correct span/text is silently corrected
  — resolved `r02_excerpt`, valid, undegraded, citing the true revision
  with no omission (`_resolve_candidate`, `dossier.py:318-326`, overwrites
  the claim without comparison). The output citation itself is byte-true,
  and the served endpoint never forwards caller revisions (unreachable via
  HTTP), but the brief's "wrong revision → invalid/degraded, never
  silently accepted" clause is not fully met. Recommended: name a
  `citation_revision_mismatch` omission (or degrade) when claimed ≠
  resolved revision. The battery never probes this case (it covers
  wrong-text and unknown-span only).
- F2 (hygiene, non-blocking): battery `_flush` appends to
  `citation_battery.log` and accumulates `served_retrieval.json` steps on
  every run — verification reruns (including this review's) duplicate log
  lines and grow the JSON. Recommend truncate-on-first-write or run-id
  scoping. This review's suite runs extended both files; content left as
  generated.
- Own-probe `trimmed_interior_nonzero` non-pass is a probe artifact, not a
  product defect: small fixtures fit the tight budget without excerpting
  (`is_excerpt False`); trimming is covered by the battery's LONG_DOC
  test with independent raw-byte checks.

## Execution scope (all observed this session)

- `pytest tests/test_served_path_integration.py -q` → 11 passed, exit 0.
- `pytest tests/ -q` (full suite, unmodified) → 251 passed, exit 0.
- `pytest tests/test_write_path_integration.py -q` (INT-1, intent
  untouched) → 3 passed, exit 0. No INT-1 regression.
- Own probe `/tmp/int2v_probe.py` (fresh /tmp DBs, novel fixtures) →
  26/29 checks pass, exit 0 on passes; 2 non-passes diagnosed as probe
  artifact, 1 as F1 (reproduced with exact-case rerun).
- Scope check: INT-2-window writes (mtimes 05:10+) are `router/cascade.py`,
  `api/app.py`, `cordis/dossier.py` (new), `test_served_path_integration.py`
  (new), `INT-2/` evidence — all inside the allowed set
  (`cordis/__init__.py` dossier re-export is minimal adapter glue).
  Write-path/migration/lock/freshness files (`engine.py`, `migrate.py`,
  `schema.sql`, `outbox.py`, `generations.py`, `encoder.py`,
  `index_adapter.py`, `compiler.py`, `models.py`) all predate the INT-2
  window (R01–R07/INT-1); the app.py write-path region contains no
  vector/ANN/dossier wiring. No other file modified by this review.

## Next action

INT-2 **PASS** — served read path provably uses the R03 ANN branch pinned
to the serving generation with honest labels, and dossiers carry
revision-provenance citations with enforced visibility and honest
support. Carryovers: F1 fix (one-line-class omission on revision
mismatch), F2 battery-log hygiene, delivery §4 items (published-segment
ANN reads, served historical discovery, retract→invalidate crash window —
all unchanged and out of INT-2 scope).
