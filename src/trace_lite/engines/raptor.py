"""RAPTOR Engine: Recursive Abstractive Processing for Tree-Organized Retrieval."""

import uuid
from datetime import datetime, timezone
import numpy as np

from trace_lite.spine import Atom
from trace_lite.cortex import Tree, TreeNode, ClusteringPipeline, VectorStore, ForestIndex
from trace_lite.adapters import LLMAdapter, EmbeddingAdapter


class RaptorEngine:
    """
    Builds RAPTOR summary trees from leaf atoms.
    Recursively clusters and summarizes nodes until a root node is reached.
    """

    def __init__(
        self,
        llm: LLMAdapter,
        embedder: EmbeddingAdapter,
        vector_store: VectorStore,
        forest: ForestIndex,
        clustering: ClusteringPipeline | None = None,
        max_depth: int = 5,
    ):
        self.llm = llm
        self.embedder = embedder
        self.vector_store = vector_store
        self.forest = forest
        self.clustering = clustering or ClusteringPipeline()
        self.max_depth = max_depth

    def build_tree(self, tree_id: str, atoms: list[Atom], tree_name: str | None = None) -> Tree:
        if not atoms:
            tree = Tree(
                tree_id=tree_id,
                name=tree_name or f"Tree {tree_id[:8]}",
                description="Empty tree",
            )
            self.forest.store_tree(tree)
            return tree

        now = datetime.now(timezone.utc).isoformat()

        # 1. Create Level 0 (leaf) nodes
        level_nodes: list[TreeNode] = []
        node_embeddings: list[np.ndarray] = []
        leaf_count = len(atoms)

        atom_texts = [a.content for a in atoms]
        embeddings = self.embedder.embed(atom_texts)

        for i, atom in enumerate(atoms):
            node_id = f"node-{uuid.uuid4().hex[:12]}"
            node = TreeNode(
                node_id=node_id,
                tree_id=tree_id,
                level=0,
                node_type="leaf",
                atom_ids=[atom.atom_id],
                summary_text=atom.content,
                created_at=now,
                last_accessed=now,
            )
            level_nodes.append(node)
            node_embeddings.append(embeddings[i])

        all_tree_nodes: list[TreeNode] = list(level_nodes)
        vector_batch: list[tuple[str, np.ndarray, dict]] = [
            (
                n.node_id,
                emb,
                {
                    "tree_id": tree_id,
                    "node_type": "leaf",
                    "level": 0,
                    "summary": n.summary_text,
                    "atom_ids": n.atom_ids,
                },
            )
            for n, emb in zip(level_nodes, node_embeddings)
        ]

        # 2. Recursive Clustering & Summarization
        current_level = 0
        current_nodes = level_nodes
        current_embeddings = np.array(node_embeddings)

        while current_level < self.max_depth and len(current_nodes) > 1:
            clusters, orphans = self.clustering.cluster(current_embeddings)

            # If no multi-item cluster formed and we have items, bundle them into a root summary
            if not clusters and len(current_nodes) > 1:
                clusters = [list(range(len(current_nodes)))]
                orphans = []

            next_level_nodes: list[TreeNode] = []
            next_level_embeddings: list[np.ndarray] = []

            for cluster_indices in clusters:
                cluster_nodes = [current_nodes[idx] for idx in cluster_indices]
                summary_text = self._summarize_cluster(cluster_nodes)
                summary_emb = self.embedder.embed([summary_text])[0]

                parent_id = f"node-{uuid.uuid4().hex[:12]}"
                combined_atom_ids = []
                for cn in cluster_nodes:
                    combined_atom_ids.extend(cn.atom_ids)
                    cn.parent_id = parent_id

                is_root = len(clusters) == 1 and current_level + 1 == self.max_depth
                parent_node = TreeNode(
                    node_id=parent_id,
                    tree_id=tree_id,
                    level=current_level + 1,
                    node_type="root_summary" if is_root else "cluster_summary",
                    atom_ids=list(dict.fromkeys(combined_atom_ids)),
                    summary_text=summary_text,
                    children_ids=[cn.node_id for cn in cluster_nodes],
                    created_at=now,
                    last_accessed=now,
                )

                next_level_nodes.append(parent_node)
                next_level_embeddings.append(summary_emb)
                all_tree_nodes.append(parent_node)

                vector_batch.append(
                    (
                        parent_node.node_id,
                        summary_emb,
                        {
                            "tree_id": tree_id,
                            "node_type": parent_node.node_type,
                            "level": parent_node.level,
                            "summary": parent_node.summary_text,
                            "atom_ids": parent_node.atom_ids,
                        },
                    )
                )

            # Handle orphans by promoting them to next level
            for orphan_idx in orphans:
                orphan_node = current_nodes[orphan_idx]
                next_level_nodes.append(orphan_node)
                next_level_embeddings.append(current_embeddings[orphan_idx])

            if len(next_level_nodes) == len(current_nodes):
                # No consolidation progress made, stop recursion
                break

            current_level += 1
            current_nodes = next_level_nodes
            current_embeddings = np.array(next_level_embeddings)

        # 3. Create Root Node if multiple top nodes remain
        root_node_id = None
        if len(current_nodes) == 1:
            root_node = current_nodes[0]
            root_node.node_type = "root_summary"
            root_node_id = root_node.node_id
        elif len(current_nodes) > 1:
            root_summary = self._summarize_cluster(current_nodes)
            root_emb = self.embedder.embed([root_summary])[0]
            root_node_id = f"node-{uuid.uuid4().hex[:12]}"
            all_atoms = []
            for cn in current_nodes:
                all_atoms.extend(cn.atom_ids)
                cn.parent_id = root_node_id

            root_node = TreeNode(
                node_id=root_node_id,
                tree_id=tree_id,
                level=current_level + 1,
                node_type="root_summary",
                atom_ids=list(dict.fromkeys(all_atoms)),
                summary_text=root_summary,
                children_ids=[cn.node_id for cn in current_nodes],
                created_at=now,
                last_accessed=now,
            )
            all_tree_nodes.append(root_node)
            vector_batch.append(
                (
                    root_node.node_id,
                    root_emb,
                    {
                        "tree_id": tree_id,
                        "node_type": "root_summary",
                        "level": root_node.level,
                        "summary": root_node.summary_text,
                        "atom_ids": root_node.atom_ids,
                    },
                )
            )

        # 4. Store tree and nodes
        description = "Summary tree"
        if root_node_id:
            root_node_obj = next((n for n in all_tree_nodes if n.node_id == root_node_id), None)
            if root_node_obj and root_node_obj.summary_text:
                description = root_node_obj.summary_text

        if len(description) > 200:
            description = description[:197] + "..."


        tree = Tree(
            tree_id=tree_id,
            name=tree_name or f"Tree {tree_id[:8]}",
            description=description,
            root_node_id=root_node_id,
            node_count=len(all_tree_nodes),
            leaf_count=leaf_count,
            depth=current_level + 1,
            created_at=now,
            last_consolidated=now,
        )

        self.forest.store_tree(tree)
        self.forest.store_nodes_batch(all_tree_nodes)
        self.vector_store.upsert_batch(vector_batch)

        return tree

    def _summarize_cluster(self, nodes: list[TreeNode]) -> str:
        texts = [n.summary_text for n in nodes if n.summary_text]
        if not texts:
            return "No content available."

        combined_text = "\n".join(f"- {t}" for t in texts[:10])
        prompt = (
            "Summarize the following related text passages into a single concise paragraph. "
            "Capture key concepts, facts, entities, and relationships. "
            "Be informative and factual.\n\n"
            f"Passages:\n{combined_text}\n\nSummary:"
        )
        return self.llm.complete(prompt, max_tokens=300)
