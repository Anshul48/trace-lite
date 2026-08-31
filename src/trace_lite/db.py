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

import hashlib
import json
import logging
import shutil
import sqlite3
import threading
import uuid
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from trace_lite.config import TraceLiteConfig
from trace_lite.spine import SpineStore, SourceArtifact, Atom, SpineEvent, Atomizer
from trace_lite.cortex import (
    ForestIndex,
    LanceDBStore,
    VectorStore,
    VectorStoreError,
    EnergyModel,
    Tree,
    TreeNode,
)
from trace_lite.adapters import (
    EmbeddingAdapter,
    SentenceTransformerEmbedder,
    LLMAdapter,
    LiteLLMAdapter,
    redact_diagnostic,
)
from trace_lite.adapters.llm import (
    LLMPreflightError,
    OllamaAvailabilityError,
    RejectedCredentialError,
    UnsupportedModelError,
)
from trace_lite.diagnostics import BuildDiagnostic, diagnostic_dict
from trace_lite.engines import (
    RaptorEngine,
    ForestRouter,
    LatticeEngine,
    QueryResult,
    SummaryGenerationError,
    validate_summary,
)
from trace_lite.engines.router import TreeNamingError
from trace_lite.cortex.manifest import IndexBuildManifest
from trace_lite.cortex import ClusteringPipeline
from trace_lite.providers import (
    ProviderConfigurationError,
    auto_load_models_enabled,
    require_active_provider,
    resolve_active_provider,
)


logger = logging.getLogger("trace_lite.db")


@dataclass
class IngestionResult:
    artifact_id: str
    atom_count: int
    tree_ids: list[str]
    pending_atoms: int = 0
    pending_trees: int = 0
    needs_organization: bool = False


@dataclass
class ConsolidationResult:
    """Summary of a staged organization/rebuild operation.

    Legacy fallback/provenance counts are reported by status and validation;
    newly activated non-leaf nodes must have LLM or retry provenance.
    """
    trees_updated: int
    summaries_generated: int
    pending_atoms: int = 0
    pending_trees: int = 0
    orphaned_atoms: int = 0
    needs_organization: bool = False
    needs_recovery: bool = False


@dataclass
class DatabaseStatus:
    """Operational status, including pending work and active-index trust.

    ``fallback_summaries`` is a legacy diagnostic. A non-zero value makes the
    active derived index untrusted and requires a fresh rebuild.
    """
    total_atoms: int
    total_trees: int
    estimated_tokens: int
    active_nodes: int
    total_nodes: int
    pending_atoms: int = 0
    pending_trees: int = 0
    orphaned_atoms: int = 0
    needs_organization: bool = False
    needs_recovery: bool = False
    indexed_atoms: int = 0
    valid_summaries: int = 0
    fallback_summaries: int = 0
    vector_count: int = 0
    active_build_id: str | None = None
    validation_state: str = "unvalidated"
    validation_errors: list[str] | None = None
    validation_warnings: list[str] | None = None
    index_trusted: bool = False
    structure_present: bool = False
    quality_verified: bool = False
    credential_state: str = "not_configured"
    active_provider: str | None = None


@dataclass
class IndexBuildResult:
    build_id: str
    state: str
    trees_updated: int
    summaries_generated: int
    validation: dict
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def __getitem__(self, key: str):
        return self.to_dict()[key]


class IndexValidationError(RuntimeError):
    """Raised when a candidate derived index cannot be activated."""

    def __init__(self, validation: dict):
        self.validation = validation
        errors = validation.get("errors") or ["index validation failed"]
        super().__init__(
            "; ".join(errors)
            + "; activation was prevented and the previous active index was preserved"
        )


class QueryBlockedError(RuntimeError):
    """A query would otherwise use pending or untrusted derived state."""


class TraceLite:
    """
    Source-first headless database with fail-closed hierarchical retrieval.

    Usage:
        db = TraceLite("./my_knowledge_base")
        db.ingest("SQLite is a lightweight transactional database.", document_name="Notes")
        db.organize()  # requires a saved and verified provider
        res = db.query("What database do we use?")  # blocks if state is pending/untrusted
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

        # Warm-up is deliberately owned by the database runtime rather than by
        # the LLM adapter.  It only touches the local embedding adapter and is
        # never part of provider preflight or an implicit build.
        self._model_warmup_lock = threading.RLock()
        self._model_warmup_status = "not_loaded"
        self._model_warmup_error: str | None = None
        self._model_warmup_event: threading.Event | None = None
        self._model_warmup_thread: threading.Thread | None = None

        # 1. Core Stores
        spine_path = self.data_dir / self.config.spine_db_name
        cortex_path = self.data_dir / self.config.cortex_db_name
        vector_dir = self.data_dir / self.config.vector_db_name

        self.spine = SpineStore(spine_path)
        self.forest = ForestIndex(cortex_path)
        self.vector_store = vector_store or LanceDBStore(vector_dir)

        # 2. Adapters
        self.embedder = embedder or SentenceTransformerEmbedder(self.config.embedding_model)
        self._uses_runtime_provider = llm is None
        self._runtime_provider = resolve_active_provider() if llm is None else None
        self.llm = llm or LiteLLMAdapter(
            model=self.config.llm_model,
            api_base=self.config.llm_api_base,
            api_version=self._runtime_provider.api_version if self._runtime_provider else None,
            api_key=self._runtime_provider.api_key if self._runtime_provider else None,
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
            title_retry_count=self.config.router_title_retry_count,
        )

        self.raptor = RaptorEngine(
            llm=self.llm,
            embedder=self.embedder,
            vector_store=self.vector_store,
            forest=self.forest,
            clustering=ClusteringPipeline(
                n_components=self.config.umap_n_components,
                min_cluster_size=self.config.hdbscan_min_cluster_size,
                max_children=self.config.raptor_max_children_per_summary,
                projection_max_samples=self.config.projection_max_points,
            ),
            max_depth=self.config.raptor_max_depth,
            max_children=self.config.raptor_max_children_per_summary,
            summary_min_length=self.config.raptor_summary_min_length,
            summary_retry_count=self.config.raptor_summary_retry_count,
            summary_max_tokens=self.config.llm_max_tokens_summary,
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

    def model_status(self) -> dict[str, object]:
        """Return local embedding warm-up state without touching the LLM."""
        with self._model_warmup_lock:
            if bool(getattr(self.embedder, "is_loaded", False)):
                self._model_warmup_status = "ready"
                self._model_warmup_error = None
            status = self._model_warmup_status
            error = self._model_warmup_error
            thread = self._model_warmup_thread
        loaded = status == "ready" or bool(getattr(self.embedder, "is_loaded", False))
        return {
            "auto_load_models": auto_load_models_enabled(),
            "enabled": auto_load_models_enabled(),
            "model": self.config.embedding_model,
            "embedding_model": self.config.embedding_model,
            "status": "ready" if loaded else status,
            "loaded": loaded,
            "loading": status == "loading" or bool(thread and thread.is_alive()),
            "error": error,
            "embedding": {
                "model": self.config.embedding_model,
                "status": "ready" if loaded else status,
                "loaded": loaded,
                "error": error,
            },
            # Startup warm-up must never initialize, preflight, or call this
            # adapter.  Keep that invariant visible to API/UI consumers.
            "llm": {"status": "not_requested", "contacted": False},
        }

    def warm_up_models(self) -> dict[str, object]:
        """Synchronously load only the local embedding model.

        Concurrent callers share one in-flight load.  A failed load is exposed
        through status and is re-attempted by a later explicit call, while the
        original exception is still raised to one-shot CLI callers.
        """
        with self._model_warmup_lock:
            if self._model_warmup_status == "ready" or bool(getattr(self.embedder, "is_loaded", False)):
                self._model_warmup_status = "ready"
                self._model_warmup_error = None
                return self.model_status()
            if self._model_warmup_status == "loading" and self._model_warmup_event is not None:
                event = self._model_warmup_event
                owner = False
            else:
                event = threading.Event()
                self._model_warmup_event = event
                self._model_warmup_status = "loading"
                self._model_warmup_error = None
                owner = True

        if not owner:
            event.wait()
            with self._model_warmup_lock:
                if self._model_warmup_status == "failed":
                    raise RuntimeError(self._model_warmup_error or "Embedding model warm-up failed.")
            return self.model_status()

        try:
            warm_up = getattr(self.embedder, "warm_up", None)
            if callable(warm_up):
                warm_up()
            # Older/custom adapters may not implement warm_up.  They remain
            # valid injected adapters; their first real embed call is lazy.
            with self._model_warmup_lock:
                self._model_warmup_status = "ready"
                self._model_warmup_error = None
            return self.model_status()
        except Exception as exc:
            message = redact_diagnostic(f"{type(exc).__name__}: {str(exc)[:300]}")
            with self._model_warmup_lock:
                self._model_warmup_status = "failed"
                self._model_warmup_error = message
            raise
        finally:
            with self._model_warmup_lock:
                if self._model_warmup_event is event:
                    event.set()

    def start_model_warmup(self) -> threading.Thread | None:
        """Start a daemon warm-up when the global preference is enabled."""
        if not auto_load_models_enabled():
            return None
        with self._model_warmup_lock:
            if self._model_warmup_status == "ready" or bool(getattr(self.embedder, "is_loaded", False)):
                self._model_warmup_status = "ready"
                return self._model_warmup_thread
            if self._model_warmup_thread is not None and self._model_warmup_thread.is_alive():
                return self._model_warmup_thread

            def run() -> None:
                try:
                    self.warm_up_models()
                    logger.info("Embedding model warm-up ready: %s", self.config.embedding_model)
                except Exception as exc:
                    # The UI exposes the full state through /api/models/status;
                    # keep this log safe and concise for startup failures.
                    logger.warning(
                        "Embedding model warm-up failed (%s): %s",
                        type(exc).__name__,
                        redact_diagnostic(str(exc)[:300]),
                    )

            self._model_warmup_thread = threading.Thread(
                target=run,
                name="TraceLiteEmbeddingWarmup",
                daemon=True,
            )
            self._model_warmup_thread.start()
            return self._model_warmup_thread

    def ingest(
        self,
        text: str,
        document_name: str | None = None,
        source_uri: str | None = None,
        metadata: dict | None = None,
        chronological_order: Literal["asc", "desc"] | None = None,
    ) -> IngestionResult:
        """Persist a source artifact/atoms and queue them without an LLM call.

        Routing, tree naming, summaries, and vectors are deliberately deferred
        to ``organize``/``reindex_all`` after provider preflight succeeds.
        """
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
            current_status = self.status()
            return IngestionResult(
                artifact_id=artifact_id,
                atom_count=0,
                tree_ids=[],
                pending_atoms=current_status.pending_atoms,
                pending_trees=current_status.pending_trees,
                needs_organization=current_status.needs_organization,
            )

        self.spine.store_atoms_batch(atoms)
        from trace_lite.engines.graph import generate_sequential_edges, generate_co_occurrence_edges
        seq_edges = generate_sequential_edges(atoms)
        co_edges = generate_co_occurrence_edges(atoms)
        if seq_edges or co_edges:
            self.forest.store_edges(seq_edges + co_edges)

        self.spine.append_event(
            SpineEvent.create(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                event_type="artifact.ingested",
                payload={"artifact_id": artifact_id, "atom_count": len(atoms)},
            )
        )

        # Captures are intentionally queued at source level.  Semantic routing
        # and LLM tree naming happen only in the fully staged derived build.
        self.forest.queue_source_atoms([atom.atom_id for atom in atoms])
        tree_ids: list[str] = []

        current_status = self.status()
        return IngestionResult(
            artifact_id=artifact_id,
            atom_count=len(atoms),
            tree_ids=tree_ids,
            pending_atoms=current_status.pending_atoms,
            pending_trees=current_status.pending_trees,
            needs_organization=current_status.needs_organization,
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

    def consolidate(self, tree_id: str | None = None, diagnostic_sink=None) -> ConsolidationResult:
        # ``consolidate`` is a compatibility name for a validated derived
        # rebuild.  A targeted request is retained for the API contract; the
        # complete candidate is rebuilt so vector/node parity remains global.
        result = self.reindex_all(diagnostic_sink=diagnostic_sink)
        current_status = self.status()
        return ConsolidationResult(
            trees_updated=result.trees_updated,
            summaries_generated=result.summaries_generated,
            pending_atoms=current_status.pending_atoms,
            pending_trees=current_status.pending_trees,
            orphaned_atoms=current_status.orphaned_atoms,
            needs_organization=current_status.needs_organization,
            needs_recovery=current_status.needs_recovery,
        )

    def organize(self, diagnostic_sink=None) -> ConsolidationResult:
        """Build only the durable work that is waiting for organization.

        The explicit ``consolidate`` command remains the full rebuild entry
        point. This method is the safe, idempotent operation used by the web
        workspace and CLI; queries never trigger organization implicitly.
        """
        pending_source_atom_ids = self.forest.get_pending_source_atom_ids()
        pending_tree_ids = self.forest.get_pending_tree_ids()
        pending_assignments = {
            t: self.forest.get_pending_atom_ids(t) for t in pending_tree_ids
        }
        has_pending = bool(pending_source_atom_ids or any(pending_assignments.values()))

        if not has_pending:
            current_status = self.status()
            return ConsolidationResult(
                trees_updated=0,
                summaries_generated=0,
                pending_atoms=current_status.pending_atoms,
                pending_trees=current_status.pending_trees,
                orphaned_atoms=current_status.orphaned_atoms,
                needs_organization=current_status.needs_organization,
                needs_recovery=current_status.needs_recovery,
            )

        status = self.status()
        existing_trees = self.forest.list_trees()
        if not status.index_trusted or not status.structure_present or not existing_trees:
            return self.consolidate(diagnostic_sink=diagnostic_sink)

        self._emit_diagnostic(
            diagnostic_sink,
            stage="preflight",
            message="Checking provider routing and credentials before incremental organization.",
            status="attempt",
        )
        try:
            self._preflight_provider()
        except Exception as exc:
            self._emit_diagnostic(
                diagnostic_sink,
                stage="preflight",
                failure_code=self._failure_code(exc),
                message=self._diagnostic_message(exc),
                status="final_failure",
            )
            raise
        self._emit_diagnostic(
            diagnostic_sink,
            stage="preflight",
            message="Provider preflight passed.",
            status="success",
        )

        pending_atoms: list[Atom] = []
        if pending_source_atom_ids:
            for aid in pending_source_atom_ids:
                atom = self.spine.get_atom(aid)
                if atom:
                    pending_atoms.append(atom)

        routed_assignments: list[tuple[str, str, list[Atom]]] = []
        if pending_atoms:
            routed_assignments = self.router.route_plan(
                pending_atoms,
                existing_trees=existing_trees,
                diagnostic_sink=diagnostic_sink,
            )

        tree_atoms_to_add: dict[str, list[Atom]] = {}
        tree_names: dict[str, str] = {t.tree_id: t.name for t in existing_trees}

        for tree_id, name, atoms in routed_assignments:
            tree_atoms_to_add.setdefault(tree_id, []).extend(atoms)
            tree_names[tree_id] = name

        for tree_id, atom_ids in pending_assignments.items():
            for aid in atom_ids:
                atom = self.spine.get_atom(aid)
                if atom:
                    tree_atoms_to_add.setdefault(tree_id, []).append(atom)

        trees_updated = 0
        summaries_generated = 0
        processed_source_atoms: set[str] = set()

        for tree_id, new_atoms in tree_atoms_to_add.items():
            existing_tree = self.forest.get_tree(tree_id)
            if existing_tree:
                existing_atom_ids = self.forest.get_indexed_atom_ids(tree_id)
                existing_atoms = [
                    self.spine.get_atom(aid) for aid in existing_atom_ids
                ]
                existing_atoms_filtered = [a for a in existing_atoms if a is not None]
                seen_ids: set[str] = set()
                combined_atoms: list[Atom] = []
                for a in existing_atoms_filtered + new_atoms:
                    if a.atom_id not in seen_ids:
                        seen_ids.add(a.atom_id)
                        combined_atoms.append(a)

                built_tree = self.raptor.build_tree(
                    tree_id,
                    combined_atoms,
                    tree_name=existing_tree.name,
                    persist=True,
                    diagnostic_sink=diagnostic_sink,
                )
                trees_updated += 1
                summaries_generated += max(0, built_tree.node_count - built_tree.leaf_count)
            else:
                seen_ids = set()
                deduped_new_atoms: list[Atom] = []
                for a in new_atoms:
                    if a.atom_id not in seen_ids:
                        seen_ids.add(a.atom_id)
                        deduped_new_atoms.append(a)

                built_tree = self.raptor.build_tree(
                    tree_id,
                    deduped_new_atoms,
                    tree_name=tree_names.get(tree_id, f"Tree {tree_id[:8]}"),
                    persist=True,
                    diagnostic_sink=diagnostic_sink,
                )
                trees_updated += 1
                summaries_generated += max(0, built_tree.node_count - built_tree.leaf_count)

            for a in new_atoms:
                processed_source_atoms.add(a.atom_id)

        for tree_id, atom_ids in pending_assignments.items():
            self.forest.clear_pending_assignments(tree_id, atom_ids)
        self.forest.clear_pending_source_atoms(processed_source_atoms)
        if pending_source_atom_ids - processed_source_atoms:
            self.forest.clear_pending_source_atoms(pending_source_atom_ids)

        active_build = self.forest.get_active_index_build()
        if active_build:
            all_trees = self.forest.list_trees()
            all_nodes_count = sum(t.node_count for t in all_trees)
            all_leaves_count = sum(t.leaf_count for t in all_trees)
            all_source_atoms = len(self.spine.list_atoms())
            try:
                self.forest.update_index_build(
                    active_build["build_id"],
                    state="active",
                    source_atom_count=all_source_atoms,
                    tree_count=len(all_trees),
                    node_count=all_nodes_count,
                    leaf_count=all_leaves_count,
                    vector_count=all_nodes_count,
                )
            except Exception:
                pass

        current_status = self.status()
        return ConsolidationResult(
            trees_updated=trees_updated,
            summaries_generated=summaries_generated,
            pending_atoms=current_status.pending_atoms,
            pending_trees=current_status.pending_trees,
            orphaned_atoms=current_status.orphaned_atoms,
            needs_organization=current_status.needs_organization,
            needs_recovery=current_status.needs_recovery,
        )

    def reindex_all(self, diagnostic_sink=None) -> IndexBuildResult:
        """Build and validate a complete derived index from Spine.

        No active Cortex row or active vector collection is changed until the
        candidate has passed every structural and vector parity check.
        """
        atoms = self.spine.list_atoms()
        # Generic provider preflight happens before routing, naming, manifests,
        # vectors, or queue rows are touched.  This is also required for a
        # leaf-only rebuild: organization must never imply credentials work.
        self._emit_diagnostic(
            diagnostic_sink,
            stage="preflight",
            message="Checking provider routing and credentials before staging.",
            status="attempt",
        )
        try:
            self._preflight_provider()
        except Exception as exc:
            self._emit_diagnostic(
                diagnostic_sink,
                stage="preflight",
                failure_code=self._failure_code(exc),
                message=self._diagnostic_message(exc),
                status="final_failure",
            )
            raise
        self._emit_diagnostic(
            diagnostic_sink,
            stage="preflight",
            message="Provider preflight passed.",
            status="success",
        )
        self.router.naming_diagnostics = []
        self._emit_diagnostic(
            diagnostic_sink,
            stage="routing",
            message="Planning staged tree assignments without mutating the active index.",
            status="attempt",
        )
        try:
            assignments = self._reindex_assignments(
                atoms, diagnostic_sink=diagnostic_sink
            )
        except Exception as exc:
            self._emit_diagnostic(
                diagnostic_sink,
                stage="tree_naming",
                failure_code=self._failure_code(exc),
                message=self._diagnostic_message(exc),
                status="final_failure",
            )
            raise
        self._emit_diagnostic(
            diagnostic_sink,
            stage="routing",
            message=f"Planned {len(assignments)} staged tree assignment(s).",
            status="success",
        )
        pending_source_atom_ids = self.forest.get_pending_source_atom_ids()
        pending_assignments = {
            tree_id: self.forest.get_pending_atom_ids(tree_id)
            for tree_id in self.forest.get_pending_tree_ids()
        }

        build_id = f"build-{uuid.uuid4().hex[:16]}"
        expected_dimension = int(
            getattr(self.embedder, "dimension", self.config.embedding_dimension)
        )
        manifest = IndexBuildManifest(
            build_id=build_id,
            source_atom_count=len(atoms),
            embedding_model=getattr(self.embedder, "model_name", self.config.embedding_model),
            embedding_dimension=expected_dimension,
            config_fingerprint=self._config_fingerprint(),
            vector_collection=getattr(self.vector_store, "collection_name", None),
        )
        self.forest.create_index_build(manifest.to_dict())
        old_collection = getattr(self.vector_store, "collection_name", None)
        old_snapshot = None
        staged_store = None
        vector_activated = False
        vector_mutation_started = False
        try:
            old_snapshot = self._snapshot_vectors()
            built_trees: list[Tree] = []
            built_nodes: list[TreeNode] = []
            vector_rows: list[tuple[str, object, dict]] = []
            self.raptor.summary_diagnostics = []

            # A buffer keeps custom vector backends out of the active index
            # until the candidate is known to be structurally sound.
            from trace_lite.cortex.vector_store import MockVectorStore

            buffer = MockVectorStore(dim=expected_dimension)
            for tree_id, tree_name, tree_atoms in assignments:
                self.raptor.build_tree(
                    tree_id,
                    tree_atoms,
                    tree_name=tree_name,
                    persist=False,
                    diagnostic_sink=diagnostic_sink,
                )
                candidate = self.raptor.last_build
                if candidate is None:
                    raise RuntimeError("RAPTOR did not return a build candidate.")
                built_trees.append(candidate.tree)
                built_nodes.extend(candidate.nodes)
                buffer.upsert_batch(candidate.vectors)
                vector_rows.extend(candidate.vectors)

            self._emit_diagnostic(
                diagnostic_sink,
                stage="validation",
                tree_id=None,
                message="Validating staged topology, summaries, and vector parity.",
                status="attempt",
            )
            validation = self._validate_candidate(
                built_trees,
                built_nodes,
                list(buffer.iter_vectors()),
                source_atom_ids={atom.atom_id for atom in atoms},
                expected_dimension=expected_dimension,
            )
            if not validation["healthy"]:
                self._emit_diagnostic(
                    diagnostic_sink,
                    stage="validation",
                    failure_code="validation_failed",
                    message="Staged index failed validation; activation was prevented.",
                    status="final_failure",
                )
                raise IndexValidationError(validation)

            self._emit_diagnostic(
                diagnostic_sink,
                stage="validation",
                message="Staged index passed validation.",
                status="success",
            )

            summary_counts = dict(validation.get("summary_quality") or {})
            quality_failure_codes = dict(
                summary_counts.get("validation_failure_codes") or {}
            )
            title_retry_attempts = 0
            for diagnostic in self.router.naming_diagnostics:
                code = diagnostic.get("failure_code")
                if code:
                    quality_failure_codes[str(code)] = quality_failure_codes.get(str(code), 0) + 1
                    title_retry_attempts += 1
            summary_counts["validation_failure_codes"] = quality_failure_codes
            summary_counts["title_retry_attempts"] = title_retry_attempts
            self.forest.update_index_build(
                build_id,
                state="validated",
                source_atom_count=len(atoms),
                tree_count=len(built_trees),
                node_count=len(built_nodes),
                leaf_count=sum(tree.leaf_count for tree in built_trees),
                vector_count=len(vector_rows),
                embedding_dimension=expected_dimension,
                vector_collection=self._candidate_collection_name(build_id),
                valid_summary_count=summary_counts["valid"],
                fallback_summary_count=summary_counts["fallback"],
                retry_summary_count=summary_counts["retry"],
                passthrough_summary_count=summary_counts["passthrough"],
                summary_quality={
                    **summary_counts,
                    "minimum_length": self.config.raptor_summary_min_length,
                },
                warnings=validation.get("warnings", []),
            )

            self._emit_diagnostic(
                diagnostic_sink,
                stage="activation",
                message="Activating the validated vector collection and Cortex build.",
                status="attempt",
            )
            # LanceDB gets a genuinely separate versioned collection.  Generic
            # injected stores use their optional build API or a rollback-safe
            # replace operation at the activation boundary.
            if isinstance(self.vector_store, LanceDBStore):
                staged_store = self.vector_store.begin_build(
                    build_id, dimension=expected_dimension
                )
                staged_store.upsert_batch(vector_rows)
                staged_validation = self._validate_vectors(
                    list(staged_store.iter_vectors()),
                    {node.node_id for node in built_nodes},
                    expected_dimension,
                )
                if staged_validation["errors"]:
                    raise IndexValidationError(staged_validation)

                if hasattr(self.vector_store, "activate_collection"):
                    if isinstance(staged_store, LanceDBStore):
                        self.vector_store.activate_collection(staged_store.collection_name)
                    else:
                        self.vector_store.activate_collection(staged_store)
                    vector_activated = True
            elif vector_rows or old_snapshot:
                vector_mutation_started = True
                try:
                    self._replace_vectors(vector_rows)
                    vector_activated = True
                except Exception:
                    self._restore_vectors(old_snapshot, old_collection)
                    raise

            self.forest.activate_index_build(build_id, built_trees, built_nodes)
            from trace_lite.engines.graph import generate_all_edges
            all_graph_edges = generate_all_edges(atoms, built_nodes)
            if all_graph_edges:
                self.forest.store_edges(all_graph_edges)
            # Consume only work that was part of this successful candidate.
            # New captures arriving during a build remain durable and pending.
            for tree_id, atom_ids in pending_assignments.items():
                self.forest.clear_pending_assignments(tree_id, atom_ids)
            self.forest.clear_pending_source_atoms(pending_source_atom_ids)
            # The candidate was validated against the exact rows that are now
            # published.  Avoid a second backend read after the commit becoming
            # a new failure window; callers can run validate_index independently.
            active_validation = dict(validation)
            active_validation.update(
                {
                    "active_build_id": build_id,
                    "active_build_state": "active",
                    "active_vector_collection": self._candidate_collection_name(build_id),
                    "validation_state": "healthy",
                }
            )
            self._emit_diagnostic(
                diagnostic_sink,
                stage="activation",
                message=f"Activated validated build {build_id}.",
                status="success",
            )
            return IndexBuildResult(
                build_id=build_id,
                state="active",
                trees_updated=len(built_trees),
                summaries_generated=sum(tree.node_count - tree.leaf_count for tree in built_trees),
                validation=active_validation,
                warnings=active_validation.get("warnings", []),
            )
        except Exception as exc:
            failure_stage = (
                "summary_generation"
                if isinstance(exc, SummaryGenerationError)
                else "tree_naming"
                if isinstance(exc, TreeNamingError)
                else "validation"
                if isinstance(exc, IndexValidationError)
                else "build"
            )
            self._emit_diagnostic(
                diagnostic_sink,
                stage=failure_stage,
                failure_code=self._failure_code(exc),
                message=self._diagnostic_message(exc),
                status="final_failure",
            )
            self._emit_diagnostic(
                diagnostic_sink,
                stage="failure",
                failure_code=self._failure_code(exc),
                message=self._diagnostic_message(exc),
                status="final_failure",
            )
            try:
                failure_code = self._failure_code(exc)
                retry_attempts = sum(
                    max(0, int(item.get("attempts", 1)) - 1)
                    for item in self.raptor.summary_diagnostics
                    if isinstance(item, dict)
                )
                self.forest.update_index_build(
                    build_id,
                    state="failed",
                    # Persist the authored safe diagnostic, never an adapter
                    # exception that might contain a provider body or prompt.
                    error=self._diagnostic_message(exc),
                    summary_quality={
                        "valid": 0,
                        "retry_attempts": retry_attempts,
                        "validation_failure_codes": {failure_code: 1},
                        "last_failure_code": failure_code,
                    },
                )
            except Exception:
                pass
            if vector_activated or vector_mutation_started:
                self._restore_vectors(old_snapshot, old_collection)
            raise

    def _preflight_provider(self) -> None:
        """Verify the configured runtime provider before every derived build."""
        if self._uses_runtime_provider:
            runtime = require_active_provider()
            self._runtime_provider = runtime
            if isinstance(self.llm, LiteLLMAdapter):
                if runtime.provider_id is None and not self.llm.is_ollama_model:
                    raise ProviderConfigurationError(
                        "No active API-key provider is configured for this model. "
                        "Use Save & Verify before organizing."
                    )
                self.llm.kwargs["api_key"] = runtime.api_key
                if runtime.api_base is not None:
                    self.llm.api_base = runtime.api_base
                if runtime.model:
                    self.llm.model = runtime.model
        preflight = getattr(self.llm, "preflight", None)
        if callable(preflight):
            preflight()
            return
        # Explicitly injected test/custom adapters have no provider credentials
        # to resolve.  Their normal summary calls remain the preflight contract.

    @staticmethod
    def _failure_code(error: BaseException) -> str:
        if isinstance(error, ProviderConfigurationError):
            return "provider_configuration_error"
        if isinstance(error, SummaryGenerationError):
            return error.failure_code
        if isinstance(error, TreeNamingError):
            return error.failure_code
        if isinstance(error, IndexValidationError):
            return "validation_failed"
        if isinstance(error, OllamaAvailabilityError):
            return type(error).__name__.replace("Error", "").casefold()
        if isinstance(error, RejectedCredentialError):
            return "credential_rejected"
        if isinstance(error, UnsupportedModelError):
            return "unsupported_model"
        if isinstance(error, LLMPreflightError):
            return type(error).__name__.replace("Error", "").casefold()
        return "build_failed"

    @staticmethod
    def _diagnostic_message(error: BaseException) -> str:
        if isinstance(error, ProviderConfigurationError):
            return str(error)[:300]
        if isinstance(error, (SummaryGenerationError, TreeNamingError, IndexValidationError)):
            return str(error)[:300]
        if isinstance(error, LLMPreflightError):
            if isinstance(
                error,
                (OllamaAvailabilityError, RejectedCredentialError, UnsupportedModelError),
            ):
                return str(error)[:300]
            return (
                f"{type(error).__name__} stopped provider verification; "
                "check the configured model, endpoint, and credentials."
            )
        return (
            f"{type(error).__name__} interrupted the staged build; "
            "activation was prevented and the previous index was preserved."
        )

    @staticmethod
    def _emit_diagnostic(
        sink,
        *,
        stage: str,
        tree_id: str | None = None,
        node_id: str | None = None,
        attempt: int = 0,
        max_attempts: int = 1,
        failure_code: str | None = None,
        message: str = "",
        status: str = "attempt",
    ) -> None:
        if sink is None:
            return
        # The internal stages use stable machine-readable names.  The extra
        # label is useful to human-facing sinks without making consumers parse
        # prose to identify a stage.
        event = BuildDiagnostic(
            stage=stage,
            tree_id=tree_id,
            node_id=node_id,
            attempt=attempt,
            max_attempts=max_attempts,
            failure_code=failure_code,
            message=message[:300],
            status=status,
            retrying=status == "retrying",
            final_failure=status == "final_failure",
        )
        payload = diagnostic_dict(event)
        payload["stage_label"] = stage.replace("_", " ")
        try:
            sink(payload)
        except Exception:
            return

    def _reindex_assignments(
        self, atoms: list[Atom], *, diagnostic_sink=None
    ) -> list[tuple[str, str, list[Atom]]]:
        """Plan complete staged membership without changing active Cortex."""
        if not atoms:
            return []
        atom_by_id = {atom.atom_id: atom for atom in atoms}
        existing = self.forest.list_trees()
        memberships: dict[str, set[str]] = {tree.tree_id: set() for tree in existing}
        for tree in existing:
            memberships[tree.tree_id].update(self.forest.get_indexed_atom_ids(tree.tree_id))
            memberships[tree.tree_id].update(self.forest.get_pending_atom_ids(tree.tree_id))

        assigned = set().union(*memberships.values()) if memberships else set()
        unassigned = set(atom_by_id) - assigned

        planned: dict[str, tuple[str, list[Atom]]] = {}
        for tree in existing:
            ids = memberships.get(tree.tree_id, set())
            tree_atoms = [atom_by_id[atom_id] for atom_id in sorted(ids) if atom_id in atom_by_id]
            if tree_atoms:
                planned[tree.tree_id] = (tree.name, tree_atoms)
        if unassigned:
            staged_atoms = [atom_by_id[atom_id] for atom_id in sorted(unassigned)]
            for tree_id, tree_name, routed_atoms in self.router.route_plan(
                staged_atoms,
                existing_trees=existing,
                diagnostic_sink=diagnostic_sink,
            ):
                if tree_id in planned:
                    existing_name, existing_atoms = planned[tree_id]
                    known_ids = {atom.atom_id for atom in existing_atoms}
                    planned[tree_id] = (
                        existing_name,
                        existing_atoms + [atom for atom in routed_atoms if atom.atom_id not in known_ids],
                    )
                else:
                    planned[tree_id] = (tree_name, list(routed_atoms))
        return [
            (tree_id, tree_name, tree_atoms)
            for tree_id, (tree_name, tree_atoms) in planned.items()
        ]

    def _config_fingerprint(self) -> str:
        payload = json.dumps(self.config.index_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _candidate_collection_name(self, build_id: str) -> str:
        if isinstance(self.vector_store, LanceDBStore):
            return f"cortex_nodes_{build_id}"
        return f"mock-{build_id}"

    def _snapshot_vectors(self) -> list[tuple[str, object, dict]] | None:
        iterator = getattr(self.vector_store, "iter_vectors", None)
        if not callable(iterator):
            return None
        return list(iterator())

    def _replace_vectors(self, rows: list[tuple[str, object, dict]]) -> None:
        replace_all = getattr(self.vector_store, "replace_all", None)
        if callable(replace_all):
            replace_all(rows)
            return
        self.vector_store.upsert_batch(rows)

    def _restore_vectors(
        self,
        snapshot: list[tuple[str, object, dict]] | None,
        collection_name: str | None,
    ) -> None:
        if snapshot is None:
            return
        if isinstance(self.vector_store, LanceDBStore) and collection_name:
            self.vector_store.restore_collection(collection_name)
            return
        restore = getattr(self.vector_store, "restore_snapshot", None)
        if callable(restore):
            restore(snapshot, collection_name)
            return
        replace_all = getattr(self.vector_store, "replace_all", None)
        if callable(replace_all):
            replace_all(snapshot)

    @staticmethod
    def _summary_counts(
        nodes: list[TreeNode],
        approved_summary_ids: set[str] | None = None,
        diagnostics: list[dict] | None = None,
        validation_failure_codes: dict[str, int] | None = None,
    ) -> dict:
        failure_code_counts = dict(validation_failure_codes or {})
        for item in diagnostics or []:
            if not isinstance(item, dict):
                continue
            for code in item.get("failure_codes", []) or []:
                if code:
                    failure_code_counts[str(code)] = failure_code_counts.get(str(code), 0) + 1
        counts = {
            "valid": 0,
            "fallback": 0,
            "retry": 0,
            "passthrough": 0,
            "retry_attempts": 0,
            "title_retry_attempts": 0,
            "validation_failure_codes": failure_code_counts,
        }
        diagnostic_by_node = {
            str(item.get("node_id")): item
            for item in (diagnostics or [])
            if isinstance(item, dict) and item.get("node_id")
        }
        for node in nodes:
            if node.level == 0:
                continue
            approved = (
                node.node_id in approved_summary_ids
                if approved_summary_ids is not None
                else bool((node.summary_text or "").strip())
                and node.summary_provenance in {"llm", "retry"}
            )
            if approved:
                counts["valid"] += 1
            if node.summary_provenance == "fallback":
                counts["fallback"] += 1
            elif node.summary_provenance == "retry":
                counts["retry"] += 1
                diagnostic = diagnostic_by_node.get(node.node_id) or {}
                counts["retry_attempts"] += max(
                    1, int(diagnostic.get("attempts", 2)) - 1
                )
            elif node.summary_provenance == "passthrough":
                counts["passthrough"] += 1
        return counts

    def _validate_vectors(
        self,
        vectors: list[tuple[str, object, dict]],
        node_ids: set[str],
        expected_dimension: int,
    ) -> dict:
        errors: list[str] = []
        seen: set[str] = set()
        dimensions: set[int] = set()
        for node_id, vector, metadata in vectors:
            if node_id in seen:
                errors.append(f"duplicate vector ID: {node_id}")
            seen.add(node_id)
            array = list(vector)
            dimensions.add(len(array))
            if len(array) != expected_dimension:
                errors.append(
                    f"vector dimension for {node_id} is {len(array)}, expected {expected_dimension}"
                )
            if not isinstance(metadata, dict):
                errors.append(f"vector metadata for {node_id} is not an object")
        missing = sorted(node_ids - seen)
        extra = sorted(seen - node_ids)
        if missing:
            errors.append(f"missing vectors for {len(missing)} node(s)")
        if extra:
            errors.append(f"vectors contain {len(extra)} detached node(s)")
        return {
            "errors": errors,
            "vector_count": len(vectors),
            "vector_ids": seen,
            "dimensions": sorted(dimensions),
            "dimension": expected_dimension if len(dimensions) <= 1 else None,
        }

    def _validate_candidate(
        self,
        trees: list[Tree],
        nodes: list[TreeNode],
        vectors: list[tuple[str, object, dict]],
        *,
        source_atom_ids: set[str],
        expected_dimension: int,
        excluded_source_atom_ids: set[str] | None = None,
    ) -> dict:
        errors: list[str] = []
        warnings: list[str] = []
        node_ids: set[str] = set()
        leaf_atom_ids: set[str] = set()
        # A forest may intentionally place one Spine atom in more than one
        # tree. Reject duplicate leaves within a tree, while allowing that
        # documented cross-tree routing behavior.
        leaf_atom_occurrences: dict[tuple[str, str], int] = {}
        approved_summary_ids: set[str] = set()
        summary_failure_codes: dict[str, int] = {}
        nodes_by_tree: dict[str, list[TreeNode]] = {}
        for node in nodes:
            if node.node_id in node_ids:
                errors.append(f"duplicate node ID: {node.node_id}")
            node_ids.add(node.node_id)
            nodes_by_tree.setdefault(node.tree_id, []).append(node)

        for tree in trees:
            tree_nodes = nodes_by_tree.get(tree.tree_id, [])
            by_id = {node.node_id: node for node in tree_nodes}
            if tree.node_count != len(tree_nodes):
                errors.append(f"tree {tree.tree_id} node_count does not match stored nodes")
            actual_leaves = [node for node in tree_nodes if node.level == 0]
            if tree.leaf_count != len(actual_leaves):
                errors.append(f"tree {tree.tree_id} leaf_count does not match level-0 nodes")
            if not tree.root_node_id or tree.root_node_id not in by_id:
                errors.append(f"tree {tree.tree_id} has no valid root")
                continue
            roots = [node for node in tree_nodes if node.parent_id is None]
            if len(roots) != 1 or roots[0].node_id != tree.root_node_id:
                errors.append(f"tree {tree.tree_id} must have exactly one root")
            root = by_id[tree.root_node_id]
            if tree.depth != root.level:
                errors.append(f"tree {tree.tree_id} depth must equal root level")
            if root.level > self.config.raptor_max_depth:
                errors.append(f"tree {tree.tree_id} exceeds raptor_max_depth")

            visiting: set[str] = set()
            visited: set[str] = set()

            def visit(node: TreeNode) -> None:
                if node.node_id in visiting:
                    errors.append(f"cycle detected at node {node.node_id}")
                    return
                if node.node_id in visited:
                    return
                visiting.add(node.node_id)
                if node.level == 0:
                    if node.children_ids:
                        errors.append(f"leaf {node.node_id} has children")
                    if node.node_type != "leaf":
                        warnings.append(f"level-0 node {node.node_id} is not typed leaf")
                    leaf_atom_ids.update(node.atom_ids)
                    for atom_id in node.atom_ids:
                        key = (node.tree_id, atom_id)
                        leaf_atom_occurrences[key] = leaf_atom_occurrences.get(key, 0) + 1
                else:
                    if not (node.summary_text or "").strip():
                        errors.append(f"summary node {node.node_id} is blank")
                    if node.summary_provenance not in {"llm", "retry"}:
                        errors.append(
                            f"summary node {node.node_id} is untrusted: "
                            f"provenance '{node.summary_provenance}' is not LLM-generated"
                        )
                    if not node.children_ids:
                        errors.append(f"summary node {node.node_id} has no children")
                    child_atom_ids: set[str] = set()
                    for child_id in node.children_ids:
                        child = by_id.get(child_id)
                        if child is None:
                            errors.append(f"node {node.node_id} references missing child {child_id}")
                            continue
                        if child.parent_id != node.node_id:
                            errors.append(f"child {child_id} has an invalid parent reference")
                        if child.level != node.level - 1:
                            errors.append(f"edge {node.node_id}->{child_id} skips a hierarchy level")
                        child_atom_ids.update(child.atom_ids)
                        visit(child)
                    child_summary_texts = [
                        by_id[child_id].summary_text or ""
                        for child_id in node.children_ids
                        if child_id in by_id
                    ]
                    quality = validate_summary(
                        node.summary_text,
                        self.config.raptor_summary_min_length,
                        child_texts=child_summary_texts,
                    )
                    if (
                        quality.valid
                        and node.summary_provenance in {"llm", "retry"}
                        and bool(node.children_ids)
                        and all(child_id in by_id for child_id in node.children_ids)
                    ):
                        approved_summary_ids.add(node.node_id)
                    else:
                        code = quality.code or "untrusted_provenance"
                        summary_failure_codes[code] = summary_failure_codes.get(code, 0) + 1
                        errors.append(
                            f"summary node {node.node_id} failed quality validation "
                            f"({code}): {quality.feedback or 'summary provenance is not verified'}"
                        )
                    if set(node.atom_ids) != child_atom_ids:
                        errors.append(f"parent {node.node_id} atom_ids do not equal child union")
                if node.parent_id is not None:
                    parent = by_id.get(node.parent_id)
                    if parent is None:
                        errors.append(f"node {node.node_id} references missing parent {node.parent_id}")
                    else:
                        if node.node_id not in parent.children_ids:
                            errors.append(f"parent {parent.node_id} omits child {node.node_id}")
                        if node.level != parent.level - 1:
                            errors.append(f"node {node.node_id} is not adjacent to its parent")
                visiting.remove(node.node_id)
                visited.add(node.node_id)

            visit(root)
            for detached in sorted(
                (node for node in tree_nodes if node.node_id not in visited),
                key=lambda node: node.node_id,
            ):
                visit(detached)
            if visited != set(by_id):
                errors.append(f"tree {tree.tree_id} contains detached nodes")

        indexed_source_atom_ids = source_atom_ids - set(excluded_source_atom_ids or set())
        missing_atoms = sorted(indexed_source_atom_ids - leaf_atom_ids)
        if missing_atoms:
            errors.append(f"{len(missing_atoms)} source atom(s) are not covered by leaves")
        unknown_atoms = sorted(leaf_atom_ids - source_atom_ids)
        if unknown_atoms:
            errors.append(f"{len(unknown_atoms)} leaf atom ID(s) are absent from Spine")
        duplicated_atoms = sorted(
            key for key, occurrences in leaf_atom_occurrences.items()
            if occurrences != 1
        )
        if duplicated_atoms:
            errors.append(
                f"{len(duplicated_atoms)} source atom/tree assignment(s) are represented by multiple leaves"
            )

        summary_counts = self._summary_counts(
            nodes,
            approved_summary_ids=approved_summary_ids,
            diagnostics=self.raptor.summary_diagnostics,
            validation_failure_codes=summary_failure_codes,
        )
        vector_validation = self._validate_vectors(vectors, node_ids, expected_dimension)
        errors.extend(vector_validation["errors"])
        healthy = not errors
        return {
            "healthy": healthy,
            "errors": errors,
            "warnings": warnings,
            "tree_count": len(trees),
            "node_count": len(nodes),
            "leaf_count": len(leaf_atom_ids),
            "indexed_atoms": len(leaf_atom_ids),
            "source_atoms": len(source_atom_ids),
            "indexed_source_atoms": len(indexed_source_atom_ids),
            "vector_count": len(vectors),
            "valid_summaries": summary_counts["valid"],
            "fallback_summaries": summary_counts["fallback"],
            "retry_summaries": summary_counts["retry"],
            "passthrough_summaries": summary_counts["passthrough"],
            "summary_quality": summary_counts,
            "quality_verified": not summary_failure_codes,
            "vector_dimension": vector_validation["dimension"],
            "checks": {
                "source_to_leaf_coverage": (
                    not missing_atoms and not unknown_atoms and not duplicated_atoms
                ),
                "one_root_per_tree": not any(
                    "no valid root" in error or "exactly one root" in error
                    for error in errors
                ),
                "acyclic": not any("cycle" in error for error in errors),
                "parent_child_references": not any("reference" in error or "omits child" in error for error in errors),
                "parent_atom_unions": not any("atom_ids" in error for error in errors),
                "vector_node_parity": not any("vector" in error or "detached node" in error for error in errors),
                "vector_dimensions": not any("dimension" in error for error in errors),
                "adjacent_levels": not any("level" in error or "adjacent" in error for error in errors),
                "nonblank_summaries": not any("blank" in error for error in errors),
                "summary_quality": not summary_failure_codes,
            },
        }

    def validate_index(self) -> dict:
        """Validate the active derived index against Spine and vectors."""
        source_atom_ids = {atom.atom_id for atom in self.spine.list_atoms()}
        pending_atom_ids = (
            self.forest.get_pending_atom_ids()
            | self.forest.get_pending_source_atom_ids()
        )
        trees = self.forest.list_trees()
        nodes = [node for tree in trees for node in self.forest.get_tree_nodes(tree.tree_id)]
        expected_dimension = int(
            getattr(self.embedder, "dimension", self.config.embedding_dimension)
        )
        try:
            vectors = list(self.vector_store.iter_vectors())
            result = self._validate_candidate(
                trees,
                nodes,
                vectors,
                source_atom_ids=source_atom_ids,
                expected_dimension=expected_dimension,
                excluded_source_atom_ids=pending_atom_ids,
            )
        except Exception as exc:
            result = {
                "healthy": False,
                "errors": [f"vector/index validation unavailable: {exc}"],
                "warnings": [],
                "tree_count": len(trees),
                "node_count": len(nodes),
                "leaf_count": len(self.forest.get_indexed_atom_ids()),
                "indexed_atoms": len(self.forest.get_indexed_atom_ids()),
                "source_atoms": len(source_atom_ids),
                "indexed_source_atoms": len(source_atom_ids - pending_atom_ids),
                "vector_count": 0,
                "valid_summaries": 0,
                "fallback_summaries": 0,
                "retry_summaries": 0,
                "passthrough_summaries": 0,
                "summary_quality": {
                    "valid": 0,
                    "fallback": 0,
                    "retry": 0,
                    "passthrough": 0,
                    "retry_attempts": 0,
                    "title_retry_attempts": 0,
                    "validation_failure_codes": {},
                },
                "quality_verified": False,
                "vector_dimension": None,
                "checks": {},
            }
        manifest = self.forest.get_index_build()
        latest_manifest = self.forest.get_latest_index_build()
        if manifest:
            if manifest.get("config_fingerprint") != self._config_fingerprint():
                result["warnings"].append("active build was created with a different configuration fingerprint")
            if int(manifest.get("embedding_dimension", expected_dimension)) != expected_dimension:
                result["errors"].append("active build embedding dimension differs from the configured embedder")
                result["healthy"] = False
            manifest_checks = {
                "source_atom_count": result.get("indexed_source_atoms", 0),
                "tree_count": result.get("tree_count", 0),
                "node_count": result.get("node_count", 0),
                "vector_count": result.get("vector_count", 0),
                "valid_summary_count": result.get("valid_summaries", 0),
            }
            for field_name, observed in manifest_checks.items():
                if int(manifest.get(field_name, observed)) != int(observed):
                    result["errors"].append(
                        f"active manifest {field_name} does not match observed index"
                    )
                    result["healthy"] = False
            if manifest.get("state") != "active":
                result["errors"].append("active manifest is not in active state")
                result["healthy"] = False
        elif latest_manifest and latest_manifest.get("state") == "failed":
            # A failed candidate deliberately has no active-build pointer.
            # Put its actual failure ahead of the legacy Cortex shell's
            # structural errors so recovery diagnostics explain the cause,
            # not merely the safe state that was retained afterward.
            failure = (latest_manifest.get("error") or "unknown build failure").strip()
            result["errors"].insert(
                0,
                "Latest derived-index build "
                f"{latest_manifest.get('build_id')} failed before activation: {failure}. "
                "Spine atoms and pending assignments were preserved (including source-level pending captures).",
            )
            result["healthy"] = False
        elif not manifest:
            result["errors"].append(
                "No verified active derived index exists. Run a fresh reindex after provider setup."
            )
            result["healthy"] = False

        if not result.get("quality_verified", False) and result.get("node_count", 0):
            result["errors"].append(
                "Stored summaries are structurally present but not quality verified. "
                "Run a clean reindex before querying."
            )
            result["healthy"] = False

        trusted = bool(
            manifest
            and manifest.get("state") == "active"
            and result["healthy"]
            and result.get("quality_verified", False)
        )
        if manifest and (
            int(manifest.get("fallback_summary_count", 0)) > 0
            or int(manifest.get("passthrough_summary_count", 0)) > 0
        ):
            result["errors"].append(
                "Active index is untrusted because it contains legacy fallback or passthrough summaries. "
                "Run a fresh reindex."
            )
            result["healthy"] = False
            trusted = False

        pending_assignments = self.forest.has_pending_assignments()
        pending_sources = self.forest.has_pending_source_atoms()
        pending = pending_assignments or pending_sources
        if pending:
            result["warnings"].append("source captures are pending organization")
            result["healthy"] = False
            result["errors"].append("pending captures are not part of the active index")
        result["active_build_id"] = manifest.get("build_id") if manifest else None
        result["active_build_state"] = manifest.get("state") if manifest else None
        if manifest:
            result["active_vector_collection"] = manifest.get("vector_collection")
        else:
            result["active_vector_collection"] = None
        result["manifest"] = manifest
        result["latest_build"] = latest_manifest
        result["last_failed_build"] = (
            latest_manifest if latest_manifest and latest_manifest.get("state") == "failed" else None
        )
        # Pending captures make the current workspace unhealthy, but they do
        # not invalidate the last verified active index: force/last-known-good
        # queries intentionally exclude those captures.
        result["trusted"] = trusted
        result["structure_present"] = bool(trees or nodes)
        result["quality_verified"] = bool(result.get("quality_verified", False))
        result["trust_state"] = "verified" if trusted else "untrusted"
        result["pending_source_atoms"] = len(self.forest.get_pending_source_atom_ids())
        result["validation_state"] = (
            "healthy" if result["healthy"] and manifest else
            "empty" if not source_atom_ids and not trees else
            "untrusted" if not trusted else
            "invalid" if not result["healthy"] else "unvalidated"
        )
        return result

    def index_manifest(self) -> dict | None:
        """Return the active build manifest without forcing a rebuild."""
        return self.forest.get_index_build()

    def query(
        self,
        query_text: str,
        top_k: int = 10,
        mode: Literal["hybrid", "tree", "flat", "lexical"] = "hybrid",
        *,
        force: bool = False,
        allow_hot_inbox: bool = False,
    ) -> QueryResult:
        health = self.validate_index()
        pending = self.forest.has_pending_work() or self._has_orphaned_atoms()
        if not force:
            if pending and not allow_hot_inbox:
                raise QueryBlockedError(
                    "New source captures are pending organization. Run 'tl organize' first, "
                    "or use --force to search only the last verified index."
                )
            if mode in ("tree", "flat"):
                if health.get("structure_present") and not health.get("trusted"):
                    raise QueryBlockedError(
                        "The active derived index is untrusted or unavailable. Run a fresh "
                        "'tl reindex --all' after Save & Verify; force query is unavailable "
                        "without a verified index."
                    )
                if not health.get("structure_present") and not allow_hot_inbox:
                    raise QueryBlockedError(
                        "The active derived index is untrusted or unavailable. Run a fresh "
                        "'tl reindex --all' after Save & Verify; force query is unavailable "
                        "without a verified index."
                    )
            if mode == "hybrid" and health.get("structure_present") and not health.get("trusted"):
                raise QueryBlockedError(
                    "The active derived index is untrusted or unavailable. Run a fresh "
                    "'tl reindex --all' after Save & Verify; force query is unavailable "
                    "without a verified index."
                )
            result = self.lattice.query(query_text=query_text, top_k=top_k, mode=mode)
            if pending and allow_hot_inbox:
                warning_msg = "Contains unorganized hot-inbox evidence"
                if warning_msg not in result.warnings:
                    result.warnings.append(warning_msg)
            else:
                for item in result.items:
                    if item.tree_id is None:
                        warning_msg = "Contains unorganized hot-inbox evidence"
                        if warning_msg not in result.warnings:
                            result.warnings.append(warning_msg)
                        break
            return result

        if not health.get("trusted"):
            raise QueryBlockedError(
                "Force query requires a last verified active index. Run Save & Verify, "
                "then 'tl reindex --all' to replace the untrusted index."
            )
        # Flat search is intentionally the only force path: tree traversal calls
        # an LLM.  Active vectors exclude queued source captures by construction.
        result = self.lattice.query(query_text=query_text, top_k=top_k, mode="flat")
        result.warnings.append(
            "Force query searched only the last verified index; pending source captures were excluded."
        )
        return result

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
        # Content-based estimate, intentionally approximate and independent of
        # whichever provider tokenizer is configured.
        source_atoms = self.spine.list_atoms()
        estimated_tokens = sum(
            max(1, round(max(len(atom.content), len(atom.content.split()) * 4) / 4))
            for atom in source_atoms
        )

        pending_atom_ids = (
            self.forest.get_pending_atom_ids()
            | self.forest.get_pending_source_atom_ids()
        )
        pending_tree_ids = self.forest.get_pending_tree_ids()
        indexed_atom_ids = self.forest.get_indexed_atom_ids()
        orphaned_atoms = sum(
            1
            for atom in self.spine.list_atoms()
            if atom.atom_id not in indexed_atom_ids and atom.atom_id not in pending_atom_ids
        )
        pending_atoms = len(pending_atom_ids)
        needs_recovery = orphaned_atoms > 0
        health = self.validate_index()
        manifest = self.forest.get_index_build()
        provider = resolve_active_provider()

        return DatabaseStatus(
            total_atoms=atoms_count,
            total_trees=len(trees),
            estimated_tokens=estimated_tokens,
            active_nodes=active_nodes,
            total_nodes=total_nodes,
            pending_atoms=pending_atoms,
            pending_trees=len(pending_tree_ids),
            orphaned_atoms=orphaned_atoms,
            needs_organization=bool(pending_atoms or needs_recovery),
            needs_recovery=needs_recovery,
            indexed_atoms=health.get("indexed_atoms", len(indexed_atom_ids)),
            valid_summaries=health.get("valid_summaries", 0),
            fallback_summaries=health.get("fallback_summaries", 0),
            vector_count=health.get("vector_count", 0),
            active_build_id=manifest.get("build_id") if manifest else None,
            validation_state=health.get("validation_state", "unvalidated"),
            validation_errors=health.get("errors", []),
            validation_warnings=health.get("warnings", []),
            index_trusted=bool(health.get("trusted")),
            structure_present=bool(health.get("structure_present", bool(trees))),
            quality_verified=bool(health.get("quality_verified", False)),
            credential_state=provider.credential_state,
            active_provider=provider.provider_id,
        )

    def trees(self) -> list[Tree]:
        return self.forest.list_trees()

    def _has_orphaned_atoms(self) -> bool:
        known_atom_ids = (
            self.forest.get_indexed_atom_ids()
            | self.forest.get_pending_atom_ids()
            | self.forest.get_pending_source_atom_ids()
        )
        return any(atom.atom_id not in known_atom_ids for atom in self.spine.list_atoms())

    def _queue_orphaned_atoms(self, tree_id: str | None = None) -> None:
        """Queue legacy Spine atoms before a verified build can route them."""
        known_atom_ids = (
            self.forest.get_indexed_atom_ids()
            | self.forest.get_pending_atom_ids()
            | self.forest.get_pending_source_atom_ids()
        )
        orphaned_atoms = [
            atom for atom in self.spine.list_atoms() if atom.atom_id not in known_atom_ids
        ]
        if not orphaned_atoms:
            return

        # Legacy atoms have no source-level queue record.  Preserve them there
        # and defer tree routing until the next verified staged build.
        self.forest.queue_source_atoms([atom.atom_id for atom in orphaned_atoms])

    def export(self, output_zip_path: str | Path) -> None:
        """Export Spine, Cortex, active vectors, manifest, and checksums."""
        zip_path = Path(output_zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        checksums: dict[str, str] = {}
        for file_name in [self.config.spine_db_name, self.config.cortex_db_name]:
            database_path = self.data_dir / file_name
            if database_path.exists():
                try:
                    with sqlite3.connect(str(database_path), timeout=30.0) as connection:
                        connection.execute("PRAGMA wal_checkpoint(FULL)")
                except sqlite3.Error:
                    # The main database is still exported; a concurrent writer
                    # can be diagnosed from the resulting checksum/archive.
                    pass
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_name in [self.config.spine_db_name, self.config.cortex_db_name]:
                p = self.data_dir / file_name
                if p.exists():
                    zipf.write(p, arcname=file_name)
                    checksums[file_name] = self._sha256_file(p)

            vector_dir = self.data_dir / self.config.vector_db_name
            if vector_dir.exists() and vector_dir.is_dir():
                for path in sorted(vector_dir.rglob("*")):
                    if not path.is_file():
                        continue
                    relative = Path(self.config.vector_db_name) / path.relative_to(vector_dir)
                    arcname = relative.as_posix()
                    zipf.write(path, arcname=arcname)
                    checksums[arcname] = self._sha256_file(path)

            manifest = self.forest.get_index_build()
            manifest_payload = {
                "active": manifest,
                "builds": self.forest.list_index_builds(),
                "validation": self.validate_index(),
            }
            manifest_bytes = json.dumps(
                manifest_payload, indent=2, ensure_ascii=False, default=str
            ).encode("utf-8")
            zipf.writestr("index-manifest.json", manifest_bytes)
            checksums["index-manifest.json"] = hashlib.sha256(manifest_bytes).hexdigest()
            checksum_bytes = json.dumps(checksums, indent=2, sort_keys=True).encode("utf-8")
            zipf.writestr("checksums.json", checksum_bytes)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def reset_derived(self) -> None:
        """Reset only rebuildable Cortex/vector state."""
        self.forest.reset_derived()
        reset = getattr(self.vector_store, "reset_derived", None)
        if callable(reset):
            reset()

    def garbage_collect_index(self, keep_build_ids: set[str] | None = None) -> dict:
        """Explicit maintenance operation for retained manifests/collections."""
        removed_builds = self.forest.garbage_collect_index_builds(keep_build_ids)
        removed_collections: list[str] = []
        collect = getattr(self.vector_store, "garbage_collect", None)
        if callable(collect):
            active = self.forest.get_index_build()
            keep_collections = {
                active.get("vector_collection")
            } if active and active.get("vector_collection") else set()
            removed_collections = collect(keep_collections)
        return {"builds": removed_builds, "vector_collections": removed_collections}
