# S0 capability ledger (2026-09-12) — controlling baseline for S1–S6

Candidate: HEAD `0df044b`, manifest `evidence/query-native/_integration/candidate_manifest.json`
(92 files hashed; HEAD is base only — hashes are the identity).
Status vocabulary: PROVEN-served | VERIFIED-component | DIAGNOSTIC | PARKED | UNQUALIFIED.
Nothing below may be re-labeled without a new executable check on the pinned candidate.

## PROVEN on the served path (INT-1/INT-2, independent probes + suite 261)

- Atomic revisioned writes: POST /api/ingest|sync → source_revision + idempotency
  + outbox jobs; edits supersede, replays dedupe. Evidence: _integration/INT-1/.
- Projections on writes: spans, assertions/relations, holon memberships minted
  per revision; corpus tracks current atoms across restart. Same.
- API-driven invalidation (R06-F4): premise edit auto-stales dependents; deletes
  retain history. Same.
- Honest freshness: per-revision coverage checklist; resume pending→complete.
  Same.
- Served ANN branch: route=vector, generation-pinned, named fallback selectable.
  Evidence: _integration/INT-2/.
- Revision-anchored dossier citations resolved server-side against raw bytes.
  Same. Known minor: wrong CLAIMED revision silently corrected (byte-true output).

## VERIFIED at component level (library tests, not served-path proof)

- R00 baseline reproduction + frozen workload/gates (R00/).
- R01 atomic store/outbox/migration, R02 spans/views, R04 graphs, R05 holons,
  R06 incremental/freshness intent (ratio 1.06x, absolute gate deferred),
  R07 dossiers/Cordis. Evidence: evidence/query-native/Rxx/ + reviews.

## DIAGNOSTIC (executed, characterized, not qualified)

- Integrated 2k + 10k: exact gates green; quality/publication budgets unmet;
  NFCorpus shortfall = fallback-representation constraint (R09/nfcorpus_constraint.md).
- Native legacy ladder to 250k (1M memory-gate failed, halt proven).
  Evidence: R09/, _integration/WIN/native_ladder_20260912.md.

## PARKED (honest negative/inconclusive; revival conditions recorded)

- R08 predictive segmentation/repair: negative deltas, severance, cost.
- INT-3 learned weights: quality proven (+0.036 scifact / +0.108 nfcorpus paired)
  but serving cost fails (7–13 min rebuild/swap). Needs S4 background publication.

## UNQUALIFIED (no claim permitted)

- Any rung as production qualification; 100k+ integrated; S1 native
  correctness gate; S2 bounded reads; S3 autonomous maintenance; S4 persistent
  vectors; S5 semantic utility; memory budget at any scale (native RSS curve
  recorded, no budget set).

## Controlling evaluation revision

R00 workload.json + gates.json (TL-QN-2026-09-12.1) → R09 contract_amendments.md
→ SUPPLEMENT_S_BINDINGS_2026-09-12.md → this ledger. Amendments are additive
records; frozen files are never edited. BEIR test.tsv remains FINAL (score-only).
