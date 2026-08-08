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

"""Configuration parameters for trace-lite."""

from dataclasses import dataclass
from pathlib import Path
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

    # Router (Forest)
    router_placement_threshold: float = 0.65
    router_max_trees_per_atom: int = 3

    # LATTICE (Traversal)
    lattice_branch_factor: int = 3
    lattice_max_depth: int = 5

    # Energy
    energy_decay_exponent: float = 0.5
    energy_retrieval_threshold: float = 0.1

    @classmethod
    def default(cls) -> "TraceLiteConfig":
        saved_model, saved_api_base = apply_saved_config()
        model = saved_model or "ollama/llama3.1:8b"
        return cls(llm_model=model, llm_api_base=saved_api_base)

