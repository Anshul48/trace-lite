"""Generator script for the 320-Query In-Domain Frozen Benchmark Fixture (private_indomain_v1.json)."""

import json
import hashlib
from pathlib import Path

# 80 rich, unambiguous technical paragraphs
PARAGRAPHS = [
    # Spine Architecture (atoms 0 - 9)
    "The Spine store (spine.sqlite3) is an append-only ledger that preserves raw immutable source documents and atoms.",
    "The Atomizer splits ingested document text into individual atoms while tracking exact character start and end offsets and SHA-256 content hashes.",
    "The SpineStore includes an SQLite FTS5 virtual table called atom_fts with unicode61 porter tokenization for lexical BM25 search.",
    "Spine database write transactions guarantee ACID crash consistency and ensure atoms are never mutated or destructively deleted.",
    "Every SourceArtifact records a unique artifact_id, optional document_name, source_uri, SHA-256 content hash, and timestamp.",
    "The Spine ledger records immutable SpineEvent entries such as artifact.ingested, index.built, and tree.activated.",
    "The hot inbox search capability enables immediate lexical and flat vector queries on unorganized pending captures before consolidation.",
    "Ingestion in TraceLite has zero LLM overhead because text parsing and atom storage execute without blocking on LLM or embedding calls.",
    "The Spine database path can be overridden via the TraceLiteConfig spine_db_name configuration setting.",
    "Atom character offset boundaries char_offset_start and char_offset_end are computed relative to the original raw document text.",

    # Cortex Forest Index (atoms 10 - 19)
    "The ForestIndex SQLite database (cortex.sqlite3) manages disposable hierarchical tree structures and summary nodes.",
    "Every TreeNode contains node_id, tree_id, level, node_type, atom_ids, summary_text, parent_id, children_ids, and summary_provenance.",
    "Tree hierarchy levels range from level 0 for source leaves to level 1 and higher for cluster summaries, culminating in a single root summary.",
    "The RAPTOR clustering pipeline applies UMAP dimension reduction and HDBSCAN clustering to organize related atoms into branches.",
    "The maximum tree summary depth and maximum child fan-out per cluster summary are configured via raptor_max_depth and max_children.",
    "Every summary node records provenance: source for raw leaves, llm for generated summaries, and retry for corrected summaries.",
    "Deterministic quality gates validate summaries by checking minimum character length, non-empty text, absence of think tags, and lexical overlap.",
    "Pending queues in Cortex include pending_source_atoms for unassigned captures and pending_assignments for tree-assigned atoms.",
    "The ForestRouter assigns pending source atoms to target trees using cosine similarity and LLM-generated topic titles.",
    "Activation of an index build atomically replaces tree nodes and updates the active build manifest without partial downtime.",

    # Vector Store & Embeddings (atoms 20 - 29)
    "LanceDB serves as the embedded vector store storing dense floating-point embeddings for all tree leaf nodes and cluster summaries.",
    "The SentenceTransformerEmbedder uses the all-MiniLM-L6-v2 model producing 384-dimensional dense semantic vectors.",
    "LanceDB vector tables use versioned collection naming cortex_nodes_build_id to allow atomic collection swapping upon build activation.",
    "Vector dimensional parity checks verify that all stored embeddings match the expected embedding dimension before candidate index activation.",
    "The embedding model supports background daemon warmup to eliminate cold-start latency on initial user queries.",
    "If an index build candidate fails validation, the vector store automatically deletes candidate vectors and rolls back to the previous snapshot.",
    "The MockVectorStore backend provides an in-memory dictionary implementation for fast, isolated unit testing without disk I/O.",
    "Vector search normalizes embedding vectors and computes cosine similarity distances between query vectors and candidate nodes.",
    "Full consolidation re-embeds all source atoms and completely rebuilds the dense vector collection from the Spine ground truth.",
    "Vector snapshot and restore handlers safeguard against partial index corruption during unexpected system interruptions.",

    # Tri-Channel Retrieval & Lattice Engine (atoms 30 - 39)
    "The LatticeEngine executes tri-channel hybrid retrieval combining Path A tree traversal, Path B flat vector search, and Path C lexical search.",
    "Path A performs top-down RAPTOR tree traversal using beam search to locate the most relevant summary branches and leaves.",
    "Path B performs flat nearest-neighbor dense vector search in LanceDB filtered strictly to level 0 leaf nodes.",
    "Path C executes SQLite FTS5 BM25 lexical keyword matching against the atom_fts full-text index.",
    "In hybrid mode, candidate scores are fused using the linear formula 0.40 flat vector score plus 0.30 tree score plus 0.30 BM25 score.",
    "TraceLite retrieval supports four distinct execution modes: hybrid, tree, flat, and lexical.",
    "Sufficiency gating evaluates candidate scores to classify query sufficiency as answerable, ambiguous, or insufficient_evidence.",
    "A query is classified as ambiguous when the score difference between the top two retrieved evidence candidates is less than 0.015.",
    "A query is classified as insufficient_evidence when the highest candidate score is below the 0.20 confidence threshold.",
    "Each retrieved EvidenceItem contains character offsets, content hash, document name, source URI, and fused channel scores.",

    # Vault Watcher & Plugins (atoms 40 - 49)
    "The VaultWatcher monitors local directories such as Obsidian vaults and automatically ingests modified markdown files.",
    "The Obsidian plugin interfaces with TraceLite via local REST API endpoints to provide semantic search within the Obsidian UI.",
    "Periodic filesystem scanning computes SHA-256 hashes of markdown files to detect additions and edits without duplicate ingestion.",
    "File ingestion debounce timers ensure partial writes from active text editors do not trigger premature indexing.",
    "The watcher ignores hidden configuration folders such as .obsidian and skips non-markdown file extensions.",
    "Auto-consolidation can be enabled in VaultWatcher to automatically trigger tree organization after batch note ingestion.",
    "The REST API provides endpoints for /api/v1/vault/sync, /api/v1/trees/{tree_id}/dag, and /api/projects.",
    "CORS headers in the API server explicitly allow the app://obsidian.md origin for seamless desktop integration.",
    "The DAG visualizer serializes tree hierarchies into nodes and links format for interactive web-based graph visualization.",
    "The /api/export endpoint generates a complete zip archive backup containing the Spine and Cortex databases.",

    # Historical & Revisions (atoms 50 - 59)
    "The legacy V1 prototype used flat JSON files and an unindexed in-memory list for candidate retrieval.",
    "The V2 production architecture replaced legacy JSON files with transactional SQLite Spine and LanceDB vector tables.",
    "Legacy V1 permitted unverified fallback summaries to be activated when the LLM service was unavailable.",
    "The V2 architecture enforces fail-closed indexing, discarding candidate builds if any summary fails deterministic quality gates.",
    "The legacy V1 system executed embedding and tree generation synchronously during file ingestion.",
    "V2 decoupled raw ingestion from derived organization, guaranteeing millisecond ingestion without blocking on AI models.",
    "Superseded RFC 101 proposed a purely lexical BM25 ranking algorithm for all knowledge base queries.",
    "Approved RFC 104 superseded RFC 101 by establishing tri-channel hybrid fusion combining dense, tree, and BM25 scores.",
    "The deprecated manual folder categorization system was replaced by automated ForestRouter semantic clustering and LLM naming.",
    "The initial prototype 128-dimensional mock embedder was upgraded to the 384-dimensional all-MiniLM-L6-v2 model in production.",

    # Security & Providers (atoms 60 - 69)
    "TraceLite supports multiple LLM providers including Ollama local models, OpenAI, Anthropic Claude, and Groq.",
    "Provider preflight verification tests credentials and network availability before initiating any derived index build.",
    "Diagnostic redaction sanitizes error logs to ensure API keys, authorization headers, and raw prompts are never exposed.",
    "The trust boundary marks an active index untrusted if legacy fallback or passthrough summaries are detected.",
    "Provider API keys are stored securely using platform keyring services or encrypted local configuration files.",
    "When Ollama is unavailable, the preflight diagnostic outputs specific guidance on launching the Ollama service and pulling models.",
    "The ProjectManager provides complete multi-workspace isolation with independent storage directories for each project.",
    "The model warmup daemon runs in a non-blocking background thread to initialize SentenceTransformer weights.",
    "A QueryBlockedError is raised when queries attempt to execute against unorganized pending captures without the force flag.",
    "Diagnostic sink handlers collect structured failure codes and retry attempt counts during summary generation.",

    # Benchmarks & CLI (atoms 70 - 79)
    "TraceLite provides developer CLI commands including ingest, query, organize, consolidate, reindex, status, and benchmark.",
    "The BenchmarkRunner engine evaluates retrieval accuracy across frozen benchmark datasets and outputs structured JSON reports.",
    "Retrieval evaluation calculates Recall@5, Recall@20, nDCG@10, nDCG@30, Complete Gold Coverage, Precision, and Abstention Accuracy.",
    "The benchmark matrix defines target pass gates across 8 evaluation categories including Direct Lookup and Multi-Hop Evidence.",
    "Latency tracking measures query response times and reports p50, p95, p99, mean, min, and max milliseconds.",
    "The MemoryTracker utility measures process resident set size (RSS) in megabytes using psutil.",
    "The StorageTracker calculates total disk space and disk bytes consumed per atom across Spine, Cortex, and LanceDB.",
    "The completion CLI command generates shell autocompletion scripts for bash, zsh, fish, and powershell.",
    "The --json CLI flag outputs machine-readable JSON responses for programmatic integration into CI and automated workflows.",
    "The --force query option bypasses pending capture blocking to search strictly within the last verified active index.",
]


def generate_benchmark_cases() -> list[dict]:
    cases = []

    # Category 1: Direct Lookup (40 cases) - Single needle lookup
    cat1_data = [
        ("What database preserves raw immutable source documents and atoms?", "atom-0", "Spine store"),
        ("What component splits document text while tracking character offsets and SHA-256 hashes?", "atom-1", "Atomizer"),
        ("What virtual table in SQLite performs lexical BM25 search with unicode61 porter tokenization?", "atom-2", "atom_fts table"),
        ("What do Spine write transactions guarantee regarding crash consistency?", "atom-3", "ACID consistency"),
        ("What metadata does every SourceArtifact record?", "atom-4", "SourceArtifact metadata"),
        ("What ledger entries are recorded in the Spine ledger?", "atom-5", "SpineEvent entries"),
        ("What capability allows immediate lexical and vector search on unorganized pending captures?", "atom-6", "Hot inbox search"),
        ("Why does ingestion in TraceLite have zero LLM overhead?", "atom-7", "Zero LLM ingestion"),
        ("Which configuration setting overrides the Spine database filename?", "atom-8", "spine_db_name config"),
        ("Relative to what are atom char_offset_start and char_offset_end computed?", "atom-9", "Char offset reference"),
        ("Which SQLite database manages disposable hierarchical tree structures?", "atom-10", "ForestIndex cortex.sqlite3"),
        ("What fields does every TreeNode contain in its schema?", "atom-11", "TreeNode schema fields"),
        ("What are the hierarchy levels in a Tree from leaves to root?", "atom-12", "Tree hierarchy levels"),
        ("What clustering algorithms does the RAPTOR pipeline apply to organize atoms?", "atom-13", "UMAP and HDBSCAN"),
        ("Which configuration parameters control the maximum tree depth and child fan-out?", "atom-14", "raptor_max_depth and max_children"),
        ("What summary provenance values are recorded for tree nodes?", "atom-15", "Summary provenance values"),
        ("What deterministic quality gates are applied to validate generated summaries?", "atom-16", "Summary quality validation"),
        ("What are the two pending queue tables in Cortex?", "atom-17", "Cortex pending queues"),
        ("What component assigns pending source atoms to target trees using cosine similarity?", "atom-18", "ForestRouter"),
        ("How does index build activation replace tree nodes without partial downtime?", "atom-19", "Atomic build activation"),
        ("Which embedded vector database stores dense embeddings for leaves and summaries?", "atom-20", "LanceDB vector store"),
        ("Which embedding model is used by the SentenceTransformerEmbedder producing 384 dimensions?", "atom-21", "all-MiniLM-L6-v2"),
        ("What collection naming format is used in LanceDB for atomic swapping?", "atom-22", "cortex_nodes_build_id"),
        ("What check verifies stored embeddings match the expected dimension before activation?", "atom-23", "Vector dimensional parity"),
        ("What mechanism eliminates cold-start latency for embeddings on startup?", "atom-24", "Background daemon warmup"),
        ("What happens to candidate vectors in LanceDB if an index build fails validation?", "atom-25", "Vector store rollback"),
        ("Which in-memory backend provides dictionary vector storage for unit tests?", "atom-26", "MockVectorStore"),
        ("What distance metric is computed between query vectors and candidate nodes?", "atom-27", "Cosine similarity distance"),
        ("What does full consolidation do to the dense vector collection?", "atom-28", "Re-embeds and rebuilds dense vector collection"),
        ("What handlers protect against vector index corruption during interruptions?", "atom-29", "Vector snapshot and restore handlers"),
        ("What tri-channel engine combines Path A, Path B, and Path C retrieval?", "atom-30", "LatticeEngine"),
        ("What traversal algorithm does Path A use to search RAPTOR summary branches?", "atom-31", "Top-down beam search"),
        ("What does Path B flat search filter strictly to in LanceDB?", "atom-32", "Level 0 leaf nodes"),
        ("What does Path C lexical search execute against?", "atom-33", "atom_fts full-text index"),
        ("What linear scoring weights are used in hybrid retrieval mode?", "atom-34", "0.40 flat + 0.30 tree + 0.30 BM25"),
        ("What four retrieval execution modes are supported by TraceLite?", "atom-35", "hybrid, tree, flat, lexical"),
        ("What does sufficiency gating classify query states into?", "atom-36", "answerable, ambiguous, insufficient_evidence"),
        ("What candidate score delta threshold triggers an ambiguous query classification?", "atom-37", "Score difference under 0.015"),
        ("What confidence threshold triggers an insufficient_evidence classification?", "atom-38", "Score below 0.20"),
        ("What fields are included in each hydrated EvidenceItem citation?", "atom-39", "EvidenceItem citation fields"),
    ]
    for i, (q, gold, needle) in enumerate(cat1_data, start=1):
        cases.append({
            "id": f"case-cat01-{i:02d}",
            "category": "direct_lookup",
            "query": q,
            "gold_atom_ids": [gold],
            "expected_abstention": False,
            "difficulty": "easy",
            "metadata": {"category_id": "CAT_01", "target_needle": needle},
        })

    # Category 2: Chronology & Order (40 cases) - Sequential workflows
    cat2_pairs = [
        ("What is the exact workflow order from raw text ingestion to Spine storage and queuing?", ["atom-0", "atom-1", "atom-7"], "Ingestion sequence"),
        ("What is the sequence of tree hierarchy from raw source leaves to root summary?", ["atom-11", "atom-12"], "Hierarchy levels"),
        ("What is the sequence of RAPTOR clustering from UMAP reduction to summary validation?", ["atom-13", "atom-14", "atom-16"], "RAPTOR pipeline"),
        ("What steps occur during staged routing before index activation?", ["atom-17", "atom-18", "atom-19"], "Routing and activation sequence"),
        ("What is the sequence of vector candidate preparation from embedding to parity check?", ["atom-21", "atom-22", "atom-23"], "Vector staging"),
        ("What happens during full consolidation from re-embedding to snapshot safeguard?", ["atom-28", "atom-29"], "Consolidation sequence"),
        ("How does the LatticeEngine combine Path A tree search and Path B flat search?", ["atom-30", "atom-31", "atom-32"], "Retrieval path order"),
        ("What is the evaluation sequence from candidate fusion to sufficiency gating?", ["atom-34", "atom-36", "atom-39"], "Scoring to citation sequence"),
        ("What is the sequence of file detection, debouncing, and ingestion in VaultWatcher?", ["atom-40", "atom-42", "atom-43"], "Watcher scan sequence"),
        ("How does the watcher transition from scanning to auto-consolidation?", ["atom-42", "atom-45"], "Watcher auto-consolidation order"),
        ("What is the sequence of API endpoints from vault sync to DAG visualization?", ["atom-46", "atom-48"], "API workflow"),
        ("What is the chronological progression from V1 flat JSON to V2 SQLite Spine?", ["atom-50", "atom-51"], "Storage evolution order"),
        ("How did summary validation evolve from V1 fallback to V2 fail-closed gating?", ["atom-52", "atom-53"], "Validation evolution order"),
        ("What is the chronological transition from synchronous ingestion to decoupled organization?", ["atom-54", "atom-55"], "Ingestion architecture evolution"),
        ("What is the order of RFC progression from lexical RFC 101 to hybrid RFC 104?", ["atom-56", "atom-57"], "RFC evolution sequence"),
        ("How did tree assignment evolve from manual folders to automated ForestRouter?", ["atom-58", "atom-18"], "Routing evolution sequence"),
        ("What was the embedding upgrade order from 128-dim mock to 384-dim MiniLM?", ["atom-59", "atom-21"], "Embedding upgrade sequence"),
        ("What steps occur during provider preflight verification before derived builds?", ["atom-60", "atom-61"], "Preflight verification order"),
        ("How does diagnostic sanitization precede error logging in provider preflight?", ["atom-61", "atom-62"], "Diagnostic logging sequence"),
        ("What sequence of checks determines whether an active index is untrusted?", ["atom-63", "atom-53"], "Trust verification sequence"),
        ("How does the model warmup daemon initialize before handling queries?", ["atom-67", "atom-24"], "Warmup initialization order"),
        ("What is the sequence of benchmark execution from runner setup to latency tracking?", ["atom-71", "atom-72", "atom-74"], "Benchmark execution sequence"),
        ("How does the benchmark matrix verify category pass gates across metrics?", ["atom-72", "atom-73"], "Benchmark gate evaluation sequence"),
        ("What sequence of profiling runs from RSS memory tracking to disk bytes calculation?", ["atom-75", "atom-76"], "Profiling metric sequence"),
        ("How does CLI status reporting precede force query execution on pending captures?", ["atom-70", "atom-79"], "CLI query workflow"),
        ("What happens between atomizing source text and generating atom_fts lexical entries?", ["atom-1", "atom-2"], "Spine atom indexing sequence"),
        ("How are SpineEvents appended following SourceArtifact storage?", ["atom-4", "atom-5"], "Spine event logging sequence"),
        ("What is the order of character offset calculation during document atomization?", ["atom-1", "atom-9"], "Atomization offset order"),
        ("How are pending source atoms queued in Cortex after Spine storage?", ["atom-0", "atom-17"], "Queue transition sequence"),
        ("What is the order of UMAP dimension reduction before HDBSCAN clustering in RAPTOR?", ["atom-13", "atom-14"], "RAPTOR clustering steps"),
        ("How does summary quality validation precede provenance marking in tree nodes?", ["atom-15", "atom-16"], "Summary quality step"),
        ("What is the order of vector candidate creation before collection activation?", ["atom-22", "atom-19"], "Vector activation sequence"),
        ("How does the vector store recover when an index build fails validation?", ["atom-23", "atom-25"], "Vector rollback sequence"),
        ("What is the sequence from query vector generation to cosine distance computation?", ["atom-21", "atom-27"], "Vector query order"),
        ("How does Path C lexical search execute alongside Path A tree traversal?", ["atom-31", "atom-33"], "Tri-channel parallel paths"),
        ("What is the sequence of checking sufficiency thresholds after fusing channel scores?", ["atom-34", "atom-37", "atom-38"], "Sufficiency calculation order"),
        ("How does markdown hash checking precede debounced file ingestion in VaultWatcher?", ["atom-42", "atom-43"], "Watcher file check order"),
        ("What is the order of operations when creating an index backup zip via /api/export?", ["atom-0", "atom-10", "atom-49"], "Backup generation order"),
        ("How does the ProjectManager isolate storage before provider configuration is applied?", ["atom-64", "atom-66"], "Workspace isolation setup order"),
        ("What is the order of evaluating category metrics before declaring benchmark pass gates?", ["atom-72", "atom-73", "atom-74"], "Benchmark matrix verification order"),
    ]
    for i, (q, golds, desc) in enumerate(cat2_pairs, start=1):
        cases.append({
            "id": f"case-cat02-{i:02d}",
            "category": "chronology",
            "query": q,
            "gold_atom_ids": golds,
            "expected_abstention": False,
            "difficulty": "medium",
            "metadata": {"category_id": "CAT_02", "sequence_desc": desc},
        })

    # Category 3: Contradiction & Revision (40 cases) - Architectural revisions
    cat3_pairs = [
        ("What replaced the legacy flat JSON storage in the V2 production architecture?", ["atom-50", "atom-51"], "Storage replacement"),
        ("How does V2 fail-closed summary gating override the legacy V1 fallback summary behavior?", ["atom-52", "atom-53"], "Summary validation policy override"),
        ("What replaces synchronous ingestion embedding in the V2 decoupled architecture?", ["atom-54", "atom-55"], "Ingestion decoupling revision"),
        ("How does approved RFC 104 supersede the purely lexical ranking proposed in RFC 101?", ["atom-56", "atom-57"], "RFC 104 superseding RFC 101"),
        ("What automated routing component superseded the deprecated manual folder categorization?", ["atom-58", "atom-18"], "ForestRouter superseding manual folders"),
        ("What model replaced the prototype 128-dimensional mock embedder in production?", ["atom-59", "atom-21"], "MiniLM replacing mock embedder"),
        ("How does Spine append-only storage contradict destructive in-place document mutation?", ["atom-0", "atom-3"], "Append-only vs in-place mutation"),
        ("Why does zero-LLM ingestion contradict blocking LLM calls during document upload?", ["atom-7", "atom-54"], "Zero-LLM vs blocking ingestion"),
        ("How does disposable Cortex forest indexing differ from immutable Spine ground truth?", ["atom-0", "atom-10"], "Cortex disposable vs Spine immutable"),
        ("How does fail-closed summary rejection contradict accepting unverified AI responses?", ["atom-16", "atom-52"], "Fail-closed vs unverified summaries"),
        ("What distinguishes versioned LanceDB collection swapping from in-place vector overwrites?", ["atom-20", "atom-22"], "Collection swapping vs overwrites"),
        ("Why does tri-channel fusion contradict using single-channel nearest-neighbor retrieval alone?", ["atom-30", "atom-34"], "Hybrid fusion vs single-channel"),
        ("How does sufficiency abstention contradict forcing an unconfident answer on out-of-scope queries?", ["atom-36", "atom-38"], "Sufficiency abstention vs forced answer"),
        ("How does debounced note ingestion prevent capturing incomplete file edits?", ["atom-40", "atom-43"], "Debounce vs immediate partial capture"),
        ("Why are unvalidated summaries rejected even when LLM provider output is non-empty?", ["atom-16", "atom-53"], "Quality gating vs raw LLM completion"),
        ("How does atomic build activation prevent partial index downtime during reindexing?", ["atom-10", "atom-19"], "Atomic activation vs partial downtime"),
        ("Why does multi-workspace ProjectManager isolation contradict global shared state?", ["atom-66", "atom-8"], "Isolated projects vs shared state"),
        ("How does diagnostic redaction prevent leaking credentials in error logs?", ["atom-61", "atom-62"], "Redacted logs vs plain text leakage"),
        ("Why are legacy passthrough summaries untrusted under the active trust boundary?", ["atom-63", "atom-15"], "Trust boundary vs passthrough summaries"),
        ("How does the --force flag contrast with standard fail-closed query blocking on pending captures?", ["atom-68", "atom-79"], "Force flag vs query blocking"),
        ("What replaced single-threaded in-memory vector indexing in production?", ["atom-50", "atom-20"], "LanceDB replacing memory index"),
        ("How does tri-channel scoring revise purely dense vector search?", ["atom-32", "atom-34"], "Tri-channel revising pure dense"),
        ("Why does Spine preserve deleted artifacts rather than removing them from disk?", ["atom-0", "atom-4"], "Preserved artifacts vs deletion"),
        ("How does the hot inbox allow searching unorganized notes without violating fail-closed indexing?", ["atom-6", "atom-68"], "Hot inbox vs query blocking"),
        ("What prevents invalid embedding dimensions from polluting the active vector collection?", ["atom-23", "atom-25"], "Parity check vs vector corruption"),
        ("How does background model warmup replace blocking on-demand model initialization?", ["atom-24", "atom-67"], "Background warmup vs blocking load"),
        ("Why does the LATTICE beam search supersede naive flat tree scans?", ["atom-31", "atom-12"], "Beam search vs naive tree scan"),
        ("How does the ambiguity threshold distinguish borderline candidates from distinct answers?", ["atom-36", "atom-37"], "Ambiguity threshold vs single answer"),
        ("Why does VaultWatcher exclude .obsidian configuration files from ingestion?", ["atom-41", "atom-44"], "Filtered paths vs blind file scan"),
        ("How does the /api/v1/vault/sync endpoint automate manual note import commands?", ["atom-46", "atom-70"], "Automated sync vs manual commands"),
        ("Why was the original BM25-only architecture abandoned in favor of hybrid fusion?", ["atom-56", "atom-34"], "BM25-only vs hybrid fusion"),
        ("How does provider preflight prevent starting builds with invalid API keys?", ["atom-61", "atom-64"], "Preflight verification vs runtime failure"),
        ("Why are candidate vectors rolled back when summary validation fails?", ["atom-16", "atom-25"], "Vector rollback on summary failure"),
        ("How does SpineEvent tracking replace unmonitored database operations?", ["atom-3", "atom-5"], "Event logging vs unmonitored writes"),
        ("Why does UMAP dimension reduction precede HDBSCAN in the RAPTOR pipeline?", ["atom-13", "atom-14"], "UMAP before HDBSCAN clustering"),
        ("How does provenance tracking prevent mixing human source notes with AI summaries?", ["atom-11", "atom-15"], "Provenance separation vs mixed text"),
        ("Why does full consolidation rebuild from Spine rather than updating Cortex in place?", ["atom-10", "atom-28"], "Spine rebuild vs Cortex in-place update"),
        ("How does Path B leaf filtering ensure tree summaries do not compete in flat vector search?", ["atom-30", "atom-32"], "Leaf filtering vs summary vector competition"),
        ("Why does the benchmark matrix test abstention rather than only precision and recall?", ["atom-72", "atom-73"], "Abstention testing vs pure recall"),
        ("How does the --json CLI option supersede human-oriented terminal tables for automation?", ["atom-70", "atom-78"], "JSON output vs terminal table"),
    ]
    for i, (q, golds, desc) in enumerate(cat3_pairs, start=1):
        cases.append({
            "id": f"case-cat03-{i:02d}",
            "category": "contradiction",
            "query": q,
            "gold_atom_ids": golds,
            "expected_abstention": False,
            "difficulty": "medium",
            "metadata": {"category_id": "CAT_03", "revision_topic": desc},
        })

    # Category 4: Cross-Domain (40 cases) - Bridging subsystems
    cat4_pairs = [
        ("How does Spine SQLite storage interact with LanceDB vector collection management?", ["atom-0", "atom-20"], "Spine and LanceDB interaction"),
        ("How does the Atomizer character offset tracking link with EvidenceItem citation formatting?", ["atom-1", "atom-39"], "Atomizer and citation link"),
        ("How does atom_fts lexical search coordinate with LanceDB dense search in hybrid fusion?", ["atom-2", "atom-34"], "Lexical and dense fusion"),
        ("How does VaultWatcher automatic ingestion trigger Cortex pending queue updates?", ["atom-40", "atom-17"], "Watcher and Cortex queues"),
        ("How do Obsidian plugin REST API requests authenticate and map to ProjectManager workspaces?", ["atom-41", "atom-66"], "Plugin and ProjectManager"),
        ("How does SentenceTransformer embedding generation feed into RAPTOR tree clustering?", ["atom-21", "atom-13"], "Embeddings and RAPTOR clustering"),
        ("How does summary quality validation in Cortex affect active build activation in Spine?", ["atom-16", "atom-19"], "Validation and activation"),
        ("How does the hot inbox search leverage atom_fts before trees are organized in Cortex?", ["atom-6", "atom-2"], "Hot inbox and atom_fts"),
        ("How does provider preflight credential checking safeguard LLM summary generation in RAPTOR?", ["atom-61", "atom-16"], "Preflight and RAPTOR summaries"),
        ("How does the DAG visualizer API transform TreeNode parent-child relationships into graphs?", ["atom-48", "atom-11"], "DAG API and TreeNode model"),
        ("How does BenchmarkRunner execution test the LatticeEngine tri-channel retrieval pipeline?", ["atom-71", "atom-30"], "Benchmark and LatticeEngine"),
        ("How does MemoryTracker RSS profiling monitor LanceDB and SQLite memory footprints?", ["atom-75", "atom-20"], "MemoryTracker and database stores"),
        ("How does StorageTracker measure disk bytes across Spine, Cortex, and LanceDB tables?", ["atom-76", "atom-0"], "StorageTracker and Spine store"),
        ("How does the model warmup daemon reduce latency for initial LatticeEngine queries?", ["atom-67", "atom-30"], "Warmup and LatticeEngine latency"),
        ("How does diagnostic redaction protect provider API keys during failed index build logging?", ["atom-62", "atom-64"], "Redaction and API keys"),
        ("How does CORS header configuration in the API server enable Obsidian web client communication?", ["atom-47", "atom-41"], "CORS and Obsidian plugin"),
        ("How does the /api/export backup endpoint package both Spine and Cortex SQLite databases?", ["atom-49", "atom-10"], "Export endpoint and Cortex DB"),
        ("How do SpineEvent audit logs record tree.activated events triggered by Cortex builds?", ["atom-5", "atom-19"], "SpineEvent and Cortex activation"),
        ("How does the CLI --force flag interact with Cortex pending queues during query execution?", ["atom-79", "atom-17"], "Force flag and Cortex queues"),
        ("How does the CLI benchmark command invoke the evaluation metric aggregator?", ["atom-70", "atom-72"], "CLI benchmark and metric aggregator"),
        ("How does ForestRouter semantic routing use SentenceTransformer embeddings to assign atoms?", ["atom-18", "atom-21"], "Router and SentenceTransformer"),
        ("How does Path A tree traversal leverage TreeNode summary texts generated by LLMs?", ["atom-31", "atom-11"], "Tree traversal and summary text"),
        ("How does Path B leaf vector search query LanceDB collections created during index build?", ["atom-32", "atom-22"], "Path B and LanceDB collections"),
        ("How does sufficiency gating use confidence thresholds to control EvidenceItem return?", ["atom-36", "atom-38"], "Sufficiency gating and confidence"),
        ("How does VaultWatcher hash scanning prevent duplicate atom creation in the Atomizer?", ["atom-42", "atom-1"], "Watcher hash and Atomizer"),
        ("How does debounced note ingestion prevent incomplete write transactions in Spine?", ["atom-43", "atom-3"], "Debounce and Spine transactions"),
        ("How does the REST API /api/v1/vault/sync trigger background ingestion into SpineStore?", ["atom-46", "atom-0"], "Sync API and SpineStore"),
        ("How does Ollama provider availability error handling prompt user action before RAPTOR builds?", ["atom-65", "atom-13"], "Ollama error and RAPTOR build"),
        ("How does the trust boundary inspect summary provenance before allowing LatticeEngine queries?", ["atom-63", "atom-15"], "Trust boundary and provenance"),
        ("How does ProjectManager directory isolation configure custom paths for Spine and Cortex?", ["atom-66", "atom-8"], "ProjectManager and custom paths"),
        ("How does latency tracking in benchmarks measure the execution time of hybrid fusion?", ["atom-74", "atom-34"], "Latency tracking and hybrid fusion"),
        ("How does shell autocompletion generate dynamic suggestions for CLI subcommands?", ["atom-77", "atom-70"], "Autocompletion and CLI commands"),
        ("How does the --json CLI flag format output from DatabaseStatus and QueryResult objects?", ["atom-78", "atom-39"], "JSON flag and QueryResult"),
        ("How does full consolidation coordinate rebuilding between ForestIndex and LanceDB?", ["atom-28", "atom-10"], "Consolidation in Forest and LanceDB"),
        ("How does vector snapshot rollback restore LanceDB state when Cortex validation fails?", ["atom-29", "atom-16"], "Snapshot rollback and validation"),
        ("How does beam search in Path A select candidate summaries based on cosine distance?", ["atom-31", "atom-27"], "Beam search and cosine distance"),
        ("How does Path C lexical search utilize porter tokenization for keyword queries?", ["atom-33", "atom-2"], "Path C and porter tokenization"),
        ("How does ambiguity detection compare candidate scores derived from tri-channel fusion?", ["atom-37", "atom-34"], "Ambiguity detection and hybrid score"),
        ("How does the Obsidian plugin render interactive graphs generated by serialize_dag?", ["atom-41", "atom-48"], "Obsidian graph and serialize_dag"),
        ("How does category pass gate verification validate retrieval across all 8 matrix categories?", ["atom-73", "atom-72"], "Pass gates and benchmark matrix"),
    ]
    for i, (q, golds, desc) in enumerate(cat4_pairs, start=1):
        cases.append({
            "id": f"case-cat04-{i:02d}",
            "category": "cross_domain",
            "query": q,
            "gold_atom_ids": golds,
            "expected_abstention": False,
            "difficulty": "medium",
            "metadata": {"category_id": "CAT_04", "cross_domain_theme": desc},
        })

    # Category 5: Global Context (40 cases) - Broad architectural understanding
    cat5_triplets = [
        ("What are the fundamental architectural principles of the Spine immutable storage layer?", ["atom-0", "atom-1", "atom-3"], "Spine storage architecture"),
        ("How does the ForestIndex organize disposable hierarchical knowledge trees?", ["atom-10", "atom-11", "atom-12"], "ForestIndex tree hierarchy"),
        ("What is the complete clustering and summarization workflow in the RAPTOR pipeline?", ["atom-13", "atom-14", "atom-16"], "RAPTOR clustering workflow"),
        ("How does LanceDB manage dense vector embeddings and collection lifecycle in TraceLite?", ["atom-20", "atom-22", "atom-23"], "LanceDB vector management"),
        ("What is the comprehensive tri-channel hybrid retrieval architecture in LatticeEngine?", ["atom-30", "atom-34", "atom-35"], "Tri-channel retrieval architecture"),
        ("How does sufficiency gating classify query confidence and detect unanswerable queries?", ["atom-36", "atom-37", "atom-38"], "Sufficiency gating architecture"),
        ("What mechanisms does VaultWatcher provide for automatic synchronization of markdown vaults?", ["atom-40", "atom-42", "atom-43"], "VaultWatcher synchronization"),
        ("How does the TraceLite REST API provide endpoints for sync, visualization, and projects?", ["atom-46", "atom-47", "atom-48"], "REST API architecture"),
        ("What major architectural enhancements distinguish V2 from the legacy V1 prototype?", ["atom-50", "atom-51", "atom-53"], "V1 vs V2 architectural evolution"),
        ("How did RFC 104 and decoupled ingestion establish the modern retrieval architecture?", ["atom-55", "atom-57", "atom-58"], "RFC 104 and decoupled ingestion"),
        ("What security and verification protocols govern LLM provider integration?", ["atom-60", "atom-61", "atom-62"], "Provider security protocols"),
        ("How does the trust boundary enforce fail-closed safety and workspace isolation?", ["atom-63", "atom-66", "atom-68"], "Trust boundary and isolation"),
        ("What comprehensive evaluation metrics and pass gates are enforced by the benchmark suite?", ["atom-71", "atom-72", "atom-73"], "Benchmark evaluation suite"),
        ("What operational profiling tools monitor memory, storage, and latency in TraceLite?", ["atom-74", "atom-75", "atom-76"], "Operational profiling tools"),
        ("How does the CLI interface expose ingestion, query, organization, and benchmark commands?", ["atom-70", "atom-78", "atom-79"], "CLI command suite"),
        ("How does the Atomizer compute cryptographic hashes and character offsets for source text?", ["atom-1", "atom-4", "atom-9"], "Atomizer provenance protocol"),
        ("What role do pending queues play in decoupling ingestion from derived tree construction?", ["atom-7", "atom-17", "atom-18"], "Pending queues and decoupling"),
        ("How does atomic build activation ensure zero-downtime index swapping in Cortex and LanceDB?", ["atom-10", "atom-19", "atom-22"], "Zero-downtime build activation"),
        ("How does the SentenceTransformer embedder interact with background warmup and vector search?", ["atom-21", "atom-24", "atom-27"], "Embedding lifecycle"),
        ("How do Path A, Path B, and Path C provide complementary evidence in hybrid retrieval?", ["atom-31", "atom-32", "atom-33"], "Complementary retrieval paths"),
        ("How does EvidenceItem hydration assemble citations with offsets, hashes, and scores?", ["atom-1", "atom-34", "atom-39"], "EvidenceItem hydration"),
        ("How does VaultWatcher coordinate scanning, filtering, and auto-consolidation?", ["atom-40", "atom-44", "atom-45"], "Watcher pipeline coordination"),
        ("What role does CORS configuration play in securing Obsidian plugin communication?", ["atom-41", "atom-46", "atom-47"], "CORS and plugin integration"),
        ("How did summary validation evolve from permissive fallbacks to deterministic quality gates?", ["atom-16", "atom-52", "atom-53"], "Quality gate evolution"),
        ("How did the vector dimension upgrade from 128 to 384 improve semantic representation?", ["atom-21", "atom-23", "atom-59"], "Vector dimension upgrade"),
        ("How does provider preflight prevent pipeline failures when Ollama or API keys fail?", ["atom-61", "atom-64", "atom-65"], "Preflight error prevention"),
        ("How does the model warmup daemon ensure fast response times for first queries?", ["atom-24", "atom-30", "atom-67"], "Warmup latency reduction"),
        ("How does QueryBlockedError protect users from querying dirty or unindexed state?", ["atom-6", "atom-68", "atom-79"], "Query blocking and force search"),
        ("How does the BenchmarkRunner harness calculate Recall, nDCG, and Complete Gold Coverage?", ["atom-71", "atom-72", "atom-74"], "Benchmark metric calculation"),
        ("How does StorageTracker measure space efficiency across the database storage layers?", ["atom-0", "atom-20", "atom-76"], "Storage efficiency measurement"),
        ("What are the key invariants of the Spine append-only event ledger?", ["atom-0", "atom-3", "atom-5"], "Spine event ledger invariants"),
        ("How do TreeNode hierarchy levels structure information from granular leaves to root?", ["atom-11", "atom-12", "atom-15"], "Hierarchy structuring principles"),
        ("How does RAPTOR clustering bound depth and fan-out to maintain efficient trees?", ["atom-13", "atom-14", "atom-16"], "RAPTOR bounding principles"),
        ("How does versioned collection swapping in LanceDB prevent vector index corruption?", ["atom-22", "atom-23", "atom-25"], "Collection swapping safety"),
        ("How does linear fusion balance dense semantic vectors with exact lexical BM25 matching?", ["atom-2", "atom-32", "atom-34"], "Linear fusion balance"),
        ("How does the sufficiency gating state machine govern question answering confidence?", ["atom-36", "atom-37", "atom-38"], "Sufficiency state machine"),
        ("How does filesystem event debouncing prevent indexing half-written notes from editors?", ["atom-40", "atom-42", "atom-43"], "Editor write debouncing"),
        ("What data structures does serialize_dag use to represent knowledge graphs for the web UI?", ["atom-11", "atom-48", "atom-46"], "DAG data structures"),
        ("How does multi-workspace ProjectManager configuration isolate independent project data?", ["atom-8", "atom-66", "atom-70"], "Project isolation configuration"),
        ("How does the benchmark matrix test retrieval robustness across diverse query categories?", ["atom-71", "atom-73", "atom-74"], "Benchmark matrix robustness"),
    ]
    for i, (q, golds, desc) in enumerate(cat5_triplets, start=1):
        cases.append({
            "id": f"case-cat05-{i:02d}",
            "category": "global_context",
            "query": q,
            "gold_atom_ids": golds,
            "expected_abstention": False,
            "difficulty": "medium",
            "metadata": {"category_id": "CAT_05", "global_topic": desc},
        })

    # Category 6: Multi-Hop Evidence (40 cases) - Multi-step reasoning chains
    cat6_chains = [
        ("Tracing from document ingestion to atom_fts search, what components process the text?", ["atom-0", "atom-1", "atom-2"], "Ingestion to FTS hop"),
        ("Tracing from pending source capture to validated tree summary, what steps are taken?", ["atom-17", "atom-18", "atom-16"], "Pending to summary hop"),
        ("Tracing from SentenceTransformer embedding to LanceDB candidate collection swap, what occurs?", ["atom-21", "atom-22", "atom-23"], "Embedding to collection hop"),
        ("Tracing from user query to tri-channel scoring and citation offsets, how is evidence assembled?", ["atom-30", "atom-34", "atom-39"], "Query to citation hop"),
        ("Tracing from markdown file modification to auto-consolidation, what is the event chain?", ["atom-40", "atom-42", "atom-45"], "Watcher to consolidation hop"),
        ("Tracing the evolution of storage from V1 JSON to V2 SQLite and LanceDB tables, what changed?", ["atom-50", "atom-51", "atom-20"], "Storage evolution hop"),
        ("Tracing summary generation from provider preflight to deterministic quality gates, how is safety enforced?", ["atom-61", "atom-16", "atom-53"], "Preflight to quality gate hop"),
        ("Tracing query execution on unorganized notes from fail-closed blocking to --force override, how does it work?", ["atom-6", "atom-68", "atom-79"], "Query blocking to force hop"),
        ("Tracing benchmark execution from fixture loading to metric calculation and gate checks, what is the flow?", ["atom-71", "atom-72", "atom-73"], "Benchmark execution hop"),
        ("Tracing character offsets from raw source document to EvidenceItem citation, how is provenance preserved?", ["atom-1", "atom-9", "atom-39"], "Offset provenance hop"),
        ("Tracing tree construction from UMAP dimensionality reduction to root summary creation, what is the hierarchy?", ["atom-13", "atom-14", "atom-12"], "UMAP to root summary hop"),
        ("Tracing dense retrieval from query embedding to Path B leaf vector filtering in LanceDB, what happens?", ["atom-21", "atom-27", "atom-32"], "Query embedding to Path B hop"),
        ("Tracing lexical retrieval from query text to SQLite FTS5 BM25 match score calculation, how is Path C executed?", ["atom-2", "atom-33", "atom-34"], "Query text to Path C hop"),
        ("Tracing sufficiency evaluation from fused scores to ambiguous or insufficient_evidence states, how is confidence decided?", ["atom-34", "atom-37", "atom-38"], "Fused score to sufficiency hop"),
        ("Tracing file synchronization from Obsidian plugin sync endpoint to Spine write transaction, how are notes ingested?", ["atom-41", "atom-46", "atom-3"], "Plugin sync to Spine hop"),
        ("Tracing architectural revisions from RFC 101 BM25 to RFC 104 hybrid retrieval and decoupled ingestion, how did it evolve?", ["atom-56", "atom-57", "atom-55"], "RFC revision to decoupled ingestion hop"),
        ("Tracing error handling from failed Ollama preflight to redacted diagnostic logging, how are secrets protected?", ["atom-65", "atom-61", "atom-62"], "Ollama error to redacted log hop"),
        ("Tracing background initialization from model warmup daemon to zero-latency first query execution, how does it work?", ["atom-67", "atom-24", "atom-30"], "Warmup daemon to zero latency hop"),
        ("Tracing profiling operations from memory RSS tracking to disk bytes per atom measurement, what metrics are captured?", ["atom-75", "atom-76", "atom-72"], "Memory RSS to disk bytes hop"),
        ("Tracing CLI execution from shell autocompletion to JSON formatted benchmark output, how do tools integrate?", ["atom-77", "atom-70", "atom-78"], "Shell completion to JSON output hop"),
        ("Tracing atom storage from unique artifact_id creation to SpineEvent logging, what records are written?", ["atom-4", "atom-1", "atom-5"], "Artifact ID to SpineEvent hop"),
        ("Tracing tree node creation from level 0 source leaves to summary provenance tagging, how is lineage stored?", ["atom-11", "atom-12", "atom-15"], "Leaf to provenance hop"),
        ("Tracing vector build candidate creation to failure rollback and snapshot restoration, how is safety guaranteed?", ["atom-22", "atom-23", "atom-25"], "Vector candidate to rollback hop"),
        ("Tracing beam search traversal in Path A from root summary to matching leaf nodes, how does traversal descend?", ["atom-12", "atom-31", "atom-39"], "Root summary to leaf descent hop"),
        ("Tracing note editing in Obsidian from debounced write detection to SHA-256 hash update, how does watcher react?", ["atom-40", "atom-43", "atom-42"], "Note edit to hash update hop"),
        ("Tracing DAG visualization from TreeNode database query to web REST response, how are links formatted?", ["atom-10", "atom-11", "atom-48"], "TreeNode to DAG REST response hop"),
        ("Tracing project creation from ProjectManager workspace setup to custom Spine database creation, how is isolation achieved?", ["atom-66", "atom-8", "atom-0"], "Workspace setup to Spine creation hop"),
        ("Tracing summary generation failure from retry counter exhaustion to candidate build rejection, what stops activation?", ["atom-16", "atom-19", "atom-53"], "Retry exhaustion to rejection hop"),
        ("Tracing benchmark latency measurement from query dispatch to p95 percentile aggregation, how is speed assessed?", ["atom-71", "atom-74", "atom-72"], "Query dispatch to p95 latency hop"),
        ("Tracing hot inbox retrieval from pending capture insertion to unorganized search warning return, how is freshness provided?", ["atom-6", "atom-17", "atom-79"], "Pending capture to hot inbox hop"),
        ("Tracing atom_fts table creation to BM25 query score normalization in SQLite, how does lexical scoring work?", ["atom-2", "atom-33", "atom-34"], "atom_fts to score normalization hop"),
        ("Tracing ForestRouter placement from cosine similarity scoring to LLM topic label generation, how are trees created?", ["atom-18", "atom-21", "atom-10"], "Cosine similarity to tree naming hop"),
        ("Tracing LanceDB collection creation from build_id manifest generation to atomic pointer swap, how are vectors activated?", ["atom-22", "atom-19", "atom-20"], "Build manifest to vector activation hop"),
        ("Tracing beam search branch pruning in Path A to candidate scoring in the LatticeEngine, how is precision achieved?", ["atom-31", "atom-34", "atom-36"], "Branch pruning to candidate scoring hop"),
        ("Tracing vault note sync from markdown parsing to atom creation and DAG link updating, how is state kept consistent?", ["atom-42", "atom-1", "atom-48"], "Markdown parsing to DAG link hop"),
        ("Tracing trust verification from legacy summary detection to QueryBlockedError raising, how is trust protected?", ["atom-63", "atom-52", "atom-68"], "Legacy summary to query blocking hop"),
        ("Tracing encrypted provider key storage to sanitized diagnostic emission during failed calls, how are keys kept safe?", ["atom-64", "atom-61", "atom-62"], "Encrypted key to sanitized diagnostic hop"),
        ("Tracing benchmark category filtering from case category tag to pass gate evaluation, how are scores calculated?", ["atom-73", "atom-72", "atom-71"], "Case category to pass gate hop"),
        ("Tracing storage footprint profiling from atom count to average disk bytes consumed per atom, how is efficiency measured?", ["atom-1", "atom-76", "atom-75"], "Atom count to bytes per atom hop"),
        ("Tracing CLI flag handling from --json request to machine-readable JSON exit, how are programmatic pipelines supported?", ["atom-78", "atom-70", "atom-72"], "CLI json flag to pipeline exit hop"),
    ]
    for i, (q, golds, desc) in enumerate(cat6_chains, start=1):
        cases.append({
            "id": f"case-cat06-{i:02d}",
            "category": "multi_hop",
            "query": q,
            "gold_atom_ids": golds,
            "expected_abstention": False,
            "difficulty": "hard",
            "metadata": {"category_id": "CAT_06", "reasoning_chain": desc},
        })

    # Category 7: Historical & Versioned (40 cases) - Prototype vs V2 distinctions
    cat7_pairs = [
        ("How did storage format change between the V1 prototype and V2 production database?", ["atom-50", "atom-51"], "Storage format transition"),
        ("How did summary validation rules change from permissive V1 to fail-closed V2?", ["atom-52", "atom-53"], "Summary validation transition"),
        ("How did ingestion architecture evolve from synchronous V1 to decoupled V2?", ["atom-54", "atom-55"], "Ingestion architecture transition"),
        ("How did ranking algorithms evolve from RFC 101 BM25 to RFC 104 hybrid fusion?", ["atom-56", "atom-57"], "Ranking algorithm transition"),
        ("How did tree assignment evolve from manual folders to automated ForestRouter in V2?", ["atom-58", "atom-18"], "Tree assignment transition"),
        ("How did embedding models upgrade from the 128-dim prototype to 384-dim MiniLM?", ["atom-59", "atom-21"], "Embedding dimension transition"),
        ("What distinguished the legacy in-memory candidate list from LanceDB vector tables?", ["atom-50", "atom-20"], "In-memory list vs LanceDB"),
        ("What distinguished V1 passthrough summaries from V2 quality-verified summaries?", ["atom-52", "atom-16"], "Passthrough vs quality-verified"),
        ("What distinguished V1 blocking ingestion from V2 zero-LLM millisecond writes?", ["atom-54", "atom-7"], "Blocking vs zero-LLM ingestion"),
        ("What distinguished RFC 101 lexical-only retrieval from RFC 104 tri-channel fusion?", ["atom-56", "atom-30"], "RFC 101 vs RFC 104 tri-channel"),
        ("What distinguished manual tree classification from automated UMAP/HDBSCAN clustering?", ["atom-58", "atom-13"], "Manual classification vs UMAP/HDBSCAN"),
        ("What distinguished the 128-dim mock vector store from 384-dim SentenceTransformer embeddings?", ["atom-59", "atom-27"], "Mock vector vs SentenceTransformer"),
        ("How did Spine SQLite ACID transactions replace unversioned flat file storage?", ["atom-3", "atom-50"], "ACID transactions vs flat file"),
        ("How did deterministic quality gates replace legacy unverified LLM fallback output?", ["atom-16", "atom-52"], "Quality gates vs unverified LLM"),
        ("How did pending queues in Cortex enable asynchronous organization after raw ingestion?", ["atom-17", "atom-55"], "Pending queues and asynchronous organization"),
        ("How did tri-channel fusion weights 0.40/0.30/0.30 improve upon pure BM25 search?", ["atom-34", "atom-56"], "Tri-channel weights vs pure BM25"),
        ("How did automated topic title generation replace hardcoded manual folder names?", ["atom-18", "atom-58"], "Topic title generation vs manual folders"),
        ("How did LanceDB collection swapping replace dangerous in-place vector overwrites?", ["atom-22", "atom-50"], "Collection swapping vs vector overwriting"),
        ("How did fail-closed indexing prevent activating bad summaries unlike the V1 prototype?", ["atom-53", "atom-52"], "Fail-closed vs permissive activation"),
        ("How did zero-LLM ingestion solve the upload latency bottlenecks of the V1 prototype?", ["atom-7", "atom-54"], "Zero-LLM vs upload bottlenecks"),
        ("How did RFC 104 integrate Path A, Path B, and Path C into a unified scoring framework?", ["atom-57", "atom-30"], "RFC 104 unified scoring framework"),
        ("How did automated semantic routing eliminate manual taxonomy maintenance?", ["atom-18", "atom-58"], "Automated routing vs manual taxonomy"),
        ("How did the upgrade to all-MiniLM-L6-v2 improve semantic similarity precision?", ["atom-21", "atom-59"], "MiniLM upgrade vs precision"),
        ("How did immutable SpineEvent logging replace silent database updates in V1?", ["atom-5", "atom-50"], "SpineEvent logging vs silent updates"),
        ("How did provenance tagging (source/llm/retry) introduce auditability absent in V1?", ["atom-15", "atom-52"], "Provenance tagging vs unrecorded summaries"),
        ("How did hot inbox search allow querying pending captures without blocking on rebuilds?", ["atom-6", "atom-54"], "Hot inbox vs blocking rebuilds"),
        ("How did sufficiency gating and abstention eliminate hallucinations present in V1?", ["atom-36", "atom-50"], "Sufficiency gating vs V1 hallucinations"),
        ("How did VaultWatcher debouncing solve partial file ingestion issues seen in early testing?", ["atom-43", "atom-40"], "Debouncing vs partial ingestion"),
        ("How did REST API CORS configuration resolve Obsidian plugin connectivity challenges?", ["atom-47", "atom-41"], "CORS configuration vs plugin connectivity"),
        ("How did ProjectManager workspace isolation replace the single shared database of V1?", ["atom-66", "atom-50"], "Workspace isolation vs shared database"),
        ("How did provider preflight eliminate mid-build credential failures common in prototype runs?", ["atom-61", "atom-52"], "Preflight verification vs mid-build failures"),
        ("How did diagnostic redaction address security risks of logging raw API keys in V1?", ["atom-62", "atom-50"], "Diagnostic redaction vs plain text logs"),
        ("How did QueryBlockedError enforce safety against querying dirty unindexed state?", ["atom-68", "atom-54"], "QueryBlockedError vs dirty state queries"),
        ("How did the benchmark matrix formalize pass gates that were unmeasured in V1?", ["atom-73", "atom-71"], "Benchmark matrix vs unmeasured prototype"),
        ("How did memory and storage profiling verify the efficiency gains of V2 SQLite tables?", ["atom-75", "atom-51"], "Profiling vs V2 SQLite efficiency"),
        ("How did atomic tree replacement in Cortex eliminate index reading race conditions?", ["atom-19", "atom-50"], "Atomic replacement vs race conditions"),
        ("How did beam search in RAPTOR trees improve query latency compared to linear tree scans?", ["atom-31", "atom-50"], "Beam search vs linear tree scans"),
        ("How did BM25 score normalization ensure equal weighting with dense vector scores?", ["atom-34", "atom-56"], "BM25 normalization vs raw scores"),
        ("How did background model warmup eliminate initial query latency spikes?", ["atom-24", "atom-54"], "Model warmup vs latency spikes"),
        ("How did the --force CLI flag provide an explicit escape hatch for searching verified indexes?", ["atom-79", "atom-68"], "Force flag vs query blocking"),
    ]
    for i, (q, golds, desc) in enumerate(cat7_pairs, start=1):
        cases.append({
            "id": f"case-cat07-{i:02d}",
            "category": "historical",
            "query": q,
            "gold_atom_ids": golds,
            "expected_abstention": False,
            "difficulty": "medium",
            "metadata": {"category_id": "CAT_07", "version_comparison": desc},
        })

    # Category 8: Insufficient Evidence / Abstention (40 cases) - Unanswerable / out of scope
    cat8_queries = [
        ("What quantum orchard encryption protocol is used for cold storage key management?", "Quantum orchard encryption protocol"),
        ("Which blockchain smart contract handles decentralized atom validation in TraceLite?", "Blockchain validation"),
        ("What warp drive cooling algorithm regulates database temperature under load?", "Warp drive cooling"),
        ("How do alien linguistic translation matrices decode non-human documents?", "Alien translation matrices"),
        ("What recipe produces the most delicious chocolate fudge cake?", "Chocolate fudge cake recipe"),
        ("Which interstellar telescope observed the gravitational lensing of galaxy NGC-4592?", "Interstellar telescope"),
        ("What are the official rules and point scoring system for underwater hockey?", "Underwater hockey rules"),
        ("Which mythical creatures inhabit the enchanted whispering forest of Eldoria?", "Mythical creatures of Eldoria"),
        ("What is the optimal fertilizer composition for growing hydroponic tomatoes on Mars?", "Mars hydroponic tomatoes"),
        ("How do medieval blacksmiths forge Damascus steel swords with folded carbon patterns?", "Damascus steel forging"),
        ("Which submarine holds the record for the deepest manned descent in the Mariana Trench?", "Mariana Trench submarine"),
        ("What are the molecular genetics mechanisms behind bioluminescent jellyfish glow?", "Bioluminescent jellyfish"),
        ("How do time dilation effects impact atomic clocks aboard GPS satellites?", "GPS satellite time dilation"),
        ("What is the historical origin of the Venetian carnival masquerade ball traditions?", "Venetian carnival history"),
        ("Which Renaissance painter created the famous masterpiece depicting the School of Athens?", "School of Athens painter"),
        ("What aerodynamic wing shape allows the peregrine falcon to dive at terminal velocity?", "Falcon dive aerodynamics"),
        ("How do ancient Egyptian pyramid builders align monuments to cardinal constellations?", "Pyramid constellation alignment"),
        ("What chemical reactions convert grape must into sparkling champagne in French vineyards?", "Champagne fermentation chemistry"),
        ("Which chess opening strategy offers the highest win rate against the Sicilian Defense?", "Chess Sicilian defense counter"),
        ("How do geothermal power plants extract superheated steam from tectonic fault lines?", "Geothermal steam extraction"),
        ("What geological forces formed the basalt columns at Giant's Causeway in Ireland?", "Giant's Causeway geology"),
        ("Which endangered species of sea turtle nests along the Pacific coast of Costa Rica?", "Costa Rica sea turtles"),
        ("How do acoustic sound dampening baffles reduce reverberation in recording studios?", "Studio acoustic baffles"),
        ("What is the traditional Japanese tea ceremony etiquette and utensil preparation order?", "Japanese tea ceremony"),
        ("Which mountaineering route on K2 is known as the Bottleneck couloir traverse?", "K2 Bottleneck route"),
        ("How do solar flare coronal mass ejections disrupt satellite telecommunications?", "Solar flare telecommunications"),
        ("What are the nutritional differences between sourdough bread fermentation and baker's yeast?", "Sourdough vs yeast fermentation"),
        ("Which ancient Greek philosopher proposed the paradox of Achilles and the tortoise?", "Achilles and tortoise paradox"),
        ("How do honeybees communicate the direction of nectar sources using the waggle dance?", "Honeybee waggle dance"),
        ("What is the manufacturing process for high-efficiency monocrystalline photovoltaic cells?", "Monocrystalline solar cells"),
        ("Which deep-sea hydrothermal vent ecosystem relies entirely on chemosynthetic bacteria?", "Hydrothermal vent ecosystem"),
        ("How do migratory arctic terns navigate thousands of miles across planetary hemispheres?", "Arctic tern migration"),
        ("What architectural engineering techniques allow skyscrapers to withstand severe typhoons?", "Typhoon skyscraper engineering"),
        ("Which classical composer wrote the Symphony of a Thousand for massive orchestral choirs?", "Symphony of a Thousand composer"),
        ("How do volcanic calderas collapse following catastrophic super-eruptions?", "Volcanic caldera collapse"),
        ("What are the cultivation requirements for rare white truffles in Piedmont Italy?", "Piedmont white truffles"),
        ("Which Arctic expedition led by Sir John Franklin vanished in search of the Northwest Passage?", "Franklin Arctic expedition"),
        ("How do superconducting magnetic levitation trains eliminate track friction?", "Maglev superconducting trains"),
        ("What is the biological lifecycle and metamorphosis of monarch butterflies in Mexico?", "Monarch butterfly metamorphosis"),
        ("Which ancient Mayan astronomical observatory aligned with the seasonal transit of Venus?", "Mayan Venus observatory"),
    ]
    for i, (q, topic) in enumerate(cat8_queries, start=1):
        cases.append({
            "id": f"case-cat08-{i:02d}",
            "category": "out_of_scope",
            "query": q,
            "gold_atom_ids": [],
            "expected_abstention": True,
            "difficulty": "easy",
            "metadata": {"category_id": "CAT_08", "abstention_reason": f"Out of domain topic: {topic}"},
        })

    return cases


def build_and_save_indomain_benchmark():
    corpus_text = "\n\n".join(PARAGRAPHS)
    # Compute SHA-256 with normalized \n line endings
    normalized_corpus = corpus_text.replace("\r\n", "\n")
    corpus_sha256 = hashlib.sha256(normalized_corpus.encode("utf-8")).hexdigest()

    cases = generate_benchmark_cases()
    assert len(cases) == 320, f"Expected 320 cases, got {len(cases)}"

    # Check category distribution
    cats = {}
    for c in cases:
        cid = c["metadata"]["category_id"]
        cats[cid] = cats.get(cid, 0) + 1
    for cat_id, count in sorted(cats.items()):
        assert count == 40, f"Category {cat_id} has {count} cases, expected 40"

    manifest = {
        "name": "trace-lite-indomain-frozen-v1",
        "version": "1.0.0",
        "description": "Private in-domain 320-query frozen benchmark suite for Trace-Lite across 8 evaluation categories (40 cases each).",
        "corpus_sha256": corpus_sha256,
        "release_authority": True,
        "corpus_text": corpus_text,
        "cases": cases,
        "metadata": {
            "total_cases": 320,
            "categories": {
                "CAT_01": "Direct Lookup (40 cases)",
                "CAT_02": "Chronology & Order (40 cases)",
                "CAT_03": "Contradiction & Revision (40 cases)",
                "CAT_04": "Cross-Domain (40 cases)",
                "CAT_05": "Global Context (40 cases)",
                "CAT_06": "Multi-Hop Evidence (40 cases)",
                "CAT_07": "Historical & Versioned (40 cases)",
                "CAT_08": "Insufficient Evidence (40 cases)",
            },
            "paragraphs_count": len(PARAGRAPHS),
        },
    }

    out_dir = Path("benchmarks/fixtures")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "private_indomain_v1.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"[OK] Successfully built 320-case frozen benchmark fixture at: {out_path.resolve()}")
    print(f"     Corpus SHA-256: {corpus_sha256}")
    print(f"     Total Cases: {len(cases)} (40 per category across CAT_01..CAT_08)")
    return out_path


if __name__ == "__main__":
    build_and_save_indomain_benchmark()
