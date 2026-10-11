# TRACE-LITE Architecture and Roadmap Dossier (TRACE-LITE consolidation)

- Contract: TL-QN-2026-09-13.3 (8-clause final contract + G1–G4 gate packets) per `EXECUTION_PLAN.md`
- Qualification target: R09 C9-1..C9-5; 250k P95<=35ms RSS<=450MB; 1M rung per `R09-qualification.md`
- Architecture baseline: `ARCHITECTURE.md` query-native SS1-10; `PROJECT.md` QN-01..10; `EVALUATION.md` workload/matrix/gates
- Status inputs: `R09 review.md` B3 FAIL; `R09 delivery.md` 2k/10k rungs; `G2_FINAL_RECEIPT.md` G2 FAIL; `repair_cycle1.json`; `capability_cases.json` FROZEN 20; `contract_amendments.md`; `nfcorpus_constraint.md`; `run_integrated_qualification.py` header; Ref A `docs/architecture.md` V2 event-ledger; Ref B `01_SYSTEM_ARCHITECTURE.md` + `03` filing-cabinet
- Context: `/tmp/tl-arch-review/CONTEXT.md`
- Synthesis scope: compact evidence ONLY (cartographer + chunks 1–6 + critic + gap-closure priors 10/11/12). Chunk 7 has no complete evidence result (prior result 3 runtime error).

## 0. Executive summary

Trace-lite is a single-file local-first SQLite system (event ledger + projections + served snapshots + cascade retrieval + dossier compiler) that currently fails scale gates by architecture, not tuning:

- G2 FAIL: 87 lock-500s, WAL 3.95GB @10k, RSS 561.8MB, P95 8878/1579ms, quality never measured (receipt).
- R09 B3 FAIL with denominator artifact; corrected off-ladder scifact 0.6603 / nfcorpus 0.1973.
- 2k rung dossier: ingest 263 docs/s, drain 5.3 jobs/s sampled / 43.9 jobs/s probe best-case, server P95 59ms @2k already above 35ms budget at 250k.
- The same 4 structural defects recur across chunks: (1) writer lock held across heavy work, (2) PASSIVE-only WAL checkpoint starvation, (3) corpus-hub O(N)/O(N²) folds/scans/sorts, (4) per-pass full recompute/reseed/retrain with no prune/retention.
- Quality is UNKNOWN at every scale that matters: recall proven only @600 docs, gates @240 docs, rowid to 2500; ANN detaches >20k; Learned+S4 crashes; sharding/detach deltas unmeasured; 20/20 cases never executed on ladder.
- Roadmap must therefore be measurement-first (frozen reproducers + EXPLAIN + fault-injection + HW spec), then write-path/WAL/projection surgery, then served/ANN immutability, then retrieval/hub redesign, then API/dossier hardening, then re-qualification.

## 1. Failure-point inventory (severity + file refs)

### P0 / CRITICAL (gate-blocking now)

| ID | Finding | File refs | Evidence |
|----|---------|-----------|----------|
| C1-F1 | Publish ACK txn = BEGIN IMMEDIATE + FTS insert + atom_delta trigger full-text copy; writer held across amp | `source/service.py:174,225-233`; `store/database.py:30-45,84-86` | G2 87x locked / 3.95GB WAL |
| C1-F2 | Checkpoint starvation: PASSIVE-only post-commit, threshold 5000 | `store/governor.py:29,44-46`; `store/database.py:73,299` | Fix modes per sqlite WAL docs |
| C2-F1 | Writer starvation: add_members commits inside grouping txn + GROUP_COMMIT_CHUNK=8 + busy_timeout 5s; drain 18.9–43.9 jobs/s → 1M in 6.3–14.7h; full recompute ~80s@100k ~13min@1M | `filing/holon.py:516`; `source/project.py:88,104-108`; `store/database.py:86` | G2 attempt-2: 87 locked 500s, drain 529.9s/10k |
| C2-F2 | WAL starvation: PASSIVE-only governor, TRUNCATE never issued, 64MB threshold → ~400GB@1M if starved | `store/governor.py:44-46`; `filing/workers.py:49,91` | sqlite.org WAL starvation condition |
| C3-F1 | Seed-escalation: drift>256 triggers full reseed every pass; hash 0.070ms/doc, _weight 0.019ms/vec, kmeans ~60s@250k/minutes@1M | `api/app.py:631-640,660-669,759-815`; `router/vector_store.py:456-498` | Per-pass x2 cost |
| C3-F2 | Learned-incompatible-with-S4: encoder._idf AttributeError, _weight zip→[], _qenc wrong-space; zero coverage | `router/vector_store.py:480,338-345,206` | No ServedVectors+LearnedBgeEncoder test |
| C5-F1 | O(N²) drain: single corpus hub + per-pass full text refetch + 2x digest | `source/project.py:70-71,448-501`; `filing/holon.py:917-922,934,952` | 2.95s@4k, ~80ms/job@8k |
| C5-F2 | Retract = full rescan in ONE write txn: ~74s@100k ~184s@250k ~12min@1M | `filing/holon.py:556-593` | remove_members→_refresh_state |
| C4-Q1 | Hybrid full dense scan/query: 28/110/182ms @10k/100k/250k — alone breaches 35ms | `router/hybrid.py:522-564` | Measured |
| C4-Q2 | Warm sha256/token: ~1.3/12/31/125s @10k/100k/250k/1M | `router/engine.py:62-70` | Measured |
| C4-Q3 | ANN detached above 20k atoms; ladder runs ANN-off | `router/cascade.py:198-202` | SERVED_VECTOR_MAX_ATOMS=20000 verified |
| C4-Q4 | Query-path UPDATE+COMMIT takes writer lock per beam query | `router/beam.py:85-89` | Lock contention on read |
| C6-F1 | Publish commits then facet map outside retry; replay skips mapping → membership lost forever | `api/app.py:1639-1666` | Durability/visibility split |
| C6-F2 | Retract commits then fold-out skipped; retry sees 0 atoms → ghosts persist | `api/app.py:1669-1695` | Retract not idempotent |

### P1 / HIGH (breach at 100k–250k or correctness)

- C1-F3 stale-atom N+1 in ACK txn + retract skips centroid cleanup — `source/service.py:115-146 vs 398-412`; `schema.sql:33`.
- C1-F4 2x canonical text + delta + FTS ≈4–5x raw; O(N) verify_store — `source/service.py:226-244,492-566`; contentless-FTS halving option.
- C1-F5 enqueue SELECT-then-INSERT race → UNIQUE 500; claim SELECT-then-UPDATE non-atomic — `store/outbox.py:65-81,112-126`; `schema.sql:96-109`.
- C1-F6 idempotency_keys unbounded; retract replay skips payload/source check; truncated derived key — `source/service.py:284-289,305-314,380-392`; `schema.sql:89-94`.
- C1-F7 _next_seq MAX(seq) RMW under lock, no UNIQUE(stream,seq) — `source/service.py:353-358`; `schema.sql:14-21`.
- C2-F3 outbox scans: queue_stats full SUM 2x/pass, job_progress scan, OR-claim defeats idx_job_state; done never purged — `store/outbox.py:231-241,257-261,112-117`; `filing/workers.py:226,287`.
- C2-F4 trigger amp ~8–10 stmts per claim/complete/fail; heal ~0.5s@10k → ~50s@1M — `source/coverage_state.py:266-276,297-308,598-617`.
- C2-F5 hub collapse: all atoms→one corpus holon; 8 chunks/pass x O(N) scan; FULL_REFRESH_LIMIT=64; 7.9k hub @~80ms/job — `source/project.py:448-501`; `filing/holon.py:94,917-922,934,952,1102-1138`.
- C2-F6 deferral COUNT(*) every pass O(corpus); GROUP_MIN_PASSES=6 → perpetual reconciled=False — `source/project.py:116,712-717`.
- C2-F7 leases 30s floor; fail→pending no dead-letter; MAX(stream_seq) per event on unbounded events — `store/outbox.py:24,107-110,180-202`; `source/project.py:504-509`.
- C2-F8 generation per pass + per write; watermark COUNT(*)+MAX(id) per publish; activate scans kind rows; never pruned — `filing/workers.py:311-339`; `api/app.py:1034+,1026-1031`; `store/generations.py:50-52`.
- C3-F3 per-pass full kmeans retrain; MAX_LISTS=256; recall only @600 docs — `router/vector_store.py:668-720`; `router/index_adapter.py:81-115,157-159`; `tests/test_index_adapter.py:54-59`.
- C3-F4 seed RSS python-float raws ~0.9GB@250k ~3.6GB@1M; cache JSON 7.1MB@10k → ~178MB@250k; full-parse load — `router/vector_store.py:471-482,146-161`; `api/app.py:401-423`.
- C3-F5 catch-up publish busy fails pass (except misses OperationalError); staleness unbounded — `api/app.py:702-711 vs 775-789,506`.
- C3-F8 no production prune (zero callers); each publish/seed leaves full generation 131MB@250k/512MB+@1M — `api/app.py:939` only accounts.
- C4-Q5 holon self-join 3xM+sort/query; extras bypass lexical gate — `router/cascade.py:294-346,484-494`.
- C4-Q6 fp16 matrix 2.6/25.6/64/256MB + ~100MB dicts @1M → RSS breach — `router/hybrid.py:190-254`.
- C4-Q7 live_ids rebuilt per query; full sort on allowed path — `router/index_adapter.py:181,227`.
- C4-Q8 add_atom O(N*d) concat; frozen IDF no refit — `router/hybrid.py:404-414,260-269`.
- C4-Q9 publish transient ~500MB+ @250k; encode_batches dead — `router/projection.py:41-56,83-94`.
- C5-F3 get_holon full id-list 2x/add + N+1 — `filing/holon.py:391-395,406-431`.
- C5-F4 query_facets full-set materialization, limit=None — `filing/engine.py:187-215,281-311`; P95 driver.
- C5-F5 persist_graph ~20 stmts/assertion single-row; dep table unbounded — `graph/store.py:81-155`; `schema.sql:291-292`.
- C5-F6 invalidation pop(0) O(n²)+snapshot/flip; corpus exclusion caller-only; breach rolls back txn — `filing/holon.py:1020,1041-1044`; `filing/engine.py:651,688-699`; `api/app.py:1606-1608,1756-1759`.
- C5-F7 hub recall collapse: carried descriptor/reps + unweighted 128d centroid — `filing/holon.py:904,1085-1110`; `filing/engine.py:62-70`.
- C6-F3 O(N) COUNT(*) watermark per write — `api/app.py:1026-1031`.
- C6-F4 whole-vault sync under global write_lock + full RAM buffer + unconditional generation — `api/app.py:1698-1761`.
- C6-F5 shared_lock held across 2x GIL-bound warm encodes; queries serialize per generation — `api/app.py:1200-1248`.
- C6-F6 freshness() per query/dossier/status; ~1.5ms/pending-rev — `api/app.py:1771-1867,2187,2297`.
- C6-F9 unclamped limit/text/budgets; N+1 per candidate — `api/app.py:52-88`; `cordis/dossier.py:275-376`.
- C6-F10 dossier TOCTOU: pinned route, live-table resolve, no retry — `cordis/dossier.py:580-589`.
- C6-F11 relations bypass revision binding; substring synthesis check — `cordis/dossier.py:915-918,1018-1029`.
- C6-F12 TMS eval() sandbox-escape class (builtins removed only) — `cordis/tms.py:31-34`.
- C6-F13 unbounded inhibitors/committed/leases; TMS in-memory restart wipes ledger — `cordis/gate.py:37-40`; `cordis/plugin.py:96-106`.
- C6-F17 status O(served) id set + disk rglob per poll — `api/app.py:360-371,918-976`.
- C6-F18 /api/sync arbitrary path — `api/app.py:2390-2411`.

### P2 / MEDIUM-LOW (tail, quality, ops)

- C1-F8 migrate_legacy giant txn + O(N) parity + VACUUM INTO 2x disk + stale-backup reuse — `store/migrate.py:53-62,65-107,117-118,153-228`.
- C1-F9 search_current 4-way join uncovered — `source/service.py:475-489`; `schema.sql:132`.
- C2-F9 signal-only backpressure; poll 1s + 64/batch caps; shutdown max 4 batches; G3 40 jobs/s ~9% headroom — `store/outbox.py:295-313`; `api/app.py:2095`.
- C3-F6 tombstone skips counted but adequate=True; k+delta insufficient under skew — `router/vector_store.py:593-600,617-618`.
- C3-F7 filter sort depth=len(rows)+python sort; 3.8ms filtered vs 0.3ms @20k (12x) — `router/index_adapter.py:227-238`.
- C3-F9 learned.load ignores MODEL_REVISION; double-tokenize; cascade 20k detach cap; dead encode_batches — `router/learned.py:141,211-251`; `router/cascade.py:198-202`; `router/projection.py:41-56`.
- C4-Q10 lexical substring inflates T1; gate blind to negation — `router/lexical.py:300-302,218-270`.
- C5-F8–F14: suggest O(S*C) `filing/reconcile.py:87-97`; extractor O(S²)+14regex+U·x·S `graph/extractor.py:110-126,293-298,500-518`; atomic spans vs 50k cap `source/segmentation.py:191-202`; get_spans O(spans·x·rev) `source/views.py:197-212`; IN-clause var limit `graph/store.py:279-281`; O(k·D) index() `filing/holon.py:320-325`; text-blind digest/atom-immutable/version+1 `filing/holon.py:1113-1138`.
- C5-F15 unresolved binds nothing; relation cols vs harness names — `graph/extractor.py:497-519`; `graph/store.py:120-136`.
- C6-F7 excerpt bytes never verified (non-span path); caller bounds override — `cordis/dossier.py:707-734`.
- C6-F8 TokenBudget ValueError → 500 — `api/app.py:2349-2365`.
- C6-F14 recursive cycle detect (~1k crash); pop(0) revocation — `cordis/tms.py:215-293`.
- C6-F15 plugin ignores facets/visibility/as_of; leases decorative — `cordis/plugin.py:74-113`.
- C6-F16 CLI ingest never drains outbox — `cli.py:36-71`.
- C6-F19 len//4 tokens undercounts non-English 2–4x — `cordis/models.py:20-21`.
- C6-F20 tier1 keyword gate bypassed by synonyms — `cordis/gate.py:81-88`.
- Visibility: retract completes w/o invalidation verify (`source/project.py:831-834`); hole cap 64 hides backlog (`source/coverage_state.py:52`); deferred grouping lexically visible but ungrouped.

## 2. Per-chunk findings with trade-offs

### Chunk 1 — Write path & source durability
Findings F1–F9 above. Root cause: ACK txn does too much (revision + FTS + delta trigger copy + stale scans + MAX(seq)) under BEGIN IMMEDIATE while checkpoint is PASSIVE-only and outbox claim/enqueue are non-atomic.
Trade-offs:
- Move FTS/delta out of ACK (async projection) → lower ACK/lock hold but eventual lexical visibility; needs freshness contract + cap-64 fix.
- Contentless FTS + single canonical text → halves storage but loses stored-text reads; requires join-back.
- Single-writer queue + jittered busy retry + UNIQUE(stream,seq) + idempotent consumers → effectively-once; costs queue latency and retention/GC design (Debezium/AWS outbox pattern; EF Core/SQLite concurrency precedent; RocksDB manifest pointer-swap analogy).
- NORMAL-vs-FULL + fault-injection decides durability cost; not yet measured.

### Chunk 2 — Projection pipeline & background workers
Findings F1–F9 + visibility. Root cause: grouping txn commits inside pass, full recompute per pass, PASSIVE-only WAL, full-table queue/coverage scans, per-pass triggers/heal, one corpus hub, COUNT(*) deferral gate, 30s leases without dead-letter, generation-per-pass/write without prune, signal-only backpressure.
Trade-offs:
- Batch trigger maintenance per pass + heal off write path (Noria partial-IVM / DBToaster higher-order deltas / differential-dataflow arrangements) → less write amp but more projection complexity.
- Shard corpus holon at 1–2k members + background merge (LSM immutable-segment/compaction shape, already S4's shape) → O(shard) folds but quality delta UNKNOWN (SciFact floor + 20 cases must be re-measured).
- Reader gaps / RESTART/TRUNCATE + journal_size_limit backstop → bounds WAL but needs read-availability windows; BEGIN CONCURRENT/mvsqlite considered and rejected (single-file local-first).
- Outbox retention/offset tracking + purge + dead-letter → bounds scans but needs exactly the generation/watermark accounting that is currently O(N).

### Chunk 3 — Served vector snapshots & ANN
Findings F1–F9. Root cause: drift>256 full reseed + per-pass kmeans retrain + python-float raws + JSON cache full-parse + busy-miss except + no prune + learned/S4 mismatch + filter full-sort.
Trade-offs:
- Incremental IVF add / merge_from/merge_into + on-disk merge (Faiss verified), growing/sealed segments + handoff/merge (Milvus), immutable segments + merge policy + NRT + tombstones (Lucene), or HNSW online inserts — all avoid reseed/retrain but require segment manifest + CURRENT discipline + adequacy under skew + recall-vs-K/delta measurement. Faiss evolving-index survey frames the choice.
- Fixing learned+S4 (idf/weight/qenc + MODEL_REVISION pin) is mandatory before any learned-quality claim; double-tokenize and detach-cap fixes are coupled.
- Prune/collect_orphans/stale_sidecars must gain production callers or disk grows 131MB/publish @250k.

### Chunk 4 — Query routing & retrieval
Findings: beam writer lock; ANN-off >20k; dense scan P95 breach; holon self-join + gate bypass; lexical substring/negation; warm 125s@1M; fp16+dict RSS breach; live_ids rebuild + full sort; transient 500MB+; O(N·d) add + frozen IDF; RRF k=60 / cascade / WAND / stream-ANN / abstention / hash references; coverage only @240/2500/600 docs.
Trade-offs:
- Late-rowid + WAND + IVF/HNSW stream-ANN → sub-35ms but needs posting/segment infra and ANN-off recall baseline first.
- RRF k=60 standard fusion is fine; cascade early-exit saves tail only if T1 gate handles negation/synonyms and extras do not bypass it.
- Frozen IDF/hashbow is cheap but quality-capped; refit/re-embed moves cost to projection/seed (chunk 3) and warm path.
- No threshold/limit-change quality run exists; every routing shortcut is currently an unmeasured recall bet.

### Chunk 5 — Graph & holon organization
Findings F1–F15. Root cause: leaky corpus holon (semantic + physical fold/scan/centroid/sort unit) + write-txn rescans + N+1 fanouts + full-set facets + single-row persists + O(n²) invalidation + carried/stale reps + unweighted centroid + O(S²) extractor + schema/harness relation-name mismatch.
Trade-offs:
- Shard + overlap-aware community maintenance (COPRA; dynamic Leiden) + set-reconciliation sketches (IBLT/rateless/Graphene) → bounded folds but overlap/merge quality must be proven against hub-recall literature (Radovanovic Hubs in Space; high-dim spaces).
- OpenIE upgrade (ReVerb 2xAUC; MinIE band) improves relations but increases persist/extract cost unless batched (250k/s SQLite batch precedent).
- Fixing relation column names (store vs harness) is a pure blocker for capability-case execution, independent of scale.

### Chunk 6 — API surface & dossier compilation
Findings F1–F20. Root cause: commit-then-side-effect splits (publish facet map, retract fold-out), O(N) watermarks/freshness/status, global write_lock + shared_lock across encodes, unclamped inputs + N+1 resolve, pinned-route/live-resolve TOCTOU, revision-bypass + substring checks, eval TMS + in-memory ledger + recursion/pop(0), decorative leases, CLI no-drain, path traversal, token undercount, synonym bypass.
Trade-offs:
- One-writer + BEGIN IMMEDIATE canonical + BUSY_SNAPSHOT op-restart → correct concurrency but requires moving encodes out of locks and adding generation-pinned reads.
- Clamp + paginate + batch-resolve + pinned-snapshot resolve + retry → bounds dossier but changes API contracts.
- Replace eval with CEL (or equivalent) + persist TMS ledger + iterative cycle detect + indexed revocation → closes sandbox/durability holes; AIS attribution framework should replace substring synthesis checks; tiktoken-exact counting fixes budgets; rate-limit/input-validation (OWASP API) fixes DoS-by-limit.
- /api/sync path must be jailed or removed; CLI must drain or explicitly not-claim durability.

### Chunk 7 — Qualification harness & evidence
No complete evidence result (prior result 3 runtime error). Partial inspected evidence only:
- R09 dossier 2k rung (priors 11): contract TL-QN-2026-09-12.1 attempt 2; profile quality, dense_pool 2000, sparse_pool 100, beam_cap 400, served_max 20000, holon_guided, fallback-hashbow-128d (R08 PARK), hybrid top_k 10; ingest 263.28 docs/s; spans/assertions/relations 913/1902/1998; db 28.7MB; WAL peak 4.9MB; RSS peak 93.85MB; drain sampled 5.314 jobs/s, pending 1936→1680, converged false, projected_full 316.1s; server p50/p95/p99 6.54/59.02/87.35ms; e2e p50/p95/p99 407.89/474.66/499.12ms.
- Capability cases 11–20 individually read (prior 10): all beir-scifact targets, neg=false, multi=true, 2 expected + 2 forbidden each; mix verified (negation in cap-01..10 only; multi all 20); harness blocker header line 4 (subject/predicate/object vs subject_text/rel_type/object_text).
- PACKET/B2/B3/REVIEW gap closed (prior 12): external Trace Prompt-Kit standards under KIT/, not trace-lite files; in-repo artifacts are evidence/query-native/R*/delivery.md + review.md and docs/builds/query-native/packets/R00–R09.
- Harness header + 120 lines + G2 budgets read; score_beir/child-env internals not re-read.
Trade-off: harness is the only arbiter of done, but currently cannot arbitrate — gates unrecovered, quality never executed, tails unmeasured. Recovering chunk 7 is P0.

## 3. Cross-cutting risks

1. Single-writer vs federation. One-writer + BEGIN IMMEDIATE is canonical for this SQLite design (plainq skill; SES-01 precedent) but four independent lock-holders collide: ACK txn (c1F1), grouping txn (c2F1), beam query UPDATE (c4), whole-vault sync + warm encodes under locks (c6F4–F5). No single owner or lock budget exists. Federation (mvsqlite/BEGIN CONCURRENT) was considered and rejected for local-first single-file reasons — so the only path is one queued writer, reader gaps for checkpoints, and generation-pinned lock-free reads. Until then every added feature increases 500s.
2. Soft vs hard state. Generations, watermarks, coverage/freshness, queue_stats, holon_version, idempotency_keys, TMS ledger, and served CURRENT are maintained as if free, but each is O(N)/unbounded/unpruned and several sit on the write or query path (c2F3/F4/F8, c6F3/F6/F13/F17, c3F8). Hard state (ledger, atoms, FTS, outbox done rows) grows without retention. Fix requires deciding per structure: hard + retained + pruned, or soft + recomputable + evictable (Noria-style), with nothing O(N) per write/query.
3. Routing recall. ANN-off >20k + dense-scan breach + frozen IDF + substring/negation/synonym gate gaps + extras bypass + unmeasured fusion thresholds = recall UNKNOWN wherever scale matters. Provenance: recall @600, gates @240, rowid to 2500, 0.6603/0.1973 off-ladder. Every detach/shard/limit/threshold change needs an ANN-off recall-vs-K/delta curve at 20k+ skew before acceptance.
4. Hub handling. The corpus holon is simultaneously the semantic unit, fold unit, scan unit, centroid unit, and sort/filter unit (c2F5/c5F1/c3F7 triple-reported). Consequences: meaningless top-3 descriptor, stale carried reps, text-blind digest, caller-only exclusion, 100k+ sorts, 12-min retracts. Shard-at-1–2k + merge is the unanimous direction but quality impact is disclaimed by all reporters — must be measured, not assumed.
5. Sparsity choices. Hashbow-128d fallback (R08 PARK) + frozen IDF + sign-hash collisions + double-tokenize windows + contentless-vs-stored FTS + fp16-vs-raw vectors trade memory for quality at every layer. Current posture pays both: ~4–5x write amp + GB-scale transients/raws/JSON AND capped recall. Need one sparsity/quantization budget tied to HW page/cache reality, not independent per-layer guesses.
6. Precomputation limits. Warm (sha/token 125s@1M), seed (hash+weight+kmeans minutes@1M per pass x2), fold (O(N) scans), facets (full-set), freshness (1.5ms/rev), status (O(served)+rglob) all assume precomputation is cheap because corpora were small. At 250k/1M every full pass is minutes-to-hours. Only incremental/delta/segment-merge designs survive; per-pass full kmeans/reseed/refetch must go.
7. Hardware boundaries. No HW spec; no page/cache/mmap/frag model; no IO model for parity round-trips, VACUUM-2x, full-parse JSON, fp16/dict/raws RSS. All 100k/250k/1M numbers are linear extrapolations from 2k/10k probes (plus G2's 3.95GB WAL / 561.8MB RSS / 8878ms P95 halt-condition points). No P95 at 100k/250k/1M, no P99+, no lock-hold/freshness/beam-wait tails. Publishing the qualification mount + RAM + CPU + SQLite page/cache settings is a precondition for every budget.

## 4. Recommended phased roadmap to the finish line

Principle: no architecture change without a frozen reproducer and a measured gate; no gate without HW spec + EXPLAIN + metered amp.

### Phase 0 — Measurement harness (unblocks everything)
- Recover chunk 7: re-run integrated ladder; restore G1–G4 gate packets; fix relation column-name blocker (harness subject/predicate/object vs schema subject_text/rel_type/object_text); re-read score_beir/child-env internals.
- Frozen reproducers: (a) ACK breakdown per-op + FTS B/job + atom_delta B/job + WAL frames/s; (b) OR-claim EXPLAIN at 1M outbox rows; (c) per-trigger statement cost on filled DB; (d) watermark COUNT(*) wall at 100k/250k/1M; (e) shared-lock hold seconds at 10k+; (f) queue_stats cost on large outbox; (g) fold-path cost constant; (h) kmeans iters on real ladder corpus (not uniform random).
- Publish HW spec; re-derive RSS/IO budgets; fault-inject NORMAL-vs-FULL power loss on qualification ext4 mount.
- Gates: P95/P99/P999 at 10k/100k/250k under load; SciFact nDCG@10 + 20/20 cases on 10k ladder; retract-to-dossier exclusion across retry/restart; ANN-off recall vs K/delta at 20k+ skew. Quality floor: SciFact 0.6400.
- Resolves critic boundary: pin/lock, queue_stats, router limits each get an owner and a number.

### Phase 1 — Write path + WAL (P0)
- Single queued writer + jittered busy retry; shrink ACK txn (FTS/delta async); UNIQUE(stream,seq); atomic enqueue/claim; bounded idempotency_keys with TTL/purge; retract payload/source check.
- Checkpoint discipline: reader gaps + RESTART/TRUNCATE policy + journal_size_limit backstop; cover search_current join; chunk migrate_legacy + remove VACUUM-2x from critical path.
- Gate: zero lock-500s on 10k ladder; WAL bounded (not 3.95GB); ACK P95 within budget; EXPLAIN-clean claim plan.

### Phase 2 — Projection/bounded drain (P0)
- Grouping commit outside pass txn; batch trigger maintenance; heal off write path; purge done rows + dead-letter + retention/offset tracking; replace COUNT(*)/MAX per-write watermarks with maintained watermarks; prune generations; real backpressure (not signal-only); fix shutdown drain cap.
- Shard corpus holon 1–2k + background merge behind a quality gate: quantify SciFact/cases delta BEFORE accepting sharding.
- Gate: drain ≥ headroom above ingest (G3 40 jobs/s needs margin over 43.9 probe); reconciled converges under drift; no O(corpus) per pass.

### Phase 3 — Served snapshots + ANN immutability (P0/P1)
- Kill per-pass reseed/retrain: incremental IVF add/merge or sealed-segment handoff/merge or HNSW inserts; fix Learned+S4 (idf/weight/qenc + MODEL_REVISION pin); fix busy-miss except; bound delta/tombstones with measured adequacy; replace full-sort filter with indexed allowlist path; wire prune/collect_orphans/stale_sidecars into production.
- Gate: seed/publish wall + RSS bounded at 100k/250k; recall curve at 20k+ skew; no unbounded disk growth per generation.

### Phase 4 — Retrieval + hub/organization (P1)
- Remove writer lock from query path; late-rowid + WAND + stream-ANN; fix holon self-join + lexical-gate bypass; negation/synonym handling with measured deltas; refit or replace frozen IDF with budgeted re-embed; batch persist_graph; fix invalidation O(n²) + caller-only exclusion; N+1/batch fanouts; extractor windows/regex budget.
- Gate: P95<=35ms at 250k; RSS<=450MB; facets bounded; retract P95 bounded (not 12 min).

### Phase 5 — API/dossier/Cordis hardening (P1/P2)
- Atomic publish facet-map + retract fold-out (commit-then-side-effect elimination); generation-pinned reads; encodes out of locks; clamp/paginate/batch; pinned-snapshot dossier resolve + retry; revision-bound relations + AIS-grade attribution checks; CEL-for-eval; persisted TMS ledger + iterative cycle detect; jail /api/sync; tiktoken-exact budgets; CLI drain-or-disclaim.
- Gate: retry/restart visibility proofs; no 500-on-small-budget; no TOCTOU/ghost/substring escapes; traversal closed.

### Phase 6 — Re-qualification
- Full ladder 2k→10k→100k→250k→1M rung on published HW; P95/P99/P999 + RSS + WAL + drain + quality at each rung; B3 independent verify; denominator-artifact regression test; capability 20/20 on ladder.
- Done = C9-1..C9-5 + frozen floor + measured tails, not extrapolation.

## 5. What Lite means now

- Lite = single-file local-first SQLite, one queued writer, immutable served segments + CURRENT flip, bounded async projections, generation-pinned reads, LLM-free dossier compilation. Federation, concurrent writers, and online full-retrain/reseed are explicitly OUT.
- Lite does NOT mean: full recompute per pass, full scans per query, unbounded ledgers/keys/generations/caches, or unmeasured recall. Every unbounded structure found in this review violates Lite and must gain a bound, a prune policy, or eviction semantics.
- Lite's contract surface is: atomic source replacement + idempotent effectively-once projection + served-snapshot isolation + cascade retrieval within P95/RSS budgets + evidence dossiers with revision-bound attribution. Anything that breaks those (ghosts, TOCTOU, substring checks, in-memory TMS, ANN-off without recall proof) is not Lite — it is a bug.
- Operationally Lite means: one writer queue depth + one checkpoint policy + one retention policy + one HW-spec budget own every scale number. Today those four owners do not exist; creating them is the roadmap.

## 6. Open questions (with provenance)

1. What is the qualification HW + mount + SQLite page/cache config? (critic hardware/layout; G2 receipt unproven RSS; all 1M numbers extrapolated)
2. What is the per-op ACK breakdown and B/job (FTS/delta/WAL frames/s)? (c1 unresolved; G2 mechanics unmeasured)
3. What EXPLAIN plan does the OR-claim predicate produce at 1M done rows? (c2F3; index-defeat from shape only)
4. NORMAL-vs-FULL power-loss window on the qual mount? (c1 unresolved; no fault-injection)
5. What is the shard-1–2k SciFact/cases delta? (c2F5/c5F1; disclaimed by all; floor 0.6400)
6. What is ANN-off recall vs K/delta at 20k+ skew? (c3F6/c4 detach; verified detach, unmeasured recall)
7. Can Learned+S4 serve without AttributeError/wrong-space, and at what encode cost? (c3F2/F9; INT3_LIVE absent)
8. What is retract-to-dossier exclusion latency across retry/restart, and do ghosts persist? (c5F2/c6F2/citation-retract cluster; cap-64 hides backlog)
9. What are P95/P99/P999 at 100k/250k/1M under load, including lock-hold/freshness/beam-wait tails? (critic P95/tail; only 10k halt P95s)
10. Who owns the write-lock budget, and what are the pin/lock semantics under swap? (boundaries write-lock x4; c3 unresolved api/app.py:737-745; c6F4–F5)
11. What is the true RSS attribution (fp16/dict/raws/JSON/frag/mmap/page-cache)? (c3F4/c4 RSS; 448–562MB@10k unproven; seed-raws ~36MB@10k partial only)
12. Do keyword-gate synonym bypass, negation blindness, and substring inflation change quality thresholds? (c4 lexical; c6F20; no threshold-delta run)
13. What are queue_stats/watermark/fold/trigger/holon_version constants on filled DBs? (c1/c2 unresolved; needs frozen reproducers)
14. Where do PACKET/B2/B3/REVIEW live and what are the in-repo artifacts? RESOLVED by prior 12: KIT/ standards; in-repo = evidence/query-native/R*/delivery.md + review.md and docs/builds/query-native/packets/R00–R09.
15. What do 2k→10k→100k gates say once chunk 7 is recovered? (prior result 3 error; harness header-only; score_beir/child-env unread)

## 7. Open Gaps (verbatim — every unresolved item preserved)

- run_integrated_qualification.py: header + first 120 lines only; score_beir/child-env internals not re-read
- trace v2-architecture.md full body + Ref B docs 02,04-08 and research_specifications 00,02-04: listed, not individually read
- No live 100k/250k/1M timing run in this chunk: per-op ACK breakdown, FTS bytes/job, atom_delta bytes/job, and WAL frames/s unmeasured on target hardware
- Citation/visibility and routing-recall failure mechanics belong to chunks 4/6; hub-collapse 7.9k-member fold belongs to chunk 5 — not re-derived here
- synchronous=NORMAL power-loss window vs FULL on the qualification ext4 mount not fault-injected here
- Per-trigger statement cost at scale not profiled (needs EXPLAIN QUERY PLAN + timing probe on filled DB)
- RSS attribution 448-562MB@10k unproven (CONTEXT-marked); transient fold-scan RSS projected ~1GB+ at 1M, not measured
- EXPLAIN plan for OR-claim predicate over 1M done rows not run; index-defeat claim is from SQL shape, not a measured plan
- Quality impact of corpus sharding on SciFact floor 0.6400 / 20 capability cases UNKNOWN (quality never executed per CONTEXT)
- holon_version row growth rate and _snapshot MAX(version) cost at 1M projected, not measured
- kmeans Lloyd iter count on real ladder corpus (measured 0.36s@2k/0.71s@10k on uniform random with early convergence; skewed hashbow may run more of 25 iters)
- Exact seed wall-time/RSS at 100k/250k/1M (extrapolated from unit costs, not run: no ladder corpus materialized in this audit)
- Learned-weights live behavior (INT3_LIVE snapshot absent here; encode_many batch/chunk costs unmeasured)
- Query-path snapshot pin/lock behavior under swap (chunk 6 territory; swap is api/app.py:737-745 under self.lock, reader side not inspected)
- G2 561MB RSS regression attribution (context says unproven; this audit adds seed-raws ~36MB@10k as partial contributor, not the full delta)
- '7.9k' exact figure not located; G2 receipt says ~8k — used ~8k
- Weinberger09 primary URL unfetched; sign-hash via ar5iv collision section
- WAND primary body unfetched; claim at snippet level
- 1M scan is linear extrap, not a run (C9-4 deferred)
- No quality run exists; threshold-delta quality unmeasured
- 7.9k figure provenance (only 8k anchor in CONTEXT.md:81 found)
- Fold-path cost constant needs frozen reproducer; IBLT/OpenIE via snippets+abstracts, unmeasured
- watermark COUNT(*) wall time at 100k/250k/1M (needs frozen repro)
- router.route huge/negative limit behavior (chunk 4, unverified)
- untrusted TMS predicate reachability beyond API path
- shared-lock hold seconds at 10k+ (needs probe)
- queue_stats cost on large outbox (chunk 1 file)
- chunk-7: Chunk 7 - Qualification harness & evidence: integrated ladder, gate se: missing complete evidence result
- Run SciFact nDCG@10 + 20/20 cases on 10k ladder.
- Measure P95/P99/P999 at 10k/100k/250k under load.
- Run EXPLAIN on OR-claim at 1M outbox rows.
- Meter ACK breakdown, FTS/delta B/job, WAL frames/s.
- Fault-inject NORMAL-vs-FULL power loss.
- Publish HW spec; re-derive RSS/IO budgets.
- Quantify shard-1-2k SciFact/cases delta.
- Prove retract-to-dossier exclusion across retry/restart.
- Measure ANN-off recall vs K/delta at 20k+ skew.
- Single-owner write-lock budget; recover chunk7 gates.

## 8. Omitted scope / synthesis limits

- Synthesized compact evidence ONLY; no independent source re-reads, no live runs, no EXPLAIN metering in this step.
- Chunk 7 has no complete evidence result (prior result 3 generated_child_runtime_error); harness/score internals, gate semantics, and full ladder tables are therefore partial (priors 10/11/12 only).
- Sampling limits carried forward: dossier 80-line / cases 10-of-20-then-closed-by-prior-10 / harness 120-line / Ref B listed-not-read (per critic, partially closed: cases 11–20 and PACKET/B2/B3 now resolved by priors 10/12; harness body and Ref B bodies remain unread).
- Citation limits: LinkedIn/Medium/help.openai/libraries.io externals uncited per critic; Weinberger/WAND/IBLT/OpenIE at snippet/abstract level; Faiss/Milvus/Lucene/HNSW/debezium/sqlite-WAL/AIS/CEL verified via fetch per chunk reporters.
- Boundary overlaps deliberately preserved, not deduplicated: hub-collapse x3 (c2F5/c5F1/c3F7), WAL x2, outbox theory x2, write-lock x4 (c1F1/c2F1/c4-beam/c6F4–F5); sharding quality disclaimed by all; pin/lock, queue_stats, router limits each deferred to Phase 0 reproducers.
- All 100k/250k/1M figures are extrapolations unless labeled G2-measured; no P95 at scale, no P99+, no fault-injection, no HW spec — see Open Gaps and Phase 0.
