# Copyright 2026 trace-lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""TraceLite: Main user-facing SDK class."""

import json
import uuid
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from trace_lite.config import TraceLiteConfig
from trace_lite.spine import SpineStore, SourceArtifact, Atom, SpineEvent, Atomizer
from trace_lite.cortex import ForestIndex, LanceDBStore, VectorStore, EnergyModel, Tree
from trace_lite.adapters import (
    EmbeddingAdapter,
    SentenceTransformerEmbedder,
    LLMAdapter,
    LiteLLMAdapter,
)
from trace_lite.engines import RaptorEngine, ForestRouter, LatticeEngine, QueryResult


@dataclass
class IngestionResult:
    artifact_id: str
    atom_count: int
    tree_ids: list[str]


@dataclass
class ConsolidationResult:
    trees_updated: int
    summaries_generated: int


@dataclass
class DatabaseStatus:
    total_atoms: int
    total_trees: int
    estimated_tokens: int
    active_nodes: int
    total_nodes: int


class TraceLite:
    """
    Self-organizing headless database with hierarchical retrieval.

    Usage:
        db = TraceLite("./my_knowledge_base")
        db.ingest("SQLite is a lightweight transactional database.", document_name="Notes")
        db.consolidate()
        res = db.query("What database do we use?")
    """

    def __init__(
        self,
        data_dir: str | Path,
        config: TraceLiteConfig | None = None,
        embedder: EmbeddingAdapter | None = None,
        llm: LLMAdapter | None = None,
        vector_store: VectorStore | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or TraceLiteConfig.default()

        # 1. Core Stores
        spine_path = self.data_dir / self.config.spine_db_name
        cortex_path = self.data_dir / self.config.cortex_db_name
        vector_dir = self.data_dir / self.config.vector_db_name

        self.spine = SpineStore(spine_path)
        self.forest = ForestIndex(cortex_path)
        self.vector_store = vector_store or LanceDBStore(vector_dir)

        # 2. Adapters
        self.embedder = embedder or SentenceTransformerEmbedder(self.config.embedding_model)
        self.llm = llm or LiteLLMAdapter(
            model=self.config.llm_model, api_base=self.config.llm_api_base
        )

        # 3. Pipelines & Engines
        self.atomizer = Atomizer(
            min_length=self.config.min_atom_length,
            max_length=self.config.max_atom_length,
        )
        self.energy = EnergyModel(
            decay_exponent=self.config.energy_decay_exponent,
            retrieval_threshold=self.config.energy_retrieval_threshold,
        )

        self.router = ForestRouter(
            embedder=self.embedder,
            llm=self.llm,
            forest=self.forest,
            vector_store=self.vector_store,
            placement_threshold=self.config.router_placement_threshold,
            max_trees_per_atom=self.config.router_max_trees_per_atom,
        )

        self.raptor = RaptorEngine(
            llm=self.llm,
            embedder=self.embedder,
            vector_store=self.vector_store,
            forest=self.forest,
            max_depth=self.config.raptor_max_depth,
        )

        self.lattice = LatticeEngine(
            llm=self.llm,
            embedder=self.embedder,
            vector_store=self.vector_store,
            forest=self.forest,
            spine=self.spine,
            energy=self.energy,
            branch_factor=self.config.lattice_branch_factor,
            max_depth=self.config.lattice_max_depth,
        )

        self._pending_atoms: dict[str, list[Atom]] = {}

    def ingest(
        self,
        text: str,
        document_name: str | None = None,
        source_uri: str | None = None,
        metadata: dict | None = None,
        chronological_order: Literal["asc", "desc"] | None = None,
    ) -> IngestionResult:
        if not text.strip():
            raise ValueError("Cannot ingest empty text.")

        metadata = metadata or {}
        if chronological_order:
            metadata["chronological_order"] = chronological_order

        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        artifact = SourceArtifact.create(
            artifact_id=artifact_id,
            content=text,
            document_name=document_name,
            source_uri=source_uri,
            metadata=metadata,
        )
        self.spine.store_artifact(artifact)

        atoms = self.atomizer.atomize(text, artifact_id, metadata=metadata)
        if not atoms:
            return IngestionResult(artifact_id=artifact_id, atom_count=0, tree_ids=[])

        self.spine.store_atoms_batch(atoms)
        self.spine.append_event(
            SpineEvent.create(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                event_type="artifact.ingested",
                payload={"artifact_id": artifact_id, "atom_count": len(atoms)},
            )
        )

        # Route atoms to trees
        routing_map = self.router.route(atoms)
        tree_ids = list(routing_map.keys())

        # Queue pending atoms for tree consolidation
        for tid, tree_atoms in routing_map.items():
            if tid not in self._pending_atoms:
                self._pending_atoms[tid] = []
            self._pending_atoms[tid].extend(tree_atoms)

        return IngestionResult(
            artifact_id=artifact_id,
            atom_count=len(atoms),
            tree_ids=tree_ids,
        )

    def ingest_file(
        self,
        file_path: str | Path,
        document_name: str | None = None,
        chronological_order: Literal["asc", "desc"] | None = None,
    ) -> IngestionResult:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        text = path.read_text(encoding="utf-8", errors="ignore")
        name = document_name or path.name
        source_uri = path.as_uri()

        return self.ingest(
            text=text,
            document_name=name,
            source_uri=source_uri,
            chronological_order=chronological_order,
        )

    def ingest_batch(self, items: list[dict]) -> list[IngestionResult]:
        results = []
        for item in items:
            text = item.get("text") or item.get("content") or ""
            res = self.ingest(
                text=text,
                document_name=item.get("document_name"),
                source_uri=item.get("source_uri"),
                metadata=item.get("metadata"),
                chronological_order=item.get("chronological_order"),
            )
            results.append(res)
        return results

    def consolidate(self, tree_id: str | None = None) -> ConsolidationResult:
        trees_to_process = []
        if tree_id:
            tree = self.forest.get_tree(tree_id)
            if tree:
                trees_to_process.append(tree)
        else:
            trees_to_process = self.forest.list_trees()

        if not trees_to_process:
            # Consolidate pending unbuilt trees
            for tid, atoms in list(self._pending_atoms.items()):
                if atoms:
                    self.raptor.build_tree(tid, atoms)
                    self._pending_atoms.pop(tid, None)
            return ConsolidationResult(trees_updated=len(self._pending_atoms), summaries_generated=1)

        trees_updated = 0
        summaries_generated = 0

        for tree in trees_to_process:
            tid = tree.tree_id
            # Collect all atoms assigned to this tree from pending or existing nodes
            existing_nodes = self.forest.get_tree_nodes(tid, level=0)
            existing_atom_ids = set()
            for n in existing_nodes:
                existing_atom_ids.update(n.atom_ids)

            pending = self._pending_atoms.pop(tid, [])
            pending_atom_ids = {a.atom_id for a in pending}

            all_atom_ids = existing_atom_ids | pending_atom_ids
            if not all_atom_ids:
                continue

            all_atoms = [self.spine.get_atom(aid) for aid in all_atom_ids if self.spine.get_atom(aid)]
            if not all_atoms:
                continue

            updated_tree = self.raptor.build_tree(tid, all_atoms, tree_name=tree.name)
            trees_updated += 1
            summaries_generated += max(0, updated_tree.node_count - updated_tree.leaf_count)

        return ConsolidationResult(
            trees_updated=trees_updated, summaries_generated=summaries_generated
        )

    def query(
        self,
        query_text: str,
        top_k: int = 10,
        mode: Literal["hybrid", "tree", "flat"] = "hybrid",
    ) -> QueryResult:
        # If pending atoms exist, consolidate them first for up-to-date search
        if self._pending_atoms:
            self.consolidate()

        return self.lattice.query(query_text=query_text, top_k=top_k, mode=mode)

    def status(self) -> DatabaseStatus:
        atoms_count = self.spine.count_atoms()
        trees = self.forest.list_trees()
        total_nodes = 0
        active_nodes = 0

        for t in trees:
            nodes = self.forest.get_tree_nodes(t.tree_id)
            total_nodes += len(nodes)
            for n in nodes:
                if self.energy.is_active(n.last_accessed, n.access_count):
                    active_nodes += 1

        # Rough token count estimate: 1 word ≈ 1.33 tokens
        estimated_tokens = int(atoms_count * 30 * 1.33)

        return DatabaseStatus(
            total_atoms=atoms_count,
            total_trees=len(trees),
            estimated_tokens=estimated_tokens,
            active_nodes=active_nodes,
            total_nodes=total_nodes,
        )

    def trees(self) -> list[Tree]:
        return self.forest.list_trees()

    def export(self, output_zip_path: str | Path) -> None:
        zip_path = Path(output_zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_name in [self.config.spine_db_name, self.config.cortex_db_name]:
                p = self.data_dir / file_name
                if p.exists():
                    zipf.write(p, arcname=file_name)
