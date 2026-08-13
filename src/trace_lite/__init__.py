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

"""trace-lite: source-first headless database with fail-closed retrieval."""

from trace_lite.db import (
    TraceLite,
    IngestionResult,
    ConsolidationResult,
    DatabaseStatus,
    IndexBuildResult,
    IndexValidationError,
)
from trace_lite.spine import Atom, SourceArtifact, SpineEvent
from trace_lite.cortex import Tree, TreeNode
from trace_lite.engines import QueryResult, EvidenceItem
from trace_lite.providers import (
    POPULAR_PROVIDERS,
    apply_saved_config,
    auto_load_models_enabled,
    save_provider_key,
    set_auto_load_models,
)
from trace_lite.diagnostics import BuildDiagnostic
from trace_lite.adapters import NormalizedCompletionEnvelope

__version__ = "0.1.0"

__all__ = [
    "TraceLite",
    "IngestionResult",
    "ConsolidationResult",
    "DatabaseStatus",
    "IndexBuildResult",
    "IndexValidationError",
    "QueryResult",
    "EvidenceItem",
    "Atom",
    "SourceArtifact",
    "SpineEvent",
    "Tree",
    "TreeNode",
    "POPULAR_PROVIDERS",
    "save_provider_key",
    "apply_saved_config",
    "auto_load_models_enabled",
    "set_auto_load_models",
    "BuildDiagnostic",
    "NormalizedCompletionEnvelope",
]
