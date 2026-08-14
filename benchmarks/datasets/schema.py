"""Pydantic schemas for benchmark manifests, cases, and evaluation queries."""

from typing import Literal
from pydantic import BaseModel, Field


QueryCategory = Literal[
    "direct_lookup",
    "chronology",
    "contradiction",
    "cross_domain",
    "global_context",
    "multi_hop",
    "historical",
    "out_of_scope",
]


class BenchmarkCase(BaseModel):
    """A single reviewed query evaluation case with ground-truth evidence."""
    id: str
    category: QueryCategory
    query: str
    gold_atom_ids: list[str] = Field(default_factory=list)
    acceptable_alternative_ids: list[str] = Field(default_factory=list)
    expected_abstention: bool = False
    difficulty: str = "medium"
    metadata: dict = Field(default_factory=dict)


class BenchmarkManifest(BaseModel):
    """Manifest representing a frozen benchmark corpus and query judgment set."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    corpus_sha256: str
    corpus_text: str | None = None
    corpus_file: str | None = None
    release_authority: bool = False
    cases: list[BenchmarkCase] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
