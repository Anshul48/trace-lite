"""Forest Router: Routes incoming atoms to trees in the forest."""

import uuid
import numpy as np

from trace_lite.spine import Atom
from trace_lite.cortex import Tree, ForestIndex, VectorStore
from trace_lite.adapters import EmbeddingAdapter, LLMAdapter


class ForestRouter:
    """
    Routes atoms to trees based on semantic vector similarity to tree root nodes.
    - If similarity > placement_threshold: route atom to that tree
    - Atom can belong to multiple trees if it spans multiple topics
    - If no tree matches: create a new tree for the atom(s)
    """

    def __init__(
        self,
        embedder: EmbeddingAdapter,
        llm: LLMAdapter,
        forest: ForestIndex,
        vector_store: VectorStore,
        placement_threshold: float = 0.55,
        max_trees_per_atom: int = 3,
    ):
        self.embedder = embedder
        self.llm = llm
        self.forest = forest
        self.vector_store = vector_store
        self.placement_threshold = placement_threshold
        self.max_trees_per_atom = max_trees_per_atom

    def route(self, atoms: list[Atom]) -> dict[str, list[Atom]]:
        """
        Routes a list of atoms.
        Returns: {tree_id: list_of_atoms} mapping.
        """
        if not atoms:
            return {}

        existing_trees = self.forest.list_trees()
        routing_map: dict[str, list[Atom]] = {}

        # If no trees exist yet, create initial tree
        if not existing_trees:
            new_tree_id = f"tree-{uuid.uuid4().hex[:12]}"
            name = self._generate_tree_name(atoms[:5])
            tree = Tree(tree_id=new_tree_id, name=name, description="Initial root tree")
            self.forest.store_tree(tree)
            routing_map[new_tree_id] = atoms
            return routing_map

        # Collect root embeddings for existing trees
        root_nodes = []
        root_embeddings = []
        for t in existing_trees:
            if t.root_node_id:
                root_node = self.forest.get_node(t.root_node_id)
                if root_node and root_node.summary_text:
                    emb = self.embedder.embed([root_node.summary_text])[0]
                    root_nodes.append((t, root_node))
                    root_embeddings.append(emb)

        if not root_embeddings:
            # Fallback if no root nodes have summaries yet
            t = existing_trees[0]
            routing_map[t.tree_id] = atoms
            return routing_map

        root_matrix = np.vstack(root_embeddings)
        atom_texts = [a.content for a in atoms]
        atom_embeddings = self.embedder.embed(atom_texts)

        unrouted_atoms: list[Atom] = []

        for idx, atom in enumerate(atoms):
            a_emb = atom_embeddings[idx]
            a_norm = np.linalg.norm(a_emb) + 1e-9

            sims = []
            for r_idx, (tree, r_node) in enumerate(root_nodes):
                r_emb = root_matrix[r_idx]
                r_norm = np.linalg.norm(r_emb) + 1e-9
                sim = float(np.dot(a_emb, r_emb) / (a_norm * r_norm))
                sims.append((tree.tree_id, sim))

            sims.sort(key=lambda x: x[1], reverse=True)
            matched_trees = [
                tid for tid, sim in sims[: self.max_trees_per_atom] if sim >= self.placement_threshold
            ]

            if matched_trees:
                for tid in matched_trees:
                    if tid not in routing_map:
                        routing_map[tid] = []
                    routing_map[tid].append(atom)
            else:
                unrouted_atoms.append(atom)

        # Create new tree for unrouted atoms
        if unrouted_atoms:
            new_tree_id = f"tree-{uuid.uuid4().hex[:12]}"
            name = self._generate_tree_name(unrouted_atoms[:5])
            tree = Tree(tree_id=new_tree_id, name=name, description="New topic tree")
            self.forest.store_tree(tree)
            routing_map[new_tree_id] = unrouted_atoms

        return routing_map

    def _generate_tree_name(self, sample_atoms: list[Atom]) -> str:
        sample_text = "\n".join(f"- {a.content[:100]}" for a in sample_atoms)
        prompt = (
            "Give a short 2-4 word descriptive title for a knowledge tree containing these notes:\n"
            f"{sample_text}\n\nTitle:"
        )
        try:
            name = self.llm.complete(prompt, max_tokens=20).strip()
            name = name.strip('"\'').strip()
            return name if name else "General Knowledge"
        except Exception:
            return "General Knowledge"
