"""Side-effect-free staged routing for derived forest builds."""

from __future__ import annotations

import uuid

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
from trace_lite.cortex import ForestIndex, Tree, VectorStore
from trace_lite.diagnostics import BuildDiagnostic, diagnostic_dict
from trace_lite.engines.summary import ValidationResult, validate_title
from trace_lite.spine import Atom


class TreeNamingError(RuntimeError):
    """The LLM could not produce a usable title for a new staged tree."""

    def __init__(
        self,
        message: str,
        *,
        failure_code: str = "tree_naming_failed",
        attempts: int = 0,
    ) -> None:
        self.failure_code = failure_code
        self.attempts = attempts
        super().__init__(message)


def _emit(
    sink,
    *,
    tree_id: str | None,
    attempt: int,
    max_attempts: int,
    failure_code: str | None,
    message: str,
    status: str,
) -> None:
    if sink is None:
        return
    event = BuildDiagnostic(
        stage="tree_naming",
        tree_id=tree_id,
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
        # Diagnostics are observability only.  A broken optional sink must not
        # change the staged build's publication semantics.
        return


def _provider_failure_code(error: BaseException) -> str:
    if isinstance(error, EmptyResponseError):
        return "empty_response"
    if isinstance(error, TemporaryProviderError):
        return "provider_temporary"
    if isinstance(error, LLMPreflightError):
        return type(error).__name__.replace("Error", "").casefold()
    return "provider_error"


def _fatal_provider_error(error: BaseException) -> bool:
    """Return whether a provider failure must not be retried."""
    return isinstance(
        error,
        (OllamaAvailabilityError, RejectedCredentialError, UnsupportedModelError),
    )


class ForestRouter:
    """Plan semantic routing without ever mutating Cortex state.

    Ingestion stores only immutable source atoms in a pending queue.  Tree IDs,
    titles, and memberships become durable only when the complete staged build
    has passed preflight, summary generation, vector validation, and activation.
    """

    # DeepSeek V4 thinking mode counts hidden reasoning against max_tokens. A
    # 20-token budget can therefore end with no visible title at all. The
    # adapter disables thinking for this bounded title request and this budget
    # leaves room for providers that still perform a small amount of internal
    # work before returning the 2-5 word label.
    _TITLE_MAX_TOKENS = 128

    def __init__(
        self,
        embedder: EmbeddingAdapter,
        llm: LLMAdapter,
        forest: ForestIndex,
        vector_store: VectorStore,
        placement_threshold: float = 0.55,
        max_trees_per_atom: int = 3,
        title_retry_count: int = 3,
    ):
        if title_retry_count < 0:
            raise ValueError("title_retry_count cannot be negative")
        self.embedder = embedder
        self.llm = llm
        self.forest = forest
        self.vector_store = vector_store
        self.placement_threshold = placement_threshold
        self.max_trees_per_atom = max_trees_per_atom
        self.title_retry_count = title_retry_count
        self.naming_diagnostics: list[dict] = []

    def route_plan(
        self,
        atoms: list[Atom],
        existing_trees: list[Tree] | None = None,
        *,
        diagnostic_sink=None,
    ) -> list[tuple[str, str, list[Atom]]]:
        """Return staged ``(tree_id, tree_name, atoms)`` assignments."""
        if not atoms:
            return []
        trees = list(existing_trees) if existing_trees is not None else self.forest.list_trees()
        if not trees:
            tree_id = self._new_tree_id()
            return [
                (
                    tree_id,
                    self._generate_tree_name(
                        atoms[:5], tree_id=tree_id, diagnostic_sink=diagnostic_sink
                    ),
                    list(atoms),
                )
            ]

        roots: list[tuple[Tree, object]] = []
        root_embeddings: list[object] = []
        for tree in trees:
            if not tree.root_node_id:
                continue
            root = self.forest.get_node(tree.root_node_id)
            if root and root.summary_text:
                roots.append((tree, root))
                root_embeddings.append(self.embedder.embed([root.summary_text])[0])

        # A retained legacy shell has no usable semantic root.  Keep its
        # existing name/id during the candidate rebuild rather than inventing a
        # persistent default tree before any LLM-derived candidate is valid.
        if not root_embeddings:
            return [(trees[0].tree_id, trees[0].name, list(atoms))]

        root_matrix = np.vstack(root_embeddings)
        atom_embeddings = self.embedder.embed([atom.content for atom in atoms])
        mapping: dict[str, list[Atom]] = {}
        names = {tree.tree_id: tree.name for tree in trees}
        unmatched: list[Atom] = []
        for atom, embedding in zip(atoms, atom_embeddings):
            norm = np.linalg.norm(embedding) + 1e-9
            similarities = []
            for index, (tree, _root) in enumerate(roots):
                root_embedding = root_matrix[index]
                similarity = float(
                    np.dot(embedding, root_embedding)
                    / (norm * (np.linalg.norm(root_embedding) + 1e-9))
                )
                similarities.append((tree.tree_id, similarity))
            matching = [
                tree_id
                for tree_id, similarity in sorted(
                    similarities, key=lambda item: item[1], reverse=True
                )[: self.max_trees_per_atom]
                if similarity >= self.placement_threshold
            ]
            if matching:
                for tree_id in matching:
                    mapping.setdefault(tree_id, []).append(atom)
            else:
                unmatched.append(atom)

        if unmatched:
            tree_id = self._new_tree_id()
            names[tree_id] = self._generate_tree_name(
                unmatched[:5], tree_id=tree_id, diagnostic_sink=diagnostic_sink
            )
            mapping[tree_id] = unmatched
        return [(tree_id, names[tree_id], group) for tree_id, group in mapping.items()]

    def route(self, atoms: list[Atom]) -> dict[str, list[Atom]]:
        """Compatibility view of a staged route; it intentionally does not save."""
        return {tree_id: group for tree_id, _name, group in self.route_plan(atoms)}

    @staticmethod
    def _new_tree_id() -> str:
        return f"tree-{uuid.uuid4().hex[:12]}"

    def _generate_tree_name(
        self,
        sample_atoms: list[Atom],
        *,
        tree_id: str | None = None,
        diagnostic_sink=None,
    ) -> str:
        sample_text = "\n".join(f"- {atom.content[:100]}" for atom in sample_atoms)
        base_prompt = (
            "Give a short 2-5 word topic label for a knowledge tree containing these notes. "
            "Return only the title.\n\n"
            f"Notes:\n{sample_text}\n\nTitle:"
        )
        max_attempts = self.title_retry_count + 1
        failures: list[str] = []
        feedback = ""
        last_code = "tree_naming_failed"

        for attempt in range(1, max_attempts + 1):
            prompt = base_prompt
            if feedback:
                prompt = (
                    "Produce a corrected topic label for the notes below. "
                    "Return only the label. The previous candidate failed the "
                    f"deterministic check `{last_code}`: {feedback}\n\n"
                    f"Notes:\n{sample_text}\n\nCorrected title:"
                )
            try:
                response = self.llm.complete(
                    prompt,
                    max_tokens=self._TITLE_MAX_TOKENS,
                )
                validation: ValidationResult = validate_title(response, 2, 5)
                if validation.valid:
                    _emit(
                        diagnostic_sink,
                        tree_id=tree_id,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        failure_code=None,
                        message="Tree title passed deterministic validation.",
                        status="success",
                    )
                    return validation.normalized_text
                last_code = validation.code or "invalid_title"
                feedback = validation.feedback
                self.naming_diagnostics.append(
                    {"tree_id": tree_id, "attempt": attempt, "failure_code": last_code}
                )
            except Exception as exc:
                if _fatal_provider_error(exc):
                    code = _provider_failure_code(exc)
                    _emit(
                        diagnostic_sink,
                        tree_id=tree_id,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        failure_code=code,
                        message="Provider configuration is unavailable; title generation stopped.",
                        status="final_failure",
                    )
                    raise
                last_code = _provider_failure_code(exc)
                feedback = "Retry the title request after the provider returned no usable response."
                self.naming_diagnostics.append(
                    {"tree_id": tree_id, "attempt": attempt, "failure_code": last_code}
                )

            failures.append(last_code)
            if attempt >= max_attempts:
                _emit(
                    diagnostic_sink,
                    tree_id=tree_id,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    failure_code=last_code,
                    message=feedback or "Tree title failed deterministic validation.",
                    status="final_failure",
                )
                raise TreeNamingError(
                    f"LLM tree naming failed after {attempt} attempt(s) "
                    f"({last_code}); activation was prevented. "
                    "Correct the provider/output and retry the build.",
                    failure_code=last_code,
                    attempts=attempt,
                )
            _emit(
                diagnostic_sink,
                tree_id=tree_id,
                attempt=attempt,
                max_attempts=max_attempts,
                failure_code=last_code,
                message=feedback or "Tree title failed; retrying with corrective instructions.",
                status="retrying",
            )

        # The loop always returns or raises; keep a defensive error for static
        # type checkers and unusual subclasses.
        raise TreeNamingError("LLM tree naming failed; activation was prevented.")
