"""Dataclass schemas for Spine storage entities."""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib


@dataclass(frozen=True)
class Atom:
    """Smallest meaningful semantic unit. Immutable once created."""

    atom_id: str
    content: str
    content_hash: str
    source_artifact_id: str
    sequence_index: int
    char_offset_start: int
    char_offset_end: int
    created_at: str  # ISO 8601 string
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        atom_id: str,
        content: str,
        source_artifact_id: str,
        sequence_index: int,
        char_offset_start: int,
        char_offset_end: int,
        created_at: str | None = None,
        metadata: dict | None = None,
    ) -> "Atom":
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        created = created_at or datetime.now(timezone.utc).isoformat()
        return cls(
            atom_id=atom_id,
            content=content,
            content_hash=content_hash,
            source_artifact_id=source_artifact_id,
            sequence_index=sequence_index,
            char_offset_start=char_offset_start,
            char_offset_end=char_offset_end,
            created_at=created,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SourceArtifact:
    """Provenance record for an ingested document or text stream."""

    artifact_id: str
    document_name: str | None
    source_uri: str | None
    content_hash: str
    created_at: str  # ISO 8601 string
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        artifact_id: str,
        content: str,
        document_name: str | None = None,
        source_uri: str | None = None,
        created_at: str | None = None,
        metadata: dict | None = None,
    ) -> "SourceArtifact":
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        created = created_at or datetime.now(timezone.utc).isoformat()
        return cls(
            artifact_id=artifact_id,
            document_name=document_name,
            source_uri=source_uri,
            content_hash=content_hash,
            created_at=created,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SpineEvent:
    """Immutable event in the append-only ledger."""

    event_id: str
    event_type: str
    timestamp: str  # ISO 8601 string
    payload: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_id: str,
        event_type: str,
        payload: dict,
        timestamp: str | None = None,
    ) -> "SpineEvent":
        created = timestamp or datetime.now(timezone.utc).isoformat()
        return cls(
            event_id=event_id,
            event_type=event_type,
            timestamp=created,
            payload=payload,
        )

    def to_dict(self) -> dict:
        return asdict(self)
