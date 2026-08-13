"""Coverage for the one canonical, dense RAPTOR summary path."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from trace_lite.adapters import MockEmbedder
from trace_lite.adapters.llm import EmptyResponseError, redact_diagnostic
from trace_lite.cortex import ForestIndex, MockVectorStore
from trace_lite.cortex.forest import TreeNode
from trace_lite.engines import RaptorEngine, validate_summary
from trace_lite.spine import Atom
from trace_lite.ui import _configure_ui_logging


class _SequenceLLM:
    def __init__(self, responses: list[object], *, model: str = "openai/test") -> None:
        self.responses = list(responses)
        self.model = model
        self.prompts: list[str] = []
        self.budgets: list[int] = []

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        self.prompts.append(prompt)
        self.budgets.append(max_tokens)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return str(response)


def _children() -> list[TreeNode]:
    return [
        TreeNode(
            "child-a",
            "tree-a",
            0,
            "leaf",
            ["atom-a"],
            "Atlas v2 is not compatible before the signed migration.",
        ),
        TreeNode(
            "child-b",
            "tree-a",
            0,
            "leaf",
            ["atom-b"],
            "Only approved clients connect after the migration validation.",
        ),
    ]


def _engine(llm: _SequenceLLM, *, forest_path: str | Path = ":memory:") -> RaptorEngine:
    return RaptorEngine(
        llm=llm,
        embedder=MockEmbedder(dim=4),
        vector_store=MockVectorStore(dim=4),
        forest=ForestIndex(forest_path),
        summary_min_length=20,
        summary_retry_count=2,
        summary_max_tokens=300,
    )


def test_dense_semicolon_summary_preserves_meaning_bearing_words() -> None:
    source = (
        "Atlas v2 is not compatible without migration; rollout occurs only after "
        "signed validation."
    )
    candidate = (
        "Atlas v2 is not compatible without migration; rollout occurs only after "
        "signed validation."
    )

    result = validate_summary(candidate, minimum_length=24, child_texts=[source])

    assert result.valid
    assert result.normalized_text == candidate


@pytest.mark.parametrize(
    ("candidate", "code"),
    [
        ("", "empty_response"),
        ("Return only the summary prose.", "prompt_echo"),
        ("<think>reasoning</think> Atlas v2 migration is required.", "reasoning_output"),
    ],
)
def test_summary_quality_gate_still_rejects_unsafe_output(candidate: str, code: str) -> None:
    result = validate_summary(candidate, minimum_length=20, child_texts=["Atlas v2 migration"])

    assert result.valid is False
    assert result.code == code


def test_initial_and_corrective_prompts_request_the_same_dense_trace() -> None:
    llm = _SequenceLLM(
        [
            "Return only the summary prose.",
            "Atlas v2 is not compatible before migration; approved clients connect after validation.",
        ]
    )
    engine = _engine(llm)

    result = engine.generate_summary(_children())

    assert result.provenance == "retry"
    assert len(llm.prompts) == 2
    for prompt in llm.prompts:
        assert "Produce one dense, comprehensive, self-contained factual trace." in prompt
        assert "causal, temporal, identity, and version relationships" in prompt
        assert "compact clauses or semicolons" in prompt
        assert "unstructured keyword list" in prompt


def test_deepseek_length_empty_response_grows_summary_budget() -> None:
    llm = _SequenceLLM(
        [
            EmptyResponseError(finish_reason="length"),
            "Atlas v2 is not compatible before migration; approved clients connect after validation.",
        ],
        model="deepseek/deepseek-v4",
    )
    engine = _engine(llm)

    result = engine.generate_summary(_children())

    assert result.provenance == "retry"
    assert llm.budgets == [1024, 2048]


class _PairClustering:
    """Deterministic pair groups, sufficient to make a two-level test tree."""

    def cluster(self, embeddings):
        return [list(range(len(embeddings)))], []

    def split_oversized(self, indices, embeddings=None):
        del embeddings
        return [indices[index : index + 2] for index in range(0, len(indices), 2)]


class _RecordingEmbedder(MockEmbedder):
    def __init__(self) -> None:
        super().__init__(dim=4)
        self.inputs: list[list[str]] = []

    def embed(self, texts: list[str]):
        self.inputs.append(list(texts))
        return super().embed(texts)


def test_canonical_summary_is_stored_embedded_and_reused_for_parent_prompt(tmp_path: Path) -> None:
    canonical_first = "Alpha v1 *is not* compatible before migration; Beta remains required."
    canonical_second = "Gamma release follows Delta validation; no rollback without approval."
    root_summary = "Alpha v1 migration; Gamma release follows validation."
    llm = _SequenceLLM([canonical_first, canonical_second, root_summary])
    embedder = _RecordingEmbedder()
    forest = ForestIndex(tmp_path / "cortex.sqlite3")
    engine = RaptorEngine(
        llm=llm,
        embedder=embedder,
        vector_store=MockVectorStore(dim=4),
        forest=forest,
        clustering=_PairClustering(),
        max_children=2,
        max_depth=3,
        summary_min_length=20,
        summary_retry_count=0,
    )
    atoms = [
        Atom.create("a", "Alpha v1 requires migration before release.", "source", 0, 0, 43),
        Atom.create("b", "Beta remains required during migration.", "source", 1, 0, 39),
        Atom.create("c", "Gamma release follows validation.", "source", 2, 0, 33),
        Atom.create("d", "Delta requires approval before rollback.", "source", 3, 0, 40),
    ]

    engine.build_tree("tree-canonical", atoms, persist=True)

    stored_nodes = forest.get_tree_nodes("tree-canonical")
    first_node = next(node for node in stored_nodes if node.summary_text == canonical_first)
    second_node = next(node for node in stored_nodes if node.summary_text == canonical_second)
    assert first_node.summary_provenance == "llm"
    assert second_node.summary_provenance == "llm"
    assert [canonical_first] in embedder.inputs
    assert [canonical_second] in embedder.inputs
    assert canonical_first in llm.prompts[-1]
    assert canonical_second in llm.prompts[-1]


def test_ui_logging_is_operationally_quiet_and_diagnostics_redact_payloads() -> None:
    root = logging.getLogger()
    previous_root_level = root.level
    previous_handlers = list(root.handlers)
    previous_formatters = {handler: handler.formatter for handler in root.handlers}
    names = [
        "trace_lite",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "LiteLLM",
        "LiteLLM Router",
        "LiteLLM Proxy",
        "litellm",
        "httpcore",
        "httpx",
        "fastapi",
        "starlette",
    ]
    previous_levels = {name: logging.getLogger(name).level for name in names}
    try:
        _configure_ui_logging()
        assert root.level == logging.INFO
        assert logging.getLogger("trace_lite").level == logging.INFO
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert logging.getLogger("fastapi").level == logging.WARNING
        assert logging.getLogger("LiteLLM").level == logging.WARNING
    finally:
        root.setLevel(previous_root_level)
        for handler in list(root.handlers):
            if handler not in previous_handlers:
                root.removeHandler(handler)
                handler.close()
            else:
                handler.setFormatter(previous_formatters[handler])
        for name, level in previous_levels.items():
            logging.getLogger(name).setLevel(level)

    diagnostic = (
        "curl -X POST https://provider.test/v1/chat -H 'Authorization: Bearer sk-super-secret-token' "
        "--data '{\"messages\":[{\"role\":\"user\",\"content\":\"private source passage\"}]}'\n"
        "request payload: {\"prompt\": \"another private passage\", \"nested\": [\"secret\"]}"
    )
    redacted = redact_diagnostic(diagnostic)

    assert "private source passage" not in redacted
    assert "another private passage" not in redacted
    assert "sk-super-secret-token" not in redacted
    assert "[REDACTED]" in redacted
