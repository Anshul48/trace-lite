"""Cordis runtime plugin: memory persistence + faceted retrieval + scoped ledger."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..filing.engine import FilingEngine
from ..filing.taxonomy import Taxonomy
from ..router.cascade import CascadeRouter
from ..store.database import Database
from .models import (
    AtomRecord,
    EvidenceAnchor,
    FacetedQuery,
    QueryResponse,
    TraceEvent,
)
from .tms import ModularContractBoundaryTMS


class TraceLiteLedgerService:
    """Scoped commitment ledger backed by the TMS, audited into the event stream."""

    def __init__(self, tms: ModularContractBoundaryTMS | None = None) -> None:
        self.tms = tms if tms is not None else ModularContractBoundaryTMS()

    def __getattr__(self, name: str) -> Any:  # delegate TMS surface (register/evaluate/...)
        return getattr(self.__dict__["tms"], name)


class TraceLiteMemoryPlugin:
    """Async-shaped persistence contract over the single-node filing cabinet."""

    def __init__(self) -> None:
        self.db: Database | None = None
        self.router: CascadeRouter | None = None
        self.ledger = TraceLiteLedgerService()
        self._epoch = 0
        self._leases: dict[str, dict[str, Any]] = {}

    def on_init(self, config: dict[str, Any]) -> bool:
        path = Path(config.get("db_path", "~/.trace-lite/storage.db")).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(path)
        taxonomy = Taxonomy(self.db.conn)
        engine = FilingEngine(self.db.conn, taxonomy)
        self.router = CascadeRouter(self.db.conn, engine)
        self.router.warm()
        mode = self.db.conn.execute("PRAGMA journal_mode").fetchone()[0]
        return mode.lower() == "wal"

    def _require(self) -> Database:
        if self.db is None or self.router is None:
            raise RuntimeError("plugin not initialised: call on_init first")
        return self.db

    def on_record_event(self, event: TraceEvent) -> str:
        db = self._require()
        return db.insert_event(
            event.stream_id, event.event_type, dict(event.payload),
        )

    def ingest_atom(self, record: AtomRecord) -> int:
        db = self._require()
        return db.insert_atom(
            doc_id=record.doc_id, text=record.text,
            start_byte=record.start_byte, end_byte=record.end_byte or None,
        )

    def query_context(self, query: FacetedQuery) -> QueryResponse:
        db = self._require()
        assert self.router is not None
        result = self.router.route(query.query, limit=query.candidate_limit)
        anchors = [
            EvidenceAnchor(
                atom_version_id=str(a["id"]),
                doc_id=a.get("doc_id", ""),
                start_byte=int(a.get("start_byte", 0)),
                end_byte=int(a.get("end_byte", 0)),
                content_hash="",
                snippet_text=str(a.get("text", ""))[:500],
            )
            for a in result.anchors
        ]
        return QueryResponse(
            query=query.query, anchors=anchors, tier_used=result.tier_used,
            elapsed_ms=result.elapsed_ms,
            sufficiency_state="answerable" if result.verdict == "answerable"
            else "insufficient_evidence",
        )

    def acquire_rcu_lease(self, pid: int) -> dict[str, Any]:
        self._epoch += 1
        lease_id = uuid4().hex
        receipt = {"pid": pid, "lease_id": lease_id, "epoch": self._epoch,
                   "acquired_at": time.time(), "expires_at": time.time() + 30.0}
        self._leases[lease_id] = receipt
        return receipt

    def release_rcu_lease(self, lease_id: str) -> bool:
        return self._leases.pop(lease_id, None) is not None

    def flush(self, timeout_ms: int | None = None) -> dict[str, Any]:
        db = self._require()
        record = db.checkpoint_now()
        return {"checkpointed_frames": record.checkpointed_frames,
                "total_committed": record.total_committed,
                "timeout_ms": timeout_ms}

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
            self.router = None
