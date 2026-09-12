# Evaluation and qualification contract

Contract: TL-QN-2026-09-12.1. All new measurements NOT RUN.

## 1. Freeze the workload before selecting winners

R00 records host CPU/RAM/GPU, OS/filesystem, free storage, loaded SQLite build, durability profile, Python/runtime dependencies, candidate commit plus dirty diff, corpus checksums, query/qrel versions, parser/encoder/extractor artifacts, random seeds, tokenizer, ranking configuration and budgets.

Report source bytes, source count, spans/source distribution, total spans/assertions/relations/memberships, graph degree tails, index size and update mix. A million sources with eight spans each is an eight-million-span workload.

Select representative domains: mixed technical notes/specifications, long narrative/argument sources, code/logs/tables, and frozen public retrieval corpora. Preserve data provenance/licensing. Development and final evaluation split by source family and time where possible; near-duplicate sources must not leak between splits.

R00 writes `evidence/query-native/R00/workload.json` and `gates.json`. Proposed numeric budgets and non-inferiority margins are recorded with their rationale, fixed before final test-set evaluation, and labeled engineering decisions rather than user mandates. Until these exist, quantitative qualification is INCONCLUSIVE.

## 2. Comparison matrix

| Variant | Purpose |
|---|---|
| B0 Current TL quality and latency profiles, separately | Reproduce historical behavior without conflating configurations |
| B1 Structural passages + BM25 + context expansion | Strong inexpensive baseline |
| B2 B1 + learned embeddings/hybrid retrieval | Isolate representation benefit |
| B3 B2 + local assertion graph | Isolate relation retention |
| B4 B3 + persistent cross-source subgraph holons | Isolate reusable organization |
| B5 B4 + predictive segmentation | Isolate adaptive boundary contribution |
| B6 B4/B5 + learned selective repair | Isolate maintenance-controller benefit |

Compare exact and ANN retrieval in the same vector space, with the same filters and candidate budgets. Separate token-surprise segmentation, embedding-shift segmentation, and contextualized embedding experiments. Expensive models are allowed but must disclose build/query cost.

Measure each added mechanism against its immediate simpler baseline and B1/B2. A combined win does not justify every component. Promote only if the designated primary metric improves with paired uncertainty analysis and all frozen regression/cost gates pass. If evidence is inconclusive, retain the simpler qualified variant and record the research outcome honestly.

## 3. Retrieval and evidence quality

- Frozen BEIR SciFact, NFCorpus and FiQA: nDCG@10, Recall@10, per-query results, abstentions and paired bootstrap intervals. Retain existing comparison floors as historical references.
- Map retrieved spans to unique original document IDs for document-level qrels. Deduplicate before top-k scoring. Multiple spans of one relevant document must not multiply relevance credit.
- Add span-level judgments for support, conditions and counterevidence. “Unjudged” is not automatically “irrelevant”; supplement sparse judgments where needed.
- Use a pinned BRIGHT subset for reasoning-intensive retrieval as a separate diagnostic, not a substitute for TL evidence judgments.
- Evaluate repeated terms across unrelated topics, near synonyms, names, rare symbols, number/unit changes, actor reversal, negation and “unless/only” exceptions.
- Score support/qualification recall, conflicting-evidence recall, evidence precision, citation-span validity, dossier completeness and source diversity.
- For answerability, report coverage versus selective error, partial/conflicting/insufficient outcomes and calibration. Include natural-language off-corpus questions sharing common words with unrelated records.
- Freeze generator/checker configuration when evaluating answer quality. Independent human judgments on a sampled set check model-judge bias. The extracting model's self-approval is not a gold label.

Required counterexamples: a relevant item late in row-ID order; an unfiled item; three empty high-scoring facets; minority evidence under a hub; two contradictory assertions with similar vectors; a valid paraphrase with no lexical overlap.

## 4. Source and lifecycle correctness gates

These are exact behavior gates, not statistical retrieval targets:

1. Every claimed byte excerpt resolves exactly in its named source revision. Test Unicode, CRLF, emoji, frontmatter stripping, tables, code fences and repeated text.
2. Same idempotency key/payload does not duplicate acceptance or projection effects. Different payload with the same key conflicts.
3. A failed replacement leaves either the old coherent revision or the new coherent revision; never partial source deletion with missing replacement.
4. Current-state retrieval returns no deleted/superseded evidence as current. Historical retrieval remains explicit.
5. An obsolete worker result cannot overwrite current projections.
6. Dependency removal marks persisted synthesis stale; query compilation cannot treat it as supported.
7. Incremental deterministic projection output agrees with full rebuild after insert/update/delete sequences, within a predeclared numerical vector tolerance.
8. Every result satisfies namespace/access/version scope. ANN filtering and holon expansion cannot cross it.
9. Mandatory context overflow produces explicit failure. Applicable constraints are never silently dropped.
10. Restart verifies evidence hashes, FTS postings and projection manifests, not merely table row counts.

Inject failure before source commit, after commit/before outbox consumption, during vector output, before/after manifest publication, during holon maintenance and during checkpoint/compaction. Test lost worker leases and duplicate delivery.

FTS external-content tables can expose content rows without proving postings are populated. Use FTS integrity facilities plus token MATCH parity against a clean rebuild and deletion/update probes. A matching COUNT(*) alone is not proof.

## 5. Performance and scalability

Use the actual shared ingestion service through CLI/API. Report component microbenchmarks separately.

Measure:

- Capture acknowledgment, lexical readiness and semantic readiness independently.
- Parse/embedding/extraction/reconciliation/index-publication time and model tokens/cost.
- Query p50/p95/p99 including queueing, query encoding, all retrieval branches, graph expansion, reranking and compilation.
- Cold start, warmed steady state and concurrent ingestion/update/delete workloads.
- Throughput at fixed concurrency; query latency at fixed arrival rate; overload/backpressure and oldest job age.
- Peak RSS/VRAM, model memory, index-build peak, WAL peak, final checkpointed DB, payload files, index files, snapshots and temporary generation overlap.
- ANN recall@k versus exact neighbors, together with task nDCG/evidence quality. Good ANN recall does not establish semantic relevance.
- Local maintenance touched objects/bytes, invalidation closure size, queue drain time and full-rebuild equivalence.

Proposed ladder: 2k sources for correctness, 10k for realistic end-to-end behavior, 100k for capacity trend, 250k diagnostic, then 1M sources if predicted resources and prior gates pass. These rungs are planning choices, not current qualification claims. Each includes measured span/edge counts. Final quality query samples cover the full corpus and row-ID distribution.

Benchmark roots are explicit persistent paths. Verify the actual filesystem, free capacity and restart survival. Do not infer tmpfs from `/tmp`, nor Linux filesystem behavior from a Windows path. This plan does not create or move benchmark data.

Do not run the 1M stage if cost/space projections exceed the recorded envelope, the queue is unstable, or a correctness gate fails. Keep artifacts and diagnose the limiting stage. Do not “repair” latency by silently sampling most evidence out of the tested configuration.

## 6. Predictive repair experiment

Dataset: ordered source revisions with meaning-changing and meaning-preserving edits plus expected affected assertions/dossiers. Split by source family and change pattern.

Baselines: full structured rebuild, deterministic dependency repair, cached lexical/vector refresh, and learned repair. Metrics: missed meaningful changes, false invalidations, touched fraction, model tokens, elapsed maintenance, current/historical query accuracy, and accumulated errors over long update sequences.

Hypothesis succeeds only if learned repair reduces cost at the frozen accuracy/regression envelope on held-out data. A predictor that simply avoids work and misses negations fails even with low average embedding residual. Inconclusive results leave deterministic repair active.

## 7. Evidence receipt

Each packet writes delivery and review records under `evidence/query-native/Rxx/`. Qualification additionally retains machine-readable manifest, commands, return codes, logs, corpus/query checksums, per-query outputs, resource series, configuration and known limitations.

Statuses: PASS, FAIL or INCONCLUSIVE. Missing runtime integration, model execution, manifest identity or required metrics is not PASS. A digest binds evidence identity; it does not establish independent trust or correctness.

This document specifies future checks. It does not claim those commands or harnesses already exist.

