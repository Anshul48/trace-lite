"""Versioned derived-index build manifests.

Spine is append-only source state.  A manifest makes the two rebuildable
projections (Cortex and vectors) observable and gives activation a durable
state machine instead of relying on the presence of a database file.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


BuildState = Literal["building", "validated", "active", "failed", "retained"]


@dataclass
class IndexBuildManifest:
    build_id: str
    state: BuildState = "building"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_atom_count: int = 0
    tree_count: int = 0
    node_count: int = 0
    leaf_count: int = 0
    vector_count: int = 0
    embedding_model: str = ""
    embedding_dimension: int = 0
    config_fingerprint: str = ""
    vector_collection: str | None = None
    valid_summary_count: int = 0
    # These legacy counters are retained for inspection. Any non-zero
    # fallback/passthrough count makes an active build untrusted.
    fallback_summary_count: int = 0
    retry_summary_count: int = 0
    passthrough_summary_count: int = 0
    summary_quality: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IndexBuildManifest":
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: value[key] for key in allowed if key in value})

    @property
    def is_active(self) -> bool:
        return self.state == "active"
