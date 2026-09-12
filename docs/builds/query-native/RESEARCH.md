# Trace-Lite query-native memory: research and architectural assessment

## Recommendation

Build TL as an incremental, evidence-preserving semantic organizer. Each source contributes a small graph whose assertions, conditions, references, and evidence remain inspectable. Reconcile that graph with nearby global structure, maintain overlapping subgraph holons, and compile query-specific evidence dossiers. Keep global lexical and vector retrieval independent of the organization so an incorrect filing decision cannot make evidence unreachable.

This is a stronger design than both one-note-one-atom storage and a flat vector database. It is also more specific than “use prediction error” or “add a knowledge graph.” Its value depends on preserving meaning-changing relations, updating them correctly, and improving evidence retrieval at an acceptable total cost. Those outcomes need experiments.

The architectural recommendation is single-node first, with bounded workers, durable jobs, incremental projections, and replaceable index adapters. Network distribution remains an option when measured resource, throughput, or availability requirements justify it. There is no universal document-count threshold.

This assessment combines source inspection at TL `c8e6b701dbd86a412ffec228afde319e122d337e`, read-only reference inspection at TRACE `a24a937009fec41e6dcb6d2ca31d3c1783ac97f7`, saved benchmark artifacts, and primary literature consulted on 2026-09-12. Runtime benchmarks, recovery experiments, and model trials were not rerun. TRACE's ongoing candidate was not inspected or modified. Repository statements below establish implementation paths or recorded results, not fresh runtime qualification.

## 1. What the current evidence establishes

Snapshot limitation: concurrent TL edits appeared after the initial inspection, including changes to ingestion, centroid maintenance, beam selection, corroboration and benchmark artifacts. The findings below refer to the inspected base revision; they are not a verdict on those uncommitted fixes. R00 must re-pin and reproduce the resulting candidate. The score table records values read before those concurrent changes and is not a live scoreboard.

The TL implementation has useful foundations: SQLite, external-content FTS5, an event table, multiple facet memberships, local holons, and a compiler interface. Its present application path does not yet implement the proposed semantic organizer.

| Finding at the inspected TL revision | Evidence | Architectural consequence |
|---|---|---|
| Ingestion replaces each source with one atom | `src/trace_lite/api/app.py::ingest_note` | Span-capable schema is underused; mixed-topic evidence cannot be independently organized |
| Replacement crosses multiple commits | `store/database.py::delete_atom, insert_atom, insert_event`; `filing/engine.py::assign_facets` | Process failure can expose incomplete replacement |
| Note ingestion refreshes every assigned centroid and warms the full router | `api/app.py::ingest_note, ingest, _on_watcher_sync` | Repeated collection-wide work and blocked queries |
| Empty facets retain centroid blobs | `filing/engine.py::refresh_centroid` returns early | Derived routing state can outlive its support |
| Beam slices ranked facets before dropping empty shortlists | `router/beam.py::candidate_ids` | Ghost facets can consume all beam slots |
| Facet candidates use bounded row-ID ordering | `filing/engine.py::_atoms_in_facets`; `router/beam.py` | Round-robin fairness across facets does not establish recall within large facets |
| Quality profile scans all dense rows; latency profile samples rows | `router/cascade.py::__init__`; `router/hybrid.py::_dense_ranked` | Quality and speed claims must name their profile |
| Corpus-wide lexical support helps determine “answerable” | `router/lexical.py::has_lexical_support`; `router/cascade.py::route` | Candidate relevance and answer support remain conflated |
| Compilation ranks evidence against `goal_id` | `cordis/compiler.py::compile_context` | Goal identifier is substituted for objective text |
| API status hardcodes trusted/organized state | `api/app.py::status` | Clients cannot observe actual projection lag |
| Gate uses lexical screening and a fixed HMAC secret | `cordis/gate.py` | Interface conformance does not establish semantic verification or secure authorization |

There are important corrections to the supplied audits. TL already has source IDs and span fields. Its dense candidate limit is not intrinsically a first-2,000-row restriction: the latency profile separately samples rows across the matrix. Its compiler sorts by salience and skips over-budget evidence; it does not apply the claimed Jaccard threshold as a universal rejection rule. Named holons remain local, but cross-source facets already exist. Hashing is a legitimate approximation; the issue is the chosen representation and its measured utility, not a mathematical prohibition on hashing.

The saved BEIR artifacts have changed since the quoted discussion:

| Dataset | Saved nDCG@10 | Saved Recall@10 | Recorded comparison floor |
|---|---:|---:|---:|
| SciFact | 0.6589 | 0.7765 | 0.6500 |
| FiQA | 0.2495 | 0.3085 | 0.3800 |
| NFCorpus | 0.3188 | 0.1446 | 0.3200 |

These are the current files in `evidence/beir/`, not scores reproduced in this assessment. The adapter checks input checksums but its output lacks a complete candidate/configuration identity. The comparison TRACE numbers are constants in the adapter, not a fresh matched TRACE run. SciFact's point estimate clears its stored floor; the confidence interval spans it. This is insufficient to declare broad parity.

The scale benchmark calls bulk storage and bulk facet assignment with short synthetic records, refreshes centroids after bulk insertion, and starts router warming after its ingestion timer. It selects the latency profile. It therefore measures a useful component workload, but cannot qualify realistic API ingestion, semantic extraction, time-to-searchable, concurrent updates, or full-corpus semantic recall. Neither 100k nor 250k is an established safe capacity for arbitrary notes. “World-class,” “rock-solid,” and “fully qualified” exceed these artifacts.

## 2. What should transfer from TRACE

TRACE's architecture distinguishes durable evidence from rebuildable semantic projections. Its source/version anchors, idempotency, proposal status, and recovery intent address classes of problems TL currently underspecifies. Its scale packets also identify costs from redundant structural edges, duplicate text representations, synchronous indexing, and checkpoint placement. Adopt the contracts and lessons; do not copy every table or assume its qualification succeeded.

The inspected TRACE `atomize()` explicitly returns positions into normalized text and labels locations `char:start-end`. It uses paragraphs, bullets, and length-bounded splitting. That is a structural baseline, not implemented predictive segmentation. A TL migration must define coordinate spaces directly rather than copying these positions into fields named bytes.

TRACE's checked-in qualification state still records unmet throughput/storage gates and refers to another candidate checkout. Those records cannot describe the live overnight run conclusively. TL's plan must stand on its own measurements.

Five transfers matter most:

1. Stable source/version identity and recoverable provenance.
2. Transactional replacement with replayable, idempotent projection work.
3. Distinct statuses for proposed, validated, stale, superseded, and rejected interpretations.
4. Storage attribution and recovery parity that include canonical evidence, not only indexes.
5. Candidate-bound qualification using representative data and explicit durability settings.

Avoid transferring a universal event for every derived adjacency or a second canonical text store. Source order can usually be recovered from ordinal/location fields. Explicit semantic relationships deserve records because they add information beyond order.

## 3. Adaptive atomization: serious mechanism, conditional benefit

The correct objective is retrievable evidence with enough context to retain its meaning. A sentence is not necessarily an independent claim, and high token surprise is not necessarily a semantic boundary. A negation can be unsurprising yet decisive; a rare name can be surprising yet irrelevant to segmentation.

Meta-Chunking provides a direct precedent for uncertainty-based segmentation, dynamic merging, and contextual completion. Its perplexity method is more specific than splitting whenever loss spikes: the published method analyzes sentence-level patterns including local minima. Its rewriting component should remain a derived search aid in TL rather than replacing source evidence.[^1]

Comparative evidence is mixed. Qu et al. report that semantic chunking does not consistently justify its additional cost.[^2] A 2026 comparison separates in-document retrieval from in-corpus retrieval: structural methods were strong for corpus retrieval, while LumberChunker performed best for the tested within-document setting.[^3] These results support an ablation, not a universal rejection or endorsement of adaptive methods.

Late chunking addresses another failure mode: passage embeddings lose antecedent context when encoding happens only after segmentation. It contextualizes tokens before pooling passage embeddings. That requires an appropriate encoder interface; an ordinary endpoint returning one vector per string is insufficient.[^4]

**Design judgment:** use two levels of identity. Stable evidence spans identify source occurrences; versioned segmentation views and holons propose useful retrieval units. Initial spans follow trustworthy structure: paragraphs, sentences, code syntax, table headers/rows, conversation turns, and log events. Refine grouping with contextual representations and optional predictive scores. Keep an escape path to surrounding source content.

Compare structural-only, predictive segmentation, contextualized embeddings, and relational extraction separately. Otherwise a combined system can improve without revealing which expensive feature helped. Include code, tables, mixed-language text, long argument chains, and minimal meaning-changing edits. Measure evidence completeness and downstream answer support, not boundary agreement alone.

## 4. From local graphs to global memory

The local graph should preserve what a source asserts, including uncertainty about extraction. A small representation can be expressive without demanding exhaustive formalization.

For “A improves throughput; this benefit disappears under high contention; batching partly restores it,” create an assertion frame with subject, predicate, object, metric, polarity, and scope. Attach the high-contention qualification to that assertion. Represent restoration with its own evidence and degree. Do not invent “batching reduces cache-line bouncing” unless the passage states or supports that mechanism.

Claims with multiple arguments are best represented as assertion nodes plus role links. This avoids forcing every n-ary statement into disconnected triples. Conditions, units, comparison baselines, versions, and temporal scope attach to the assertion. Each extracted interpretation links to source spans and an extraction run. Unresolved pronouns and ambiguous entity matches remain explicit alternatives.

GraphRAG demonstrates graph construction and community summaries for global questions over a corpus. Its reported global-summary benefits do not establish precise factual retrieval, cheap updates, or TL-scale performance.[^5] HippoRAG 2 is especially relevant because it integrates passage context with graph retrieval; its analysis shows the risks of structure-focused approaches losing performance outside their original task setting.[^6]

**Design judgment:** store passages alongside relational structure and let both participate in retrieval. Relations should improve retrieval of conditions, definitions, and counterevidence. They should not become the only representation of a source.

Reconciliation uses candidate retrieval followed by a typed decision:

| Operation | Meaning | Required safeguard |
|---|---|---|
| Link | Distinct objects have a relation | Evidence and relation status |
| Group | Objects participate in a shared holon | Overlap allowed; membership is not agreement |
| Resolve entity | Mentions refer to the same entity | Context/version checks; reversible mapping |
| Equate claims | Assertions express the same proposition under compatible scope | Polarity, roles, quantities, conditions and time compared |
| Derive | A new interpretation follows from selected evidence | Separate derived status and complete lineage |

A semantic similarity edge never silently becomes support, contradiction, or causation. A source's assertion of causation is distinguishable from validated causation. Repeated copies of one source do not constitute independent corroboration.

## 5. Holons as reusable subgraphs

Retain discourse holons, extend with thematic and argument/claim holons, and produce ephemeral query dossiers. A local discourse span still benefits from ordering and same-source validation. A thematic holon may span sources and contain disagreement. A claim holon groups evidence around an assertion under specified conditions.

Holons need typed membership, member versions, descriptors, representative passages, and explicit internal relations. A centroid is one discovery aid. It cannot encode all modes of a broad cluster or tell agreement from disagreement. Maintain a few representatives and dispersion statistics; split broad organization only when evidence and downstream use justify the change.

Use membership tables rather than materializing every pair of members. For a group of m passages, a membership representation needs O(m) references, whereas a complete pair graph needs O(m²) edges. Recursive holon containment should form a bounded DAG, although the wider evidence graph may contain cycles.

Do not physically fuse distinct source occurrences. Deduplicate identical content payloads when appropriate while retaining occurrence, source, time, and authority records. Query dossiers may combine many sources freely because each component remains addressable.

Saving a dossier does not make its generated conclusions true. Retention, validation, and authority are separate decisions. Persist repeated useful composites selectively; otherwise query history can swamp the original evidence and feed the system's own guesses back as corroboration.

## 6. Stigmergic organization and prediction-driven repair

The productive interpretation of stigmergy is that accumulated organization guides future work. Existing definitions suggest entity bindings; unresolved disagreements attract discriminating evidence; frequently useful holons suggest candidate neighborhoods. New evidence modifies that structure.

The danger is self-reinforcement. A popular but wrong cluster can attract evidence, generate summaries, and then appear better supported because those summaries are retrieved. Keep source-only retrieval, reversible assignments, explicit novelty outcomes, and evaluation queries that do not follow established cluster vocabulary.

Prediction error should be a vector of signals: boundary uncertainty, unmatched entities, incompatible roles, polarity/scope changes, relation conflicts, and downstream retrieval failures. A low embedding residual is not a sufficient test. A policy may use these signals to spend less work, but must still validate critical fields.

Differential dataflow establishes that incremental iterative computations can propagate changes rather than recomputing everything. It does not solve semantic extraction, but it is a useful computational precedent for additions, retractions, and dependency-aware updates.[^7]

**Research hypothesis:** a learned controller can choose the smallest sufficient repair after a source change, saving extraction and reconciliation work while preserving sensitivity to changed meaning. Test it against full rebuild, fixed-rule incremental repair, and cached retrieval. Train only after collecting repair traces. Evaluate on held-out source families and update types, including negation, actor reversal, temporal changes, and paraphrase.

For now, implement deterministic dependency propagation and bounded local recomputation. Learned scheduling is an optional later packet; correctness must not depend on it.

## 7. Single-node execution and storage consistency

SQLite WAL supports concurrent readers and one writer, but long read transactions can delay checkpoint progress. Separate read connections and short writer transactions are useful; simply deleting the application mutex would leave shared index state unsafe.[^8]

Use a durable outbox in the same transaction as accepted source updates. Background workers claim versioned jobs, retry idempotently, and reject obsolete results. Model inference and graph construction run outside the writer transaction. The writer validates expected source revision before publishing results.

Keep two observable freshness levels: evidence durably captured/lexically searchable and semantic organization complete. Queries receive index watermarks and coverage information. Recent lexical evidence remains available while graph/vector work is pending. A caller requiring a particular semantic revision can wait within a bound or receive an explicit incomplete result.

External ANN files cannot share SQLite's transaction atomically. Build immutable index segments, persist a manifest, then publish the active generation in SQLite. Keep old segments until readers release them. Apply tombstone/version filters before returning evidence, and use a bounded delta index for fresh embeddings. Startup checks manifest identity and replays pending work. File publication durability and cleanup must be tested separately on Windows and Linux.

Select a durability profile explicitly. WAL with synchronous=NORMAL differs from FULL for power-loss durability. Bulk acknowledgment must mean the batch's chosen persistence contract has completed. A post-commit PASSIVE checkpoint is not proof of bounded WAL size under all workloads.[^8] Current SQLite documentation also identifies a WAL-reset race and fixed/backported versions; record the actually loaded runtime and require a patched version before concurrent writer/checkpointer qualification.[^8]

The new provenance design deliberately retains source payloads plus derived retrieval material. That can cost more than the old atom-text-only design. Store source bytes once, avoid copying whole sources into events, and measure FTS/cache/index duplication explicitly. “One canonical source” does not imply “one physical copy of every indexed token.”

## 8. Retrieval and index choice

Retrieve a union of global lexical, global vector, and holon-guided candidates. Bound each route, deduplicate by evidence identity, expand required local context and typed relations, then rerank and pack a dossier. Preserve evidence disagreement and diversity. Exact identifiers may use a fast path, but lexical relevance alone does not prove answer sufficiency.

Use a candidate-relevance state when no support evaluation exists. A support stage may report supported, partial, conflicting, or insufficient evidence with a named method and scope. Do not replace the corpus-wide lexical leak with a universal term-overlap requirement: that would reject genuine paraphrases once learned representations are introduced.

HNSW is an approximate navigation structure, distinct from the semantic evidence graph.[^9] USearch is a reasonable first embedded candidate because its documented APIs include adding/removing items and persistence. Its binding-level feature differences matter for filtered retrieval and must be tested.[^10] Keep exact vector search as a recall oracle on bounded subsets. DiskANN is a possible later option if measured memory pressure justifies a more involved disk-backed index.[^11]

Do not call sqlite-vec an HNSW implementation by association. Its original release explicitly provided brute-force search; verify the selected current release and its index behavior before adopting it as an ANN backend.[^12]

Learned embeddings should be allowed. Start with a small reproducible reference candidate, such as BGE-small-en-v1.5, against lexical-only and a stronger representation chosen for the actual corpus. The model card documents 384 dimensions and a 512-token input limit; this is a candidate specification, not a latency or retrieval guarantee.[^13] Pin tokenizer, weights, runtime, prompts, pooling, normalization and quantization. Never silently truncate long spans or mix encoder generations.

## 9. Capacity is a workload, not a document count

Let D be sources, a the average spans per source, d vector dimensions, b bytes per coordinate, and A=D×a. Raw vector bytes are A×d×b. One million sources averaging eight spans produce eight million vectors; at 384 dimensions and two bytes per coordinate, those alone occupy 6.144 GB (decimal). At one byte they occupy 3.072 GB. This excludes graph links, text, SQLite indexes, dictionaries, models, worker memory, and rebuild overlap.

Actual work also depends on extracted assertions, memberships, degree distribution, update frequency, and query concurrency. A giant generic entity can dominate graph traversal. Bound expansions with relation-aware relevance, preserve mandatory qualification links, and report truncation. Avoid all-pairs local comparison for very long sources.

No evidence currently supports a promised 2 ms full-corpus query, 500 MB total memory at the richer 1M-source workload, or a universal requirement for distributed deployment above 50M sources. Select operating targets after bounded probes, then freeze them before qualification. Report the quality/latency/cost frontier rather than weakening retrieval invisibly to meet a number.

## 10. What would constitute convincing qualification

BEIR is useful for heterogeneous passage/document retrieval; it does not test temporal repair, evidence sufficiency, or causal relation extraction.[^14] BRIGHT adds reasoning-intensive retrieval challenges but still does not substitute for TL's update and provenance tests.[^15] Temporal-memory work such as Zep provides a relevant comparison for retaining changing relationships, with claims evaluated in its own workloads.[^16]

Use a combined evaluation program:

- Public frozen retrieval datasets, mapping spans back to unique document IDs without duplicate relevance credit.
- Source-span evidence judgments for conditions, counterevidence, and multi-source questions.
- Minimal-edit update suites with independently checked expected changes.
- ANN recall against exact search under filters, updates and deletions.
- Real API/CLI ingestion with time-to-lexical and time-to-semantic readiness.
- Crash injection and restart parity for canonical content, jobs, FTS and projection publication.
- Cold/warm query latency under concurrent ingestion, including queueing and embedding time.
- Storage/RSS/VRAM and extraction cost, including transient rebuild peaks.

Compare lexical-only; structural passages plus learned hybrid retrieval; that baseline plus local graphs; that system plus cross-source holons; and predictive refinement. A graph or predictor is promoted only if it improves the designated quality/cost objective without unacceptable regression on the other tasks. Inconclusive evidence is an inconclusive result.

The best initial milestone is a small end-to-end slice: two sources with related claims and conflicting conditions, an update that changes one condition, a rebuilt local/global neighborhood, and a dossier showing the current evidence and disagreement. That proves the architecture's intended behavior before multiplying it to millions of sources.

## Sources

[^1]: Zhao et al. *Meta-Chunking: Learning Text Segmentation and Semantic Completion via Logical Perception*, v3, 21 May 2025, sections 3–5. https://arxiv.org/html/2410.12788v3
[^2]: Qu et al. *Is Semantic Chunking Worth the Computational Cost?*, 2024 preprint / NAACL Findings 2025. https://arxiv.org/abs/2410.13070
[^3]: Zhou et al. *Beyond Chunk-Then-Embed: A Comprehensive Taxonomy and Evaluation of Document Chunking Strategies for Information Retrieval*, 19 February 2026, section 4 and Table 3. Preprint evidence. https://arxiv.org/html/2602.16974v1
[^4]: Günther et al. *Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models*, 2024. https://arxiv.org/abs/2409.04701
[^5]: Edge et al. *From Local to Global: A GraphRAG Approach to Query-Focused Summarization*, 2024, v2. https://arxiv.org/html/2404.16130v2
[^6]: Jiménez Gutiérrez et al. *From RAG to Memory: Non-Parametric Continual Learning for Large Language Models*, ICML 2025, sections 3–5. https://arxiv.org/html/2502.14802v2
[^7]: McSherry et al. *Differential Dataflow*, CIDR 2013. https://www.microsoft.com/en-us/research/publication/differential-dataflow/
[^8]: SQLite. *Write-Ahead Logging*, sections 2, 6 and 11; current documentation accessed 2026-09-12. https://sqlite.org/wal.html
[^9]: Malkov and Yashunin. *Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs*, 2016. https://arxiv.org/abs/1603.09320
[^10]: USearch project documentation, accessed 2026-09-12; binding capability matrix. https://github.com/unum-cloud/usearch
[^11]: Microsoft. *DiskANN*, project documentation, accessed 2026-09-12. https://github.com/microsoft/DiskANN
[^12]: Garcia. *Introducing sqlite-vec v0.1.0*, 2024; historical release scope. Current KNN documentation: https://alexgarcia.xyz/sqlite-vec/features/knn.html . Release: https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/index.html
[^13]: BAAI. *bge-small-en-v1.5 model card*, accessed 2026-09-12. https://huggingface.co/BAAI/bge-small-en-v1.5
[^14]: Thakur et al. *BEIR: A Heterogenous Benchmark for Zero-shot Evaluation of Information Retrieval Models*, 2021. https://arxiv.org/abs/2104.08663
[^15]: Su et al. *BRIGHT: A Realistic and Challenging Benchmark for Reasoning-Intensive Retrieval*, 2024. https://arxiv.org/abs/2407.12883
[^16]: Rasmussen et al. *Zep: A Temporal Knowledge Graph Architecture for Agent Memory*, 2025. https://arxiv.org/abs/2501.13956

Local sources: TL files and saved artifacts identified above; TRACE `docs/v2-architecture.md`, `src/trace/v2/atomizer.py`, and `docs/builds/p11-p1-scale-qualification/{STATE.md,packets/}` at the inspected reference checkout; desktop `03_trace_lite_filing_cabinet_core_architecture.md` (design conversation, especially messages 5 and 11). Desktop and repository blueprints are design evidence, not implementation certification.
