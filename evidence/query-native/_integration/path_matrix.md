# Runtime-path evidence matrix (steering §2)

Candidate: `evidence/query-native/_integration/candidate_manifest.json` (HEAD 0df044b + hashed files).
Method: source inspection of the served path (not imports/exports). Date: 2026-09-12.

| Arrow | Actual caller | Implementation | Persistent effects | Generation/version rules | Executable verification | Verdict |
|---|---|---|---|---|---|---|
| API ingest/sync → immutable revision | `/api/ingest`→`ingest_note`, `/api/sync`→`sync_vault` (`app.py:670,768`) | Legacy atom replace, single txn (R06) | atom/fts/events/facets rows | filing generation bump only | POST then inspect DB tables | GAP: no `source_revision`, no idempotency key, no outbox job (INT-1) |
| Revision → spans | none on write path | R02 `segmentation.py`/`views.py` (library only) | none at runtime | view ids exist but uncreated | ingest then count `evidence_span` rows = 0 | GAP (INT-1) |
| Spans → assertions/relations | none on write path | R04 `graph/` (library only) | none at runtime | extraction runs uncreated | ingest then count relations = 0 | GAP (INT-1) |
| → reconciliation/holons | none on write path | R05 `reconcile.py`/holon store (library only) | none at runtime | no versions created | ingest then count holon_members = 0 | GAP (INT-1) |
| Retrieval/index publication | `route_pinned`→`CascadeRouter.route` (`app.py:294`) | Legacy cascade; R03 `VectorIndex`/encoder exported, unused in `cascade.py`; generation-swap retry present | R03 generation rows (manual tests only) | filing generation pin | query during sync; inspect tier branch | GAP: intended branches unwired (INT-2) |
| Query dossier + citations | `/api/dossier`→`compile_dossier` (`app.py:739`, no conn) | Atom candidates w/o revision/span ids; `compile_dossier` has no `conn` param | none (read-only) | snapshot carries generation id only | dossier then resolve citations vs raw revisions | GAP: atom_self-grade, not revision-provenance (INT-2) |
| Edit/delete invalidation | none from sync path | `engine.invalidate_dependents` exists, uncalled from `sync_notes`/`ingest_note` | syntheses stay un-staled | dependency rows written by R05 lib only | edit premise via API, check dependent status | GAP: R06-F4 open (INT-1) |
| Restart/recovery | R01 outbox resume (library-tested) | jobs never created by write path → pending==0 vacuous | — | — | restart with pending work | GAP: vacuous completeness (INT-1) |

Component PASSes (R00–R07) stand as library-level results; they do not demonstrate the served path above.

## INT-1 reverification (2026-09-12, review: `_integration/INT-1/review.md`, 39/39 probe + 240 suite)

Rows 1, 2, 3, 4, 7, 8 → VERIFIED on the served path (revisions supersede/dedupe
via API; spans minted incl. unicode byte-exact; relations extracted; holons
track current atoms across restart; premise edit via API auto-stales
dependents — R06-F4 closed; freshness = per-revision coverage, resume
pending→complete across restart). Carryovers to INT-2: row 5 (ANN unwired),
row 6 (atom-anchored citations). Known window: retract-commit→invalidate
crash gap (ms-scale, events retained for repair).

## INT-2 reverification (2026-09-12, review: `_integration/INT-2/review.md`, 251 suite)

Row 5 VERIFIED: served `route=vector`, generation-pinned, named fallback
selectable, cost disclosed. Row 6 VERIFIED: dossier citations resolve
server-side against named immutable revisions + byte ranges. Minor finding
(recorded, not blocking): a wrong CLAIMED revision is silently corrected to
the true one — output byte-true but not flagged degraded; queued for the
critic repair batch. No INT-1 regression; scope clean.
