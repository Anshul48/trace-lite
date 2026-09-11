"""Loopback REST daemon (default :8420) consumed by the Obsidian companion plugin."""

from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from ..filing.engine import FilingEngine
from ..filing.taxonomy import Taxonomy
from ..obsidian.parser import facet_hints, parse_note
from ..router.cascade import CascadeRouter
from ..store.database import Database

DEFAULT_PORT = int(os.environ.get("TRACE_LITE_PORT", "8420"))


class QueryRequest(BaseModel):
    query: str
    limit: int = 10


class SyncRequest(BaseModel):
    path: str | None = None


class State:
    db: Database | None = None
    router: CascadeRouter | None = None
    taxonomy: Taxonomy | None = None
    engine: FilingEngine | None = None
    vault: Path | None = None
    started_at: float = 0.0


def _ensure_facet(taxonomy: Taxonomy, dimension: str, name: str,
                  roots: dict[str, str]) -> str:
    root_id = roots.get(dimension)
    if root_id is None:
        root_id = taxonomy.create_facet(dimension, dimension)
        roots[dimension] = root_id
    for child in taxonomy.children(root_id):
        if child.name == name:
            return child.facet_id
    return taxonomy.create_facet(dimension, name, parent_id=root_id)


def sync_vault(db: Database, taxonomy: Taxonomy, engine: FilingEngine,
               vault: Path, relpath: str | None = None) -> int:
    """Atomize vault notes, map tags/folders to facets. Returns notes synced."""
    targets = [vault / relpath] if relpath else sorted(vault.rglob("*.md"))
    roots: dict[str, str] = {}
    synced = 0
    for path in targets:
        if not path.is_file() or path.suffix != ".md":
            continue
        rel = path.relative_to(vault).as_posix()
        raw = path.read_text(encoding="utf-8")
        note = parse_note(rel, raw)
        # Replace prior atoms for this note to keep sync idempotent.
        for (aid,) in db.conn.execute("SELECT id FROM atom WHERE doc_id = ?", (rel,)).fetchall():
            db.delete_atom(aid)
        body = note.body.strip() or raw.strip()
        atom_id = db.insert_atom(doc_id=rel, text=body)
        db.insert_event(rel, "vault.synced", {"path": rel, "tags": note.tags})
        facet_ids = []
        for dimension, names in facet_hints(note).items():
            for name in names:
                facet_ids.append(_ensure_facet(taxonomy, dimension, name, roots))
        if facet_ids:
            engine.assign_facets(atom_id, facet_ids)
            for fid in set(facet_ids):
                engine.refresh_centroid(fid)
        synced += 1
    return synced


def create_app(db_path: str | Path, vault: str | Path | None = None) -> FastAPI:
    state = State()
    # Handlers run on worker threads; serialize SQLite access on one connection.
    guard = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.db = Database(db_path, check_same_thread=False)
        state.taxonomy = Taxonomy(state.db.conn)
        state.engine = FilingEngine(state.db.conn, state.taxonomy)
        state.router = CascadeRouter(state.db.conn, state.engine)
        state.router.warm()
        state.vault = Path(vault) if vault else None
        state.started_at = time.time()
        yield
        if state.db is not None:
            state.db.close()

    app = FastAPI(title="trace-lite", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        assert state.db is not None
        with guard:
            facets = state.db.conn.execute("SELECT COUNT(*) FROM facets").fetchone()[0]
            atoms = state.db.count_atoms()
        return {
            "ok": True,
            "atoms": atoms,
            "facets": int(facets),
            "vault": str(state.vault) if state.vault else None,
            "uptime_s": round(time.time() - state.started_at, 3),
        }

    @app.post("/api/query")
    def query(req: QueryRequest) -> dict[str, Any]:
        assert state.router is not None
        with guard:
            result = state.router.route(req.query, limit=req.limit)
        return {
            "query": req.query,
            "anchors": [
                {"doc_id": a.get("doc_id", ""), "snippet": str(a.get("text", ""))[:300],
                 "score": a.get("score", 0.0)}
                for a in result.anchors
            ],
            "tier_used": result.tier_used,
            "elapsed_ms": result.elapsed_ms,
            "sufficiency_state": "answerable" if result.verdict == "answerable"
            else "insufficient_evidence",
        }

    @app.post("/api/sync")
    def sync(req: SyncRequest) -> dict[str, Any]:
        assert state.db is not None and state.taxonomy is not None and state.engine is not None
        if state.vault is None and req.path is None:
            return {"synced": 0, "error": "no vault configured"}
        vault = Path(req.path) if req.path else state.vault
        assert vault is not None
        with guard:
            synced = sync_vault(state.db, state.taxonomy, state.engine, vault)
            if state.router is not None:
                state.router.warm()
            atoms = state.db.count_atoms()
        return {"synced": synced, "atoms": atoms}

    return app
