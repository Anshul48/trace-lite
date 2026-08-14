"""Deterministic synthetic corpus and query generator for 1k, 10k, 100k, and 1M scale ladders."""

import hashlib
import random
from typing import Literal
from benchmarks.datasets.schema import BenchmarkManifest, BenchmarkCase, QueryCategory
from benchmarks.datasets.loader import compute_sha256

ScaleTier = Literal["1k", "10k", "100k", "1m"]

TIER_ATOM_COUNTS = {
    "1k": 1000,
    "10k": 10000,
    "100k": 100000,
    "1m": 1000000,
}

TOPIC_TEMPLATES = [
    ("Storage Engine", "The write-ahead log ensures durability before memtable flush for shard {shard_id}."),
    ("LSM Compaction", "Tiered size-compaction reduces read amplification during high-velocity insertions in cluster {shard_id}."),
    ("Raft Consensus", "The raft leader commits log entries once a quorum of {shard_id} replicas acknowledge receipt."),
    ("Vector Indexing", "Hierarchical Navigable Small World graphs partition vector space with M={shard_id} connections per layer."),
    ("Query Optimizer", "Cost-based query optimizer selects index scan over full tablescan for partition {shard_id}."),
    ("Cache Invalidation", "Distributed cache invalidation broadcast propagates TTL lease expirations across zone {shard_id}."),
    ("Spine Storage", "Immutable source spine captures raw payload sha256 checksum for artifact id {shard_id}."),
    ("Tree Hierarchy", "LATTICE traversal navigates from root summary down to leaf node partition {shard_id}."),
]


def generate_scale_benchmark(tier: ScaleTier = "1k", seed: int = 42) -> tuple[BenchmarkManifest, str]:
    """
    Deterministically generate a scale benchmark corpus and balanced queries.
    Returns (manifest, full_corpus_text).
    """
    target_count = TIER_ATOM_COUNTS[tier]
    rng = random.Random(seed)

    paragraphs: list[str] = []
    cases: list[BenchmarkCase] = []

    # 1. Generate base filler paragraphs
    for i in range(target_count):
        topic_name, template = TOPIC_TEMPLATES[i % len(TOPIC_TEMPLATES)]
        shard_val = (i * 7 + 13) % 997
        text = template.format(shard_id=shard_val)
        paragraphs.append(text)

    # 2. Plant deterministic target needles and build queries across the 8 categories

    # CAT 1: Direct Lookup
    needle_idx_1 = min(42, target_count - 1)
    paragraphs[needle_idx_1] = "Unique Needle Alpha: The quantum flux capacitor regulates primary power at exactly 1.21 gigawatts."
    cases.append(
        BenchmarkCase(
            id=f"{tier}-cat1-direct-lookup",
            category="direct_lookup",
            query="What regulates primary power at exactly 1.21 gigawatts?",
            gold_atom_ids=[f"atom-{needle_idx_1}"],
            metadata={"planted_index": needle_idx_1},
        )
    )

    # CAT 2: Chronology / Order
    idx_seq1 = min(100, target_count - 2)
    idx_seq2 = idx_seq1 + 1
    paragraphs[idx_seq1] = "Stage One: Backup snapshot is taken prior to cluster schema migration."
    paragraphs[idx_seq2] = "Stage Two: Cluster schema migration is executed only after the snapshot verification succeeds."
    cases.append(
        BenchmarkCase(
            id=f"{tier}-cat2-chronology",
            category="chronology",
            query="What happens before cluster schema migration is executed?",
            gold_atom_ids=[f"atom-{idx_seq1}", f"atom-{idx_seq2}"],
            metadata={"planted_indices": [idx_seq1, idx_seq2]},
        )
    )

    # CAT 3: Contradiction / Revision
    idx_rev1 = min(150, target_count - 1)
    paragraphs[idx_rev1] = "Architecture Update: Protocol Theta replaces the legacy Protocol Beta for all inter-region transfers."
    cases.append(
        BenchmarkCase(
            id=f"{tier}-cat3-contradiction",
            category="contradiction",
            query="What replaced the legacy Protocol Beta?",
            gold_atom_ids=[f"atom-{idx_rev1}"],
            metadata={"planted_index": idx_rev1},
        )
    )

    # CAT 4: Cross-Domain
    idx_cross1 = min(200, target_count - 1)
    idx_cross2 = min(400, target_count - 1)
    paragraphs[idx_cross1] = "Security Policy: Authentication tokens require cryptographic HMAC verification from the key vault."
    paragraphs[idx_cross2] = "API Gateway: Incoming requests validate authentication tokens before dispatching to backend worker pools."
    cases.append(
        BenchmarkCase(
            id=f"{tier}-cat4-cross-domain",
            category="cross_domain",
            query="How does the API gateway verify tokens using the key vault?",
            gold_atom_ids=[f"atom-{idx_cross1}", f"atom-{idx_cross2}"],
            metadata={"planted_indices": [idx_cross1, idx_cross2]},
        )
    )

    # CAT 5: Global Context
    idx_glob = min(300, target_count - 1)
    paragraphs[idx_glob] = "System Overview: The substrate architecture coordinates immutable storage, hierarchical indexing, and zero-loss recovery."
    cases.append(
        BenchmarkCase(
            id=f"{tier}-cat5-global-context",
            category="global_context",
            query="What are the high-level coordinating components of the substrate architecture?",
            gold_atom_ids=[f"atom-{idx_glob}"],
            metadata={"planted_index": idx_glob},
        )
    )

    # CAT 6: Multi-Hop
    idx_h1 = min(250, target_count - 1)
    idx_h2 = min(350, target_count - 1)
    idx_h3 = min(450, target_count - 1)
    paragraphs[idx_h1] = "Service Discovery: Node registry maintains active heartbeat records for all ingress gateways."
    paragraphs[idx_h2] = "Ingress Gateways: Active ingress gateways route TLS traffic to the consensus coordinator."
    paragraphs[idx_h3] = "Consensus Coordinator: The coordinator assigns Raft log sequence numbers to incoming mutations."
    cases.append(
        BenchmarkCase(
            id=f"{tier}-cat6-multihop",
            category="multi_hop",
            query="How does a node registry heartbeat record link to Raft log mutation assignment?",
            gold_atom_ids=[f"atom-{idx_h1}", f"atom-{idx_h2}", f"atom-{idx_h3}"],
            metadata={"planted_indices": [idx_h1, idx_h2, idx_h3]},
        )
    )

    # CAT 7: Historical / Versioned
    idx_hist = min(500, target_count - 1)
    paragraphs[idx_hist] = "Historical Archive v0.1: Prototype implementation used linear in-memory scan before vector indexing was added."
    cases.append(
        BenchmarkCase(
            id=f"{tier}-cat7-historical",
            category="historical",
            query="What did the prototype implementation use prior to vector indexing?",
            gold_atom_ids=[f"atom-{idx_hist}"],
            metadata={"planted_index": idx_hist},
        )
    )

    # CAT 8: Out of Scope / Abstention
    cases.append(
        BenchmarkCase(
            id=f"{tier}-cat8-out-of-scope",
            category="out_of_scope",
            query="What is the warp drive engine coolant specification for Starship Enterprise?",
            gold_atom_ids=[],
            expected_abstention=True,
            metadata={"abstention": True},
        )
    )

    full_corpus = "\n\n".join(paragraphs)
    corpus_hash = compute_sha256(full_corpus)

    manifest = BenchmarkManifest(
        name=f"scale-synthetic-{tier}",
        version="1.0.0",
        description=f"Deterministic {tier.upper()} scale benchmark with {target_count} paragraphs and 8 query categories.",
        corpus_sha256=corpus_hash,
        corpus_text=full_corpus,
        release_authority=False,
        cases=cases,
        metadata={"tier": tier, "atom_count": target_count, "seed": seed},
    )

    return manifest, full_corpus
