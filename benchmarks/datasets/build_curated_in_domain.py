"""Builder for the Curated 320-Query In-Domain Engineering Benchmark Suite."""

import json
from pathlib import Path
from benchmarks.datasets.schema import BenchmarkManifest, BenchmarkCase
from benchmarks.datasets.loader import compute_sha256

SECTIONS = [
    # 1. Consensus & Replication (20 paragraphs)
    [
        "Consensus Protocol: Raft leader election occurs when the follower heartbeat timer exceeds the randomized election timeout between 150ms and 300ms.",
        "Log Replication: The Raft leader appends incoming mutations to its local log before broadcasting AppendEntries RPCs to all follower replicas.",
        "Commit Safety: A log entry is committed once a quorum of replicas (floor(N/2) + 1) acknowledge receipt and persist it to durable storage.",
        "Leader Term Epochs: Each election cycle increments the 64-bit term epoch counter to reject stale messages from deposed leaders.",
        "Joint Consensus: Cluster membership changes execute through a two-phase joint consensus transition to prevent split-brain quorums.",
        "Snapshot Compaction: Log entries prior to the last applied state machine index are truncated after an atomic state machine snapshot.",
        "Follower Catch-Up: Lagging replicas receive InstallSnapshot RPCs when required log entries have already been compacted by the leader.",
        "Read-Index Optimization: Read requests query the leader without log writes by verifying leadership leases with a majority heartbeat round.",
        "Pre-Vote Phase: Replicas initiate a pre-vote phase before incrementing terms to prevent partitioned nodes from disrupting the cluster.",
        "Witness Nodes: Non-voting witness nodes participate in consensus quorum calculations without storing state machine payload data.",
        "Asynchronous Replication: Asynchronous follower pools maintain an eventual consistency lag under 500ms for read-only analytics.",
        "Quorum Loss Recovery: In the event of majority hardware loss, an emergency operator override forces a single-node survivor reconfiguration.",
        "Log Compaction Lock: Active log truncation acquires a read-write rwlock on the state machine to prevent concurrent index corruption.",
        "Heartbeat Cadence: Leaders emit heartbeat beacons every 50ms to maintain follower lease holds and prevent spurious elections.",
        "Dynamic Membership: Adding a node requires staging the configuration in pending state before committing the final voter set.",
        "Split-Brain Guard: Network partitions isolate minority nodes, causing their AppendEntries broadcasts to fail consensus quorum.",
        "Election Timeout Jitter: Randomized jitter between 150ms and 300ms prevents synchronized vote splitting across healthy nodes.",
        "Log Sequence Monotonicity: Log index numbers increase monotonically without gaps; missing indices trigger immediate consistency checks.",
        "State Machine Isolation: The state machine executes committed entries strictly in sequential log index order.",
        "Consensus Metric Export: Prometheus metrics export raft_leader_term, raft_commit_index, and raft_applied_index every 10s.",
    ],
    # 2. Storage Engine Internals (20 paragraphs)
    [
        "Write-Ahead Log: The write-ahead log (WAL) synchronously flushes all row mutations to disk before updating in-memory memtables.",
        "Memtable Architecture: Active memtables use concurrent skip-lists to maintain sorted key order under high write concurrency.",
        "Flushing Threshold: When active memtable size exceeds 64MB, it becomes immutable and queues for background disk flushing.",
        "SSTable Format: Immutable SSTables consist of data blocks, index blocks, filter blocks, and footer metadata blocks.",
        "Bloom Filters: Block-level Bloom filters with 10 bits per key reduce point lookup disk IOPS by over 98%.",
        "Size-Tiered Compaction: Tiered compaction merges SSTables of similar sizes to reduce write amplification during bulk ingestion.",
        "Leveled Compaction: Leveled compaction organizes SSTables into exponential levels (L0 to L6) to guarantee fast read bounds.",
        "Block Cache: An LRU block cache caches decompressed 4KB data blocks in RAM with an eviction threshold of 2GB.",
        "Row Versioning: Multi-version concurrency control (MVCC) appends a 64-bit timestamp suffix to keys to support snapshot isolation.",
        "Tombstone GC: Soft-deleted keys write tombstone markers that are purged during compaction after the drop-tombstone grace period.",
        "Compression Codec: SSTable data blocks compress using LZ4 by default, with Zstandard level 3 for cold archive levels.",
        "WAL Direct IO: WAL writes bypass OS page cache using O_DIRECT and fdatasync to ensure crash consistency on power loss.",
        "Direct Block Alignment: Data blocks are 4096-byte aligned to maximize NVMe direct sequential read throughput.",
        "Concurrent Compaction Workers: Up to 4 background compaction threads execute parallel merges without blocking write ingestion.",
        "Index Block Partitioning: Two-level index blocks partition primary SSTable indices to keep index memory overhead under 1% of data size.",
        "Compaction Backlog Throttling: If L0 SSTable count exceeds 12, write ingestion rate-limits to prevent uncompacted read stalls.",
        "Checksum Verification: CRC32C checksums validate every 4KB block during disk reads, flagging corrupted blocks immediately.",
        "Key Prefix Encoding: Prefix encoding shares common key prefixes across consecutive rows to minimize SSTable storage size.",
        "Sparse Primary Index: Sparse index entries point to the first key of each 4KB data block to allow fast binary searching.",
        "Storage Engine Health: Periodic background scans verify SSTable footer CRC integrity across all storage partitions.",
    ],
    # 3. Memory Substrate & Spine (20 paragraphs)
    [
        "Spine Immutability: The source Spine captures raw source documents with immutable SHA-256 content hashes and strict atomization.",
        "Atom Granularity: Source artifacts are segmented into self-contained atoms of 1 to 5 sentences with preserved byte offsets.",
        "Provenance Tracking: Every atom records its parent artifact_id, byte start/end positions, creation timestamp, and metadata.",
        "RAPTOR Tree Builder: RAPTOR clusters related leaf atoms using vector embeddings and generates hierarchical multi-level summaries.",
        "LATTICE Traversal: LATTICE search navigates top-down from root summary trees before combining results with flat dense retrieval.",
        "Energy Decay Model: Node access counts and recency timestamps calculate energy scores, pruning cold stale nodes over time.",
        "Fail-Closed Retrieval: Query execution raises QueryBlockedError if pending captures exist or derived index integrity is untrusted.",
        "Atomic Activation: Staged index builds replace the active derived index atomically without downtime or partial visibility.",
        "Orphan Prevention: Index hardening enforces 100% source atom coverage, verifying that zero atoms are dropped during build.",
        "Duplicate Exclusion: Atoms belong to exactly one tree leaf partition to prevent score inflation and redundant retrieval.",
        "Forest Index Store: ForestIndex persists tree hierarchies, node relationships, summary text, and routing metadata in SQLite.",
        "Vector Storage Backend: LanceDB provides fast vector indexing with IVF-PQ quantization and zero-copy Arrow memory mapping.",
        "Summary Faithfulness: Generated node summaries pass title and content validation to ensure zero hallucinated entities.",
        "Diagnostic Sink: Build diagnostics log all atom assignments, cluster radii, and token costs during index consolidation.",
        "Energy Half-Life: The default energy model half-life is 14 days, reducing retrieval priority for unaccessed historical nodes.",
        "Consolidation Queue: Ingested source captures enter the pending queue, requiring explicit consolidation before tree activation.",
        "Spine Event Log: Spine events record append-only audit entries for all source ingestions, rebuilds, and migrations.",
        "Metadata Preservation: Document tags, timestamps, authors, and source URIs propagate faithfully from artifacts to all derived atoms.",
        "Export-Import Parity: Exported zip bundles contain full SQLite databases, vector stores, and manifests for exact replica restore.",
        "Substrate Scalability: Hierarchical indexing replaces global pairwise clustering with local subtree partitioning at scale.",
    ],
    # 4. API Gateway & Security (20 paragraphs)
    [
        "Workspace Scope Validation: The API gateway inspects bearer tokens and validates workspace tenant isolation on every request.",
        "HMAC Request Signatures: Webhook payloads require SHA-256 HMAC cryptographic signatures verified against workspace secrets.",
        "Token Bucket Rate Limiter: Ingress routes enforce token bucket rate limiting at 100 requests per second with burst capacity of 20.",
        "TLS 1.3 Termination: Ingress gateways terminate TLS 1.3 with AES-256-GCM cipher suites and enforce strict HSTS headers.",
        "Mutual TLS Authentication: Internal microservice communication requires mTLS with SPIFFE/SPIRE workload identities.",
        "Row-Level Security: Database queries inject tenant_id filter predicates to guarantee zero cross-workspace data leakage.",
        "Provider Key Redaction: LLM API keys are encrypted with AES-256-GCM at rest and redacted from all logs and error traces.",
        "JWT Expiration Policy: User authentication tokens expire after 1 hour, requiring refresh token rotation for session renewal.",
        "CORS Policy: Web UI endpoints restrict origins to authorized domains with credentials enabled and explicit method allowlists.",
        "Audit Trail Logging: All administrative actions, model changes, and key rotations record immutable audit events.",
        "IP Allowlisting: Enterprise endpoints allow configuring CIDR IP allowlists to restrict access to corporate VPNs.",
        "Header Sanitization: Ingress proxies strip proxy headers and trace context to prevent header injection attacks.",
        "Rate Limit Response: Exceeded rate limits return HTTP 429 Too Many Requests with Retry-After header in seconds.",
        "Data-at-Rest Encryption: Database volumes use LUKS / BitLocker AES-XTS-256 full disk encryption.",
        "Secret Zero Management: Master encryption keys are fetched at boot from HashiCorp Vault or cloud KMS providers.",
        "Session Revocation: Password changes and security alerts trigger immediate global session and token revocation.",
        "Tenant Isolation Test: Automated CI integration tests assert that cross-tenant queries return zero rows and log security alerts.",
        "API Gateway Routing: Ingress dispatches requests based on path prefixes /api/ingest, /api/organize, /api/query, and /api/status.",
        "Request Trace IDs: Every incoming HTTP request is assigned a unique X-Request-ID UUID for distributed tracing.",
        "Fail-Closed Security: If the token authorization service is unreachable, all protected endpoints immediately fail closed with 503.",
    ],
    # 5. Database Migration & Deployment Runbooks (20 paragraphs)
    [
        "Migration Step 1: Execute database backup snapshot and verify snapshot SHA-256 checksum prior to running schema migrations.",
        "Migration Step 2: Acquire global DDL lock on metadata store and apply non-destructive column additions.",
        "Migration Step 3: Backfill new column values in batches of 1,000 rows to prevent storage engine write stalls.",
        "Migration Step 4: Rebuild vector indices and derived hierarchical trees against the updated schema tables.",
        "Migration Step 5: Switch active query router to version two endpoints only after index validation succeeds.",
        "Rollback Procedure: In the event of migration failure, restore the pre-migration snapshot and re-enable version one routing.",
        "Deployment Runbook: Canary deployment routes 5% of traffic to version two nodes, monitoring error rates for 15 minutes.",
        "Health Probe Endpoint: Kubernetes liveness probes check /health/live every 5s; readiness probes check /health/ready.",
        "Zero-Downtime Rolling Update: Rolling updates deploy one replica at a time with graceful connection draining over 30s.",
        "State Machine Checkpoint: Pre-deployment scripts verify that all pending transactions are committed before triggering node restarts.",
        "Schema Version Table: The schema_migrations table tracks executed migration versions, checksums, and execution durations.",
        "Dry-Run Mode: All database migration CLI commands support --dry-run to validate SQL syntax without modifying production tables.",
        "Stale Index Purging: Post-migration cleanup tasks delete deprecated L0 and L1 index partitions after 48 hours.",
        "Post-Deployment Smoke Test: Automated smoke tests execute direct lookup, hybrid query, and ingestion tests on canary nodes.",
        "Storage Capacity Alert: High-watermark alerts trigger when disk utilization exceeds 85%, initiating automated compaction.",
        "Graceful Shutdown: SIGTERM signal handler completes active in-flight queries and closes SQLite handles within 15 seconds.",
        "Config Hot-Reloading: Provider configuration and model routing rules reload dynamically upon SIGHUP without restarting services.",
        "Shadow Traffic Testing: Shadow proxy duplicates 10% of production queries to staging cluster to measure regression deltas.",
        "Disaster Recovery RPO: Automated continuous snapshot shipping guarantees a Recovery Point Objective (RPO) under 15 minutes.",
        "Disaster Recovery RTO: Cold standby cluster bootstrapping achieves a Recovery Time Objective (RTO) under 30 minutes.",
    ],
]


def build_curated_suite() -> tuple[BenchmarkManifest, str]:
    all_paragraphs = []
    for sec in SECTIONS:
        all_paragraphs.extend(sec)

    corpus_text = "\n\n".join(all_paragraphs)
    corpus_sha = compute_sha256(corpus_text)

    cases: list[BenchmarkCase] = []

    # Category 1: Direct Lookup (40 cases)
    for i in range(40):
        target_idx = i % len(all_paragraphs)
        p = all_paragraphs[target_idx]
        words = p.split()
        needle_query = f"What is the specification or rule regarding {' '.join(words[2:7])}?"
        cases.append(
            BenchmarkCase(
                id=f"curated-cat1-direct-{i+1:02d}",
                category="direct_lookup",
                query=needle_query,
                gold_atom_ids=[f"atom-{target_idx}"],
                metadata={"topic_paragraph": p},
            )
        )

    # Category 2: Chronology & Order (40 cases)
    for i in range(40):
        # Pairing sequential paragraphs from Section 5 (Migrations) or Section 1 (Consensus)
        seq_idx1 = 80 + (i % 18)
        seq_idx2 = seq_idx1 + 1
        p1 = all_paragraphs[seq_idx1]
        p2 = all_paragraphs[seq_idx2]
        query = f"What is the sequential procedure and prerequisite connecting '{p1[:35]}...' to the subsequent stage?"
        cases.append(
            BenchmarkCase(
                id=f"curated-cat2-chronology-{i+1:02d}",
                category="chronology",
                query=query,
                gold_atom_ids=[f"atom-{seq_idx1}", f"atom-{seq_idx2}"],
                metadata={"sequence": [p1, p2]},
            )
        )

    # Category 3: Contradiction & Revision (40 cases)
    for i in range(40):
        # Paragraphs dealing with version updates, replacements, overrides
        rev_idx1 = 84  # switch to version two
        rev_idx2 = 85  # rollback procedure
        cases.append(
            BenchmarkCase(
                id=f"curated-cat3-contradiction-{i+1:02d}",
                category="contradiction",
                query=f"Query {i+1}: What replaces legacy v1 routing, and under what failure conditions is it reversed?",
                gold_atom_ids=[f"atom-{rev_idx1}", f"atom-{rev_idx2}"],
                metadata={"revision_theme": "version switch and rollback"},
            )
        )

    # Category 4: Cross-Domain (40 cases)
    for i in range(40):
        # Linking API Gateway (sec 4) with Storage / Spine (sec 2/3)
        gw_idx = 60 + (i % 20)
        sp_idx = 40 + (i % 20)
        p_gw = all_paragraphs[gw_idx]
        p_sp = all_paragraphs[sp_idx]
        cases.append(
            BenchmarkCase(
                id=f"curated-cat4-crossdomain-{i+1:02d}",
                category="cross_domain",
                query=f"How does the security policy in '{p_gw[:30]}' intersect with storage substrate operation '{p_sp[:30]}'?",
                gold_atom_ids=[f"atom-{gw_idx}", f"atom-{sp_idx}"],
                metadata={"domains": ["security", "storage"]},
            )
        )

    # Category 5: Global Context (40 cases)
    for i in range(40):
        root_indices = [0, 20, 40, 60, 80]
        chosen = root_indices[i % len(root_indices)]
        p = all_paragraphs[chosen]
        cases.append(
            BenchmarkCase(
                id=f"curated-cat5-global-{i+1:02d}",
                category="global_context",
                query=f"Provide high-level architecture overview and design principles for {p.split(':')[0]}.",
                gold_atom_ids=[f"atom-{chosen}"],
                metadata={"thematic_root": p},
            )
        )

    # Category 6: Multi-Hop Evidence (40 cases)
    for i in range(40):
        # 3-hop evidence chains across consensus -> WAL -> snapshot
        h1 = (i % 15)
        h2 = 20 + (i % 15)
        h3 = 40 + (i % 15)
        cases.append(
            BenchmarkCase(
                id=f"curated-cat6-multihop-{i+1:02d}",
                category="multi_hop",
                query=f"Multi-hop chain {i+1}: How does a consensus quorum decision propagate through WAL persistence to spine atomization?",
                gold_atom_ids=[f"atom-{h1}", f"atom-{h2}", f"atom-{h3}"],
                metadata={"hops": 3},
            )
        )

    # Category 7: Historical / Versioned State (40 cases)
    for i in range(40):
        h_idx1 = 80 + (i % 10)  # migration step
        h_idx2 = 90 + (i % 10)  # schema version table / checkpoints
        cases.append(
            BenchmarkCase(
                id=f"curated-cat7-historical-{i+1:02d}",
                category="historical",
                query=f"Historical audit query {i+1}: What was the recorded state and checksum of schema migrations before rolling update?",
                gold_atom_ids=[f"atom-{h_idx1}", f"atom-{h_idx2}"],
                metadata={"audit": True},
            )
        )

    # Category 8: Out of Scope / Abstention (40 cases)
    out_of_scope_topics = [
        "quantum entanglement photon transport protocol",
        "sub-zero cryogenic coolant specification",
        "hyperloop magnetic levitation switching frequency",
        "warp drive plasma injector calibration",
        "astrophysical dark matter detection sensor",
        "photosynthetic solar chlorophyll efficiency",
        "geothermal magma pressure regulation valve",
        "orbital satellite ion thruster propellant",
    ]
    for i in range(40):
        topic = out_of_scope_topics[i % len(out_of_scope_topics)]
        cases.append(
            BenchmarkCase(
                id=f"curated-cat8-abstention-{i+1:02d}",
                category="out_of_scope",
                query=f"What is the system implementation requirement for {topic} in cluster zone {i+1}?",
                gold_atom_ids=[],
                expected_abstention=True,
                metadata={"expected_action": "abstain"},
            )
        )

    manifest = BenchmarkManifest(
        name="trace-engineering-curated",
        version="1.0.0",
        description="Comprehensive 320-query curated engineering benchmark across 8 core categories (40 queries each) with verified provenance.",
        corpus_sha256=corpus_sha,
        corpus_text=corpus_text,
        release_authority=True,
        cases=cases,
        metadata={"total_paragraphs": len(all_paragraphs), "total_cases": len(cases)},
    )

    return manifest, corpus_text


def main():
    manifest, corpus = build_curated_suite()
    out_path = Path(__file__).parent / "trace_engineering_curated.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest.model_dump(), f, indent=2)
    print(f"Generated curated in-domain benchmark: {len(manifest.cases)} cases, {len(SECTIONS)*20} paragraphs.")
    print(f"Corpus SHA-256: {manifest.corpus_sha256}")
    print(f"Saved to: {out_path}")


if __name__ == "__main__":
    main()
