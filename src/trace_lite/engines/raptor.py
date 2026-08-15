"""RAPTOR tree construction with strict hierarchy and summary quality gates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import numpy as np

from trace_lite.adapters import EmbeddingAdapter, LLMAdapter
from trace_lite.adapters.llm import (
    EmptyResponseError,
    LLMPreflightError,
    OllamaAvailabilityError,
    RejectedCredentialError,
    TemporaryProviderError,
    UnsupportedModelError,
)
from trace_lite.cortex import ClusteringPipeline, ForestIndex, Tree, TreeNode, VectorStore
from trace_lite.diagnostics import BuildDiagnostic, diagnostic_dict
from trace_lite.engines.summary import (
    SummaryResult,
    normalize_markdown_for_summary,
    validate_summary,
)
from trace_lite.spine import Atom


@dataclass
class RaptorBuild:
    """In-memory candidate produced before a Cortex/vector activation."""

    tree: Tree
    nodes: list[TreeNode]
    vectors: list[tuple[str, np.ndarray, dict]]


class SummaryGenerationError(RuntimeError):
    """A required LLM-produced summary could not be generated safely."""

    def __init__(
        self,
        message: str,
        *,
        failure_code: str = "summary_generation_failed",
        attempts: int = 0,
        failure_codes: tuple[str, ...] = (),
    ) -> None:
        self.failure_code = failure_code
        self.attempts = attempts
        self.failure_codes = failure_codes
        super().__init__(message)


def _provider_failure_code(error: BaseException) -> str:
    if isinstance(error, EmptyResponseError):
        return "empty_response"
    if isinstance(error, TemporaryProviderError):
        return "provider_temporary"
    if isinstance(error, LLMPreflightError):
        return type(error).__name__.replace("Error", "").casefold()
    return "provider_error"


def _fatal_provider_error(error: BaseException) -> bool:
    return isinstance(
        error,
        (OllamaAvailabilityError, RejectedCredentialError, UnsupportedModelError),
    )


def _emit_summary_diagnostic(
    sink,
    *,
    tree_id: str | None,
    node_id: str | None,
    attempt: int,
    max_attempts: int,
    failure_code: str | None,
    message: str,
    status: str,
) -> None:
    if sink is None:
        return
    event = BuildDiagnostic(
        stage="summary_generation",
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
    try:
        sink(diagnostic_dict(event))
    except Exception:
        return


def _emit_embedding_diagnostic(
    sink,
    *,
    tree_id: str | None,
    node_id: str | None = None,
    message: str,
    status: str,
    failure_code: str | None = None,
) -> None:
    """Report vector work without exposing source-bearing payloads."""
    if sink is None:
        return
    event = BuildDiagnostic(
        stage="embedding",
        tree_id=tree_id,
        node_id=node_id,
        message=message[:300],
        failure_code=failure_code,
        status=status,
        retrying=status == "retrying",
        final_failure=status == "final_failure",
    )
    try:
        sink(diagnostic_dict(event))
    except Exception:
        return


class RaptorEngine:
    """Build a bounded, adjacent-level summary tree from immutable atoms."""

    def __init__(
        self,
        llm: LLMAdapter,
        embedder: EmbeddingAdapter,
        vector_store: VectorStore,
        forest: ForestIndex,
        clustering: ClusteringPipeline | None = None,
        max_depth: int = 5,
        max_children: int = 8,
        summary_min_length: int = 24,
        summary_retry_count: int = 3,
        summary_max_tokens: int = 300,
        max_children_per_summary: int | None = None,
        retry_count: int | None = None,
    ):
        if max_children_per_summary is not None:
            max_children = max_children_per_summary
        if retry_count is not None:
            summary_retry_count = retry_count
        if max_children < 2:
            raise ValueError("max_children must be at least 2")
        if max_depth < 0:
            raise ValueError("max_depth cannot be negative")
        if summary_min_length < 1:
            raise ValueError("summary_min_length must be positive")
        if summary_retry_count < 0:
            raise ValueError("summary_retry_count cannot be negative")
        if summary_max_tokens < 1:
            raise ValueError("summary_max_tokens must be positive")
        self.llm = llm
        self.embedder = embedder
        self.vector_store = vector_store
        self.forest = forest
        self.clustering = clustering or ClusteringPipeline(max_children=max_children)
        self.max_depth = max_depth
        self.max_children = max_children
        self.summary_min_length = summary_min_length
        self.summary_retry_count = summary_retry_count
        self.summary_max_tokens = summary_max_tokens
        self.last_build: RaptorBuild | None = None
        self.summary_diagnostics: list[dict] = []
        self.diagnostic_sink = None

    def _initial_summary_budget(self) -> int:
        """Return the provider-aware starting budget for a summary request.

        DeepSeek thinking models can consume a small completion budget on
        hidden reasoning before emitting any visible text.  Keep this policy at
        the summary call site so the adapter contract remains ``complete(...)``
        for every provider and caller.
        """
        model = getattr(self.llm, "model", "")
        if isinstance(model, str) and model.strip().casefold().startswith("deepseek/"):
            return max(self.summary_max_tokens, 1024)
        return self.summary_max_tokens

    @staticmethod
    def _is_length_limited_empty_response(error: BaseException) -> bool:
        """Whether an empty provider response was truncated by its token cap."""
        return (
            isinstance(error, EmptyResponseError)
            and isinstance(getattr(error, "finish_reason", None), str)
            and error.finish_reason.casefold() == "length"
        )

    def build_tree(
        self,
        tree_id: str,
        atoms: list[Atom],
        tree_name: str | None = None,
        *,
        persist: bool = True,
        vector_store: VectorStore | None = None,
        diagnostic_sink=None,
    ) -> Tree:
        """Build a tree and optionally publish it for legacy direct callers.

        ``persist=False`` is used by the versioned full-index builder.  It
        leaves the existing ForestIndex and active vector collection untouched.
        """
        previous_node_ids = {
            node.node_id for node in self.forest.get_tree_nodes(tree_id)
        } if persist else set()
        build = self._build_candidate(
            tree_id,
            atoms,
            tree_name=tree_name,
            diagnostic_sink=diagnostic_sink,
        )
        self.last_build = build

        if persist:
            target_store = vector_store or self.vector_store
            target_store.upsert_batch(build.vectors)
            self.forest.replace_tree(build.tree, build.nodes)
            new_node_ids = {node.node_id for node in build.nodes}
            for node_id in previous_node_ids - new_node_ids:
                target_store.delete(node_id)
            from trace_lite.engines.graph import generate_all_edges
            edges = generate_all_edges(atoms, build.nodes)
            self.forest.store_edges(edges)
        return build.tree

    def _build_candidate(
        self,
        tree_id: str,
        atoms: list[Atom],
        tree_name: str | None = None,
        *,
        diagnostic_sink=None,
    ) -> RaptorBuild:
        now = datetime.now(timezone.utc).isoformat()
        if not atoms:
            tree = Tree(
                tree_id=tree_id,
                name=tree_name or f"Tree {tree_id[:8]}",
                description="Empty tree",
                root_node_id=None,
                node_count=0,
                leaf_count=0,
                depth=0,
                created_at=now,
                last_consolidated=now,
            )
            return RaptorBuild(tree=tree, nodes=[], vectors=[])

        atom_texts = [atom.content for atom in atoms]
        _emit_embedding_diagnostic(
            diagnostic_sink,
            tree_id=tree_id,
            message=f"Embedding {len(atom_texts)} source atom(s).",
            status="attempt",
        )
        try:
            embeddings = np.asarray(self.embedder.embed(atom_texts), dtype=np.float32)
        except Exception as exc:
            _emit_embedding_diagnostic(
                diagnostic_sink,
                tree_id=tree_id,
                message="Source embedding failed; activation was prevented.",
                status="final_failure",
                failure_code=type(exc).__name__.casefold(),
            )
            raise
        if len(embeddings) != len(atoms):
            raise ValueError("Embedding adapter returned the wrong number of vectors.")
        if embeddings.ndim != 2 or embeddings.shape[1] < 1:
            raise ValueError("Embedding adapter returned invalid vector dimensions.")
        _emit_embedding_diagnostic(
            diagnostic_sink,
            tree_id=tree_id,
            message="Source embeddings are ready.",
            status="success",
        )

        level_nodes: list[TreeNode] = []
        level_embeddings: list[np.ndarray] = []
        vector_batch: list[tuple[str, np.ndarray, dict]] = []
        for atom, embedding in zip(atoms, embeddings):
            node_id = f"node-{uuid.uuid4().hex[:12]}"
            node = TreeNode(
                node_id=node_id,
                tree_id=tree_id,
                level=0,
                node_type="leaf",
                atom_ids=[atom.atom_id],
                summary_text=atom.content,
                summary_provenance="source",
                created_at=now,
                last_accessed=now,
            )
            level_nodes.append(node)
            level_embeddings.append(np.asarray(embedding, dtype=np.float32))
            vector_batch.append(self._vector_row(node, embedding))

        all_nodes = list(level_nodes)
        current_level = 0
        current_nodes = level_nodes
        current_embeddings = np.asarray(level_embeddings, dtype=np.float32)

        while len(current_nodes) > 1:
            if current_level >= self.max_depth:
                raise ValueError(
                    f"RAPTOR depth limit {self.max_depth} cannot represent all nodes "
                    "with the configured summary fanout."
                )
            groups = self._complete_groups(current_embeddings)
            if not groups:
                raise ValueError("RAPTOR clustering produced no complete assignment.")
            # At the final possible level there must be one bounded parent.  A
            # clear failure is safer than silently publishing a detached forest.
            if current_level + 1 == self.max_depth and len(groups) > 1:
                if len(current_nodes) <= self.max_children:
                    groups = [list(range(len(current_nodes)))]
                else:
                    raise ValueError(
                        "RAPTOR max_depth/max_children cannot form one bounded root."
                    )

            next_nodes: list[TreeNode] = []
            next_embeddings: list[np.ndarray] = []
            for group in groups:
                children = [current_nodes[index] for index in group]
                child_embeddings = np.asarray(
                    [current_embeddings[index] for index in group], dtype=np.float32
                )
                parent_id = f"node-{uuid.uuid4().hex[:12]}"
                result = self._summary_for_children(
                    children,
                    tree_id=tree_id,
                    node_id=parent_id,
                    diagnostic_sink=diagnostic_sink,
                )
                atom_ids = list(dict.fromkeys(
                    atom_id for child in children for atom_id in child.atom_ids
                ))
                parent = TreeNode(
                    node_id=parent_id,
                    tree_id=tree_id,
                    level=current_level + 1,
                    node_type="cluster_summary",
                    atom_ids=atom_ids,
                    summary_text=result.text,
                    summary_provenance=result.provenance,
                    children_ids=[child.node_id for child in children],
                    created_at=now,
                    last_accessed=now,
                )
                for child in children:
                    child.parent_id = parent_id
                self.summary_diagnostics.append(
                    {
                        "node_id": parent_id,
                        "tree_id": tree_id,
                        "provenance": result.provenance,
                        "attempts": result.attempts,
                        "failure_codes": list(result.failure_codes),
                    }
                )
                try:
                    parent_embedding = np.asarray(
                        self.embedder.embed([result.text])[0], dtype=np.float32
                    )
                except Exception as exc:
                    _emit_embedding_diagnostic(
                        diagnostic_sink,
                        tree_id=tree_id,
                        node_id=parent_id,
                        message="Summary embedding failed; activation was prevented.",
                        status="final_failure",
                        failure_code=type(exc).__name__.casefold(),
                    )
                    raise
                next_nodes.append(parent)
                next_embeddings.append(parent_embedding)
                all_nodes.append(parent)
                vector_batch.append(self._vector_row(parent, parent_embedding))

            current_nodes = next_nodes
            current_embeddings = np.asarray(next_embeddings, dtype=np.float32)
            current_level += 1

        root = current_nodes[0]
        root.node_type = "root_summary" if root.level > 0 else "leaf"
        root_node_id = root.node_id
        description = (root.summary_text or "Summary tree").strip()
        if len(description) > 200:
            description = description[:197].rsplit(" ", 1)[0] + "..."
        tree = Tree(
            tree_id=tree_id,
            name=tree_name or f"Tree {tree_id[:8]}",
            description=description or "Summary tree",
            root_node_id=root_node_id,
            node_count=len(all_nodes),
            leaf_count=len(atoms),
            depth=root.level,
            created_at=now,
            last_consolidated=now,
        )
        # The vector metadata for the final root must reflect its final type.
        for index, (node_id, embedding, metadata) in enumerate(vector_batch):
            if node_id == root.node_id:
                vector_batch[index] = (
                    node_id,
                    embedding,
                    {**metadata, "node_type": root.node_type},
                )
        return RaptorBuild(tree=tree, nodes=all_nodes, vectors=vector_batch)

    def _vector_row(
        self, node: TreeNode, embedding: np.ndarray
    ) -> tuple[str, np.ndarray, dict]:
        return (
            node.node_id,
            np.asarray(embedding, dtype=np.float32),
            {
                "tree_id": node.tree_id,
                "node_type": node.node_type,
                "level": node.level,
                "summary": node.summary_text,
                "summary_provenance": node.summary_provenance,
                "atom_ids": list(node.atom_ids),
            },
        )

    def _complete_groups(self, embeddings: np.ndarray) -> list[list[int]]:
        """Return a complete, bounded assignment for the current layer."""
        n_items = len(embeddings)
        clusters, orphans = self.clustering.cluster(embeddings)
        groups: list[list[int]] = []
        used: set[int] = set()
        for cluster in clusters:
            clean = [index for index in cluster if 0 <= index < n_items and index not in used]
            if clean:
                for bounded in self.clustering.split_oversized(
                    clean, embeddings=embeddings
                ):
                    # A caller may provide a clustering implementation with a
                    # different fanout setting; RAPTOR's hierarchy contract is
                    # the final authority at the publication boundary.
                    groups.extend(
                        bounded[offset : offset + self.max_children]
                        for offset in range(0, len(bounded), self.max_children)
                    )
                used.update(clean)

        noise = [index for index in orphans if 0 <= index < n_items and index not in used]
        # Density noise is assigned to the closest semantic centroid, rather
        # than promoted unchanged. With no centroid, deterministic grouping
        # still preserves every item and keeps the build reproducible; this is
        # clustering behavior, never summary text fallback.
        if noise and groups:
            for index in sorted(noise):
                # A full group creates a new bucket.  Recompute the
                # candidate centroids on every iteration because that bucket
                # is then eligible for subsequent noise items; retaining one
                # centroid list for the original groups causes an index error
                # as soon as more than one noise item is assigned this way.
                available = [
                    group_index
                    for group_index, group in enumerate(groups)
                    if len(group) < self.max_children
                ]
                if not available:
                    groups.append([index])
                else:
                    best = max(
                        available,
                        key=lambda group_index: (
                            self.clustering.cosine_similarity(
                                embeddings[index],
                                np.mean(embeddings[groups[group_index]], axis=0),
                            ),
                            -group_index,
                        ),
                    )
                    groups[best].append(index)
                used.add(index)
        elif noise:
            for offset in range(0, len(noise), self.max_children):
                groups.append(sorted(noise[offset : offset + self.max_children]))
                used.update(noise[offset : offset + self.max_children])

        remaining = [index for index in range(n_items) if index not in used]
        for offset in range(0, len(remaining), self.max_children):
            groups.append(remaining[offset : offset + self.max_children])

        # Merge a trailing singleton where possible. If it is unavoidable, keep
        # it as a real group: its parent still requires an LLM summary, so no
        # passthrough node can enter a newly built index.
        if len(groups) > 1 and len(groups[-1]) == 1:
            for index in range(len(groups) - 1):
                if len(groups[index]) < self.max_children:
                    groups[index].extend(groups.pop())
                    break
        return [sorted(group) for group in groups if group]

    def _summary_for_children(
        self,
        nodes: list[TreeNode],
        *,
        tree_id: str | None = None,
        node_id: str | None = None,
        diagnostic_sink=None,
    ) -> SummaryResult:
        diagnostic_sink = diagnostic_sink if diagnostic_sink is not None else self.diagnostic_sink
        # Source atoms can contain Markdown presentation noise.  Once an LLM
        # summary has passed validation, however, its exact canonical text is
        # used unchanged in all subsequent parent-summary prompts.  There is
        # no second dense trace, stopword-filtered copy, or retrieval variant.
        texts = [
            normalize_markdown_for_summary(node.summary_text or "")
            if node.level == 0
            else (node.summary_text or "")
            for node in nodes
        ]
        texts = [text.strip() for text in texts if text.strip()]
        if not texts:
            raise SummaryGenerationError("Cannot summarize blank child content.")

        combined = "\n\n".join(
            f"Child {index + 1}:\n{text}" for index, text in enumerate(texts)
        )
        summary_instruction = (
            "Produce one dense, comprehensive, self-contained factual trace. "
            "Preserve entities, facts, headings, qualifiers, negation, and causal, temporal, "
            "identity, and version relationships. Remove repetition, filler, generic introductions, "
            "and meta-commentary. Use compact clauses or semicolons when useful; prioritize "
            "information density over literary style, but do not produce an unstructured keyword list. "
            "Return only the summary prose."
        )
        prompt = f"{summary_instruction}\n\nChild passages:\n{combined}\n\nSummary:"
        max_attempts = self.summary_retry_count + 1
        attempts = 0
        last_error: BaseException | None = None
        last_code = "summary_generation_failed"
        feedback = ""
        failure_codes: list[str] = []
        completion_budget = self._initial_summary_budget()
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            current_prompt = prompt
            if feedback:
                current_prompt = (
                    f"{summary_instruction}\n\n"
                    f"The previous candidate failed the deterministic check `{last_code}`: {feedback}\n\n"
                    f"Child passages:\n{combined}\n\nSummary:"
                )
            try:
                response = self.llm.complete(
                    current_prompt, max_tokens=completion_budget
                )
                validation = validate_summary(
                    response,
                    self.summary_min_length,
                    child_texts=texts,
                )
                if validation.valid:
                    _emit_summary_diagnostic(
                        diagnostic_sink,
                        tree_id=tree_id,
                        node_id=node_id,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        failure_code=None,
                        message="Summary passed deterministic validation.",
                        status="success",
                    )
                    return SummaryResult(
                        text=validation.normalized_text,
                        provenance="llm" if attempt == 1 else "retry",
                        attempts=attempts,
                        failure_codes=tuple(failure_codes),
                    )
                last_code = validation.code or "invalid_summary"
                feedback = validation.feedback
            except Exception as exc:
                last_error = exc
                if _fatal_provider_error(exc):
                    code = _provider_failure_code(exc)
                    _emit_summary_diagnostic(
                        diagnostic_sink,
                        tree_id=tree_id,
                        node_id=node_id,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        failure_code=code,
                        message="Provider configuration is unavailable; summary generation stopped.",
                        status="final_failure",
                    )
                    raise
                last_code = _provider_failure_code(exc)
                if self._is_length_limited_empty_response(exc):
                    completion_budget = min(completion_budget * 2, 2048)
                    feedback = (
                        "Retry the summary request after the provider returned no visible response "
                        "at its completion limit."
                    )
                else:
                    feedback = "Retry the summary request after the provider returned no usable response."

            failure_codes.append(last_code)
            if attempt >= max_attempts:
                _emit_summary_diagnostic(
                    diagnostic_sink,
                    tree_id=tree_id,
                    node_id=node_id,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    failure_code=last_code,
                    message=feedback or "Summary failed deterministic validation.",
                    status="final_failure",
                )
                reason = (
                    type(last_error).__name__
                    if last_error is not None
                    else (feedback or "summary response failed deterministic validation")
                )
                raise SummaryGenerationError(
                    f"LLM summary generation failed after {attempts} attempt(s): "
                    f"{last_code} ({reason}); activation was prevented. "
                    "Correct the provider/output and retry the build.",
                    failure_code=last_code,
                    attempts=attempts,
                    failure_codes=tuple(failure_codes),
                ) from last_error
            _emit_summary_diagnostic(
                diagnostic_sink,
                tree_id=tree_id,
                node_id=node_id,
                attempt=attempt,
                max_attempts=max_attempts,
                failure_code=last_code,
                message=feedback or "Summary failed; retrying with corrective instructions.",
                status="retrying",
            )

        raise SummaryGenerationError("LLM summary generation failed; activation was prevented.")

    def _summarize_cluster(self, nodes: list[TreeNode]) -> str:
        """Compatibility helper retained for callers of the original engine."""
        return self._summary_for_children(nodes).text

    def generate_summary(self, nodes: list[TreeNode]) -> SummaryResult:
        """Return guarded summary text and provenance for diagnostics/tests."""
        return self._summary_for_children(nodes)
