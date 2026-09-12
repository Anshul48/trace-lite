# Query-native program state

Contract: TL-QN-2026-09-12.1
Recorded: 2026-09-12
Scope: planning revision only.

## Candidate and evidence

- TL source reviewed: `c8e6b701dbd86a412ffec228afde319e122d337e`.
- Pre-existing working-tree item: untracked `uv.lock`; preserved.
- Concurrent edits appeared during planning in TL product code, tests, benchmark artifacts and the legacy architecture document. Those edits were preserved; only planning notices were added to legacy entry points. Re-pin the resulting tree before implementation. The research findings describe the inspected base, not qualification of concurrent repairs.
- TRACE read-only reference: `a24a937009fec41e6dcb6d2ca31d3c1783ac97f7`. Live overnight candidate/runtime not inspected.
- Research and architecture artifacts written. Product changes, model trials, tests, recovery probes and benchmark reruns: NOT RUN.
- Historical P01–P07 and scale receipts remain under `../filing-cabinet/` and `evidence/`. Their scope does not qualify QN requirements.
- This planning review is not an independent builder/verifier qualification.

## Packet board

READY below means the packet has no technical prerequisite; it does not record a new execution request.

| Packet | Status | Dependencies | Evidence |
|---|---|---|---|
| R00 Baseline and evaluation contract | READY | Existing checkout | None |
| R01 Atomic evidence and projection lifecycle | DRAFT | R00 | None |
| R02 Source spans and adaptive view contract | DRAFT | R01 | None |
| R03 Learned retrieval and global index | DRAFT | R02 | None |
| R04 Evidence-linked local graphs | DRAFT | R02 | None |
| R05 Cross-source reconciliation and holons | DRAFT | R03, R04 | None |
| R06 Incremental maintenance and publication | DRAFT | R05 | None |
| R07 Evidence dossiers and Cordis adapter | DRAFT | R06 | None |
| R08 Predictive refinement experiments | DRAFT | R07 | None |
| R09 Integrated qualification | DRAFT | R07; R08 disposition | None |

## Next execution boundary

On an implementation request, begin R00; re-pin HEAD and check concurrent work first. Do not rerun completed reset packets, continue the old cross-project launch instructions, or touch TRACE.

R08 may remain PARKED/experimental after an inconclusive result while R09 qualifies the deterministic structural/graph baseline. In that case, the release must not claim implemented predictive atomization or learned repair.
