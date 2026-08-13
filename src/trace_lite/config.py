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

"""Configuration parameters for trace-lite.

The configuration object is intentionally small and serialisable.  Anything
that affects a derived index is included in :meth:`TraceLiteConfig.index_dict`
so an index build can be reproduced (or diagnosed) without storing provider
credentials in the workspace.
"""

from dataclasses import dataclass
from dataclasses import asdict
from typing import Any
from trace_lite.providers import apply_saved_config



@dataclass
class TraceLiteConfig:
    """All tunable parameters for trace-lite."""

    # Paths
    spine_db_name: str = "spine.sqlite3"
    cortex_db_name: str = "cortex.sqlite3"
    vector_db_name: str = "vectors.lance"

    # Embeddings
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dimension: int = 384

    # LLM
    llm_model: str = "ollama/llama3.1:8b"
    llm_api_base: str | None = None
    llm_max_tokens_summary: int = 300
    llm_max_tokens_traversal: int = 200

    # Atomizer
    min_atom_length: int = 50
    max_atom_length: int = 2000

    # Clustering (RAPTOR)
    umap_n_components: int = 5
    hdbscan_min_cluster_size: int = 3
    raptor_max_depth: int = 5
    raptor_max_children_per_summary: int = 8
    raptor_summary_min_length: int = 24
    # One initial generation plus three corrective retries.
    raptor_summary_retry_count: int = 3
    router_title_retry_count: int = 3

    # Projection/visualisation limits.  These are deliberately bounded so a
    # large workspace cannot make the API attempt to materialise every vector.
    projection_max_points: int = 2000
    projection_components: int = 3

    # Friendly aliases accepted by integrations that predate the expanded
    # names above.  Canonical values are normalised in __post_init__.
    raptor_max_children: int | None = None
    summary_min_length: int | None = None
    summary_retry_count: int | None = None
    projection_max_samples: int | None = None

    # Router (Forest)
    router_placement_threshold: float = 0.65
    router_max_trees_per_atom: int = 3

    # LATTICE (Traversal)
    lattice_branch_factor: int = 3
    lattice_max_depth: int = 5

    # Energy
    energy_decay_exponent: float = 0.5
    energy_retrieval_threshold: float = 0.1

    def __post_init__(self) -> None:
        """Validate settings that can otherwise produce an invalid tree."""
        if self.raptor_max_children is not None:
            self.raptor_max_children_per_summary = self.raptor_max_children
        if self.summary_min_length is not None:
            self.raptor_summary_min_length = self.summary_min_length
        if self.summary_retry_count is not None:
            self.raptor_summary_retry_count = self.summary_retry_count
        if self.projection_max_samples is not None:
            self.projection_max_points = self.projection_max_samples
        integer_limits = {
            "embedding_dimension": self.embedding_dimension,
            "umap_n_components": self.umap_n_components,
            "hdbscan_min_cluster_size": self.hdbscan_min_cluster_size,
            "raptor_max_depth": self.raptor_max_depth,
            "raptor_max_children_per_summary": self.raptor_max_children_per_summary,
            "raptor_summary_min_length": self.raptor_summary_min_length,
            "raptor_summary_retry_count": self.raptor_summary_retry_count,
            "router_title_retry_count": self.router_title_retry_count,
            "projection_max_points": self.projection_max_points,
            "projection_components": self.projection_components,
            "lattice_branch_factor": self.lattice_branch_factor,
            "lattice_max_depth": self.lattice_max_depth,
        }
        for name, value in integer_limits.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.raptor_summary_retry_count > 10:
            raise ValueError("raptor_summary_retry_count must be <= 10")
        if self.router_title_retry_count > 10:
            raise ValueError("router_title_retry_count must be <= 10")
        if self.raptor_max_children_per_summary < 2:
            raise ValueError("raptor_max_children_per_summary must be at least 2")
        if self.projection_components > 3:
            raise ValueError("projection_components must be <= 3")
        if self.min_atom_length < 1 or self.max_atom_length < self.min_atom_length:
            raise ValueError("atom length limits are invalid")
        if self.router_max_trees_per_atom < 1 or self.lattice_branch_factor < 1:
            raise ValueError("router/lattice limits must be positive")

    def index_dict(self) -> dict[str, Any]:
        """Return only settings that influence derived index contents."""
        values = asdict(self)
        # Provider credentials are never part of an index fingerprint.
        return values

    @classmethod
    def default(cls) -> "TraceLiteConfig":
        saved_model, saved_api_base = apply_saved_config()
        model = saved_model or "ollama/llama3.1:8b"
        return cls(llm_model=model, llm_api_base=saved_api_base)
