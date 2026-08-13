"""LATTICE Engine: LLM-guided hierarchical search and hybrid retrieval."""

import json
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime, timezone
import numpy as np

from trace_lite.spine import SpineStore, Atom, SourceArtifact
from trace_lite.cortex import VectorStore, ForestIndex, EnergyModel, TreeNode
from trace_lite.adapters import LLMAdapter, EmbeddingAdapter


@dataclass
class EvidenceItem:
    atom: Atom
    score: float
    tree_id: str | None
    tree_name: str | None
    source_artifact: SourceArtifact | None
    traversal_path: list[str]  # List of node summaries along path


@dataclass
class QueryResult:
    query_text: str
    items: list[EvidenceItem]
    traversal_paths: dict[str, list[str]]
    mode: str
    timestamp: str
    warnings: list[str] = field(default_factory=list)


class LatticeEngine:
    """
    LATTICE Traversal Engine:
    Performs top-down LLM-guided tree navigation combined with flat vector search.
    """

    def __init__(
        self,
        llm: LLMAdapter,
        embedder: EmbeddingAdapter,
        vector_store: VectorStore,
        forest: ForestIndex,
        spine: SpineStore,
        energy: EnergyModel | None = None,
        branch_factor: int = 3,
        max_depth: int = 5,
    ):
        self.llm = llm
        self.embedder = embedder
        self.vector_store = vector_store
        self.forest = forest
        self.spine = spine
        self.energy = energy or EnergyModel()
        self.branch_factor = branch_factor
        self.max_depth = max_depth

    def query(
        self,
        query_text: str,
        top_k: int = 10,
        mode: str = "hybrid",  # "hybrid" | "tree" | "flat"
    ) -> QueryResult:
        query_emb = self.embedder.embed([query_text])[0]
        now = datetime.now(timezone.utc).isoformat()

        tree_atom_scores: dict[str, float] = {}
        traversal_paths: dict[str, list[str]] = {}

        # 1. Tree Traversal (LATTICE)
        if mode in ("hybrid", "tree"):
            tree_results, paths = self._lattice_traverse(query_text, query_emb, top_k)
            for atom_id, score in tree_results.items():
                tree_atom_scores[atom_id] = score
            traversal_paths.update(paths)

        # 2. Flat Vector Search
        flat_atom_scores: dict[str, float] = {}
        if mode in ("hybrid", "flat"):
            # Internal summary vectors remain available to RAPTOR/LATTICE
            # traversal, but they are not direct evidence.  A flat hit must
            # hydrate source atoms from a leaf vector only.
            vector_results = self.vector_store.search(
                query_emb,
                top_k=top_k * 2,
                filter_expr="node_type = 'leaf'",
            )
            for res in vector_results:
                meta = res.metadata
                atom_ids = meta.get("atom_ids", [])
                for aid in atom_ids:
                    flat_atom_scores[aid] = max(flat_atom_scores.get(aid, 0.0), res.score)

        # 3. Hybrid Fusion & Ranking
        final_atom_scores: dict[str, float] = {}
        all_atom_ids = set(tree_atom_scores.keys()) | set(flat_atom_scores.keys())

        for aid in all_atom_ids:
            t_score = tree_atom_scores.get(aid, 0.0)
            f_score = flat_atom_scores.get(aid, 0.0)
            if mode == "hybrid":
                # Combined score favoring items found in both paths
                final_atom_scores[aid] = (0.6 * f_score) + (0.4 * t_score)
            elif mode == "tree":
                final_atom_scores[aid] = t_score
            else:
                final_atom_scores[aid] = f_score

        sorted_atom_ids = sorted(
            final_atom_scores.keys(), key=lambda aid: final_atom_scores[aid], reverse=True
        )[:top_k]

        # 4. Hydrate Evidence Items with Spine Provenance
        evidence_items: list[EvidenceItem] = []
        for aid in sorted_atom_ids:
            atom = self.spine.get_atom(aid)
            if not atom:
                continue

            artifact = self.spine.get_artifact(atom.source_artifact_id)
            score = final_atom_scores[aid]
            path = traversal_paths.get(aid, [])

            # Record retrieval energy on the derived leaf node(s), never by
            # passing a source atom ID into the tree-node table.
            for leaf in self.forest.get_leaf_nodes_for_atom(aid):
                self.forest.record_access(leaf.node_id)

            evidence_items.append(
                EvidenceItem(
                    atom=atom,
                    score=score,
                    tree_id=self._tree_for_atom(aid),
                    tree_name=self._tree_name_for_atom(aid),
                    source_artifact=artifact,
                    traversal_path=path,
                )
            )

        return QueryResult(
            query_text=query_text,
            items=evidence_items,
            traversal_paths=traversal_paths,
            mode=mode,
            timestamp=now,
        )

    def _tree_for_atom(self, atom_id: str) -> str | None:
        leaves = self.forest.get_leaf_nodes_for_atom(atom_id)
        return leaves[0].tree_id if leaves else None

    def _tree_name_for_atom(self, atom_id: str) -> str | None:
        tree_id = self._tree_for_atom(atom_id)
        if not tree_id:
            return None
        tree = self.forest.get_tree(tree_id)
        return tree.name if tree else None

    def _lattice_traverse(
        self, query_text: str, query_emb: np.ndarray, top_k: int
    ) -> tuple[dict[str, float], dict[str, list[str]]]:
        trees = self.forest.list_trees()
        if not trees:
            return {}, {}

        # 1. Filter active roots and score against query
        active_roots: list[tuple[Tree, TreeNode]] = []
        for t in trees:
            if t.root_node_id:
                root_node = self.forest.get_node(t.root_node_id)
                if root_node and self.energy.is_active(root_node.last_accessed, root_node.access_count):
                    active_roots.append((t, root_node))

        if not active_roots:
            return {}, {}

        # 2. Select top candidate trees
        scored_roots: list[tuple[Tree, TreeNode, float]] = []
        q_norm = np.linalg.norm(query_emb) + 1e-9

        for t, root in active_roots:
            if root.summary_text:
                r_emb = self.embedder.embed([root.summary_text])[0]
                r_norm = np.linalg.norm(r_emb) + 1e-9
                sim = float(np.dot(query_emb, r_emb) / (q_norm * r_norm))
                scored_roots.append((t, root, sim))

        scored_roots.sort(key=lambda x: x[2], reverse=True)
        selected_trees = scored_roots[: self.branch_factor]

        leaf_atom_scores: dict[str, float] = {}
        paths: dict[str, list[str]] = {}

        # 3. Top-down traversal for each selected tree
        for t, root, root_score in selected_trees:
            curr_nodes = [(root, [root.summary_text or "Root"])]

            for depth in range(self.max_depth):
                next_nodes: list[tuple[TreeNode, list[str]]] = []
                for node, path_trace in curr_nodes:
                    self.forest.record_access(node.node_id)

                    if node.node_type == "leaf" or not node.children_ids:
                        for aid in node.atom_ids:
                            leaf_atom_scores[aid] = max(leaf_atom_scores.get(aid, 0.0), root_score)
                            paths[aid] = list(path_trace)
                        continue

                    children = self.forest.get_children(node.node_id)
                    active_children = [
                        c for c in children if self.energy.is_active(c.last_accessed, c.access_count)
                    ]
                    if not active_children:
                        active_children = children

                    ranked_children = self._score_children(query_text, query_emb, active_children)
                    top_children = [c for c, s in ranked_children[: self.branch_factor]]
                    for c in top_children:
                        child_path = list(path_trace)
                        if c.summary_text:
                            child_path.append(c.summary_text[:120])
                        next_nodes.append((c, child_path))

                if not next_nodes:
                    break
                curr_nodes = next_nodes

        return leaf_atom_scores, paths

    def _score_children(
        self, query_text: str, query_emb: np.ndarray, children: list[TreeNode]
    ) -> list[tuple[TreeNode, float]]:
        if not children:
            return []

        q_norm = np.linalg.norm(query_emb) + 1e-9
        scored: list[tuple[TreeNode, float]] = []

        texts = [c.summary_text or "" for c in children]
        embs = self.embedder.embed(texts)

        for i, c in enumerate(children):
            c_emb = embs[i]
            c_norm = np.linalg.norm(c_emb) + 1e-9
            sim = float(np.dot(query_emb, c_emb) / (q_norm * c_norm))
            scored.append((c, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
