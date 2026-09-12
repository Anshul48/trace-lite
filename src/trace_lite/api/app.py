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
    query: str | None = None
    query_text: str | None = None
    limit: int | None = None
    top_k: int | None = None
    mode: str = "hybrid"


class SyncRequest(BaseModel):
    path: str | None = None


class IngestRequest(BaseModel):
    text: str
    document_name: str


class State:
    db: Database | None = None
    router: CascadeRouter | None = None
    taxonomy: Taxonomy | None = None
    engine: FilingEngine | None = None
    vault: Path | None = None
    watcher: Any = None
    started_at: float = 0.0
    rewarm_timer: threading.Timer | None = None
    rewarm_delay: float = 0.5

    def schedule_rewarm(self, guard: threading.Lock) -> None:
        if self.rewarm_timer is not None:
            self.rewarm_timer.cancel()
            self.rewarm_timer = None

        def _do_rewarm():
            with guard:
                if self.router is not None and self.db is not None:
                    try:
                        self.router.warm()
                    except Exception:
                        pass
                self.rewarm_timer = None

        self.rewarm_timer = threading.Timer(self.rewarm_delay, _do_rewarm)
        self.rewarm_timer.daemon = True
        self.rewarm_timer.start()

    def cancel_rewarm(self) -> None:
        if self.rewarm_timer is not None:
            self.rewarm_timer.cancel()
            self.rewarm_timer = None


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


def ingest_note(db: Database, taxonomy: Taxonomy, engine: FilingEngine,
                document_name: str, raw: str, event_type: str = "vault.synced") -> int:
    """Idempotent single-note upsert: parse, replace atoms, facet-map. Returns atom id."""
    note = parse_note(document_name, raw)
    old_aids = [
        aid for (aid,) in db.conn.execute(
            "SELECT id FROM atom WHERE doc_id = ?", (document_name,)
        ).fetchall()
    ]
    old_fids: set[str] = set()
    for aid in old_aids:
        for fid, _ in engine.facets_of_atom(aid):
            old_fids.add(fid)
    # Replace prior atoms for this note to keep sync idempotent.
    for aid in old_aids:
        db.delete_atom(aid)
    body = note.body.strip() or raw.strip()
    atom_id = db.insert_atom(doc_id=document_name, text=body)
    db.insert_event(document_name, event_type,
                    {"path": document_name, "tags": note.tags})
    roots: dict[str, str] = {}
    facet_ids = []
    for dimension, names in facet_hints(note).items():
        for name in names:
            facet_ids.append(_ensure_facet(taxonomy, dimension, name, roots))
    if facet_ids:
        engine.assign_facets(atom_id, facet_ids)
    affected_fids = old_fids | set(facet_ids)
    for fid in affected_fids:
        engine.refresh_centroid(fid)
    return atom_id


def sync_notes(db: Database, taxonomy: Taxonomy, engine: FilingEngine,
               vault: Path, paths: list[Path]) -> int:
    """Sync an explicit list of vault files (used by the background watcher)."""
    synced = 0
    for path in paths:
        if not path.is_file() or path.suffix != ".md":
            continue
        try:
            rel = path.relative_to(vault).as_posix()
        except ValueError:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        ingest_note(db, taxonomy, engine, rel, raw)
        synced += 1
    return synced


def sync_vault(db: Database, taxonomy: Taxonomy, engine: FilingEngine,
               vault: Path, relpath: str | None = None) -> int:
    """Atomize vault notes, map tags/folders to facets. Returns notes synced."""
    targets = [vault / relpath] if relpath else sorted(vault.rglob("*.md"))
    return sync_notes(db, taxonomy, engine, vault, targets)


def create_app(db_path: str | Path, vault: str | Path | None = None) -> FastAPI:
    state = State()
    # Handlers run on worker threads; serialize SQLite access on one connection.
    guard = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from ..obsidian.watcher import DebouncedWatcher

        state.db = Database(db_path, check_same_thread=False)
        state.taxonomy = Taxonomy(state.db.conn)
        state.engine = FilingEngine(state.db.conn, state.taxonomy)
        state.engine.bind_database(state.db)
        state.router = CascadeRouter(state.db.conn, state.engine)
        state.router.warm()
        state.vault = Path(vault) if vault else None
        state.started_at = time.time()

        def _on_watcher_sync(paths: list[Path]) -> None:
            with guard:
                assert state.db is not None and state.taxonomy is not None
                assert state.engine is not None and state.vault is not None
                sync_notes(state.db, state.taxonomy, state.engine, state.vault, paths)
                if state.router is not None:
                    state.router.warm()

        def _on_watcher_remove(paths: list[Path]) -> None:
            with guard:
                assert state.db is not None and state.vault is not None
                for path in paths:
                    try:
                        rel = path.relative_to(state.vault).as_posix()
                    except ValueError:
                        continue
                    if state.db.delete_doc(rel):
                        state.db.insert_event(rel, "note.removed", {"path": rel})
                if state.engine is not None:
                    state.engine.clear_stale_centroids()
                if state.router is not None:
                    state.router.warm()

        if state.vault is not None and state.vault.is_dir():
            state.watcher = DebouncedWatcher(
                state.vault, on_sync=_on_watcher_sync, on_remove=_on_watcher_remove
            )
            state.watcher.start()
        yield
        with guard:
            state.cancel_rewarm()
            if state.watcher is not None:
                state.watcher.stop()
                state.watcher = None
            if state.db is not None:
                state.db.close()
                state.db = None
                state.router = None

    app = FastAPI(title="trace-lite", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        assert state.db is not None
        with guard:
            facets = state.db.conn.execute("SELECT COUNT(*) FROM facets").fetchone()[0]
            trees = state.db.conn.execute(
                "SELECT COUNT(DISTINCT dimension) FROM facets").fetchone()[0]
            atoms = state.db.count_atoms()
        return {
            "ok": True,
            "status": "connected",
            "atoms": atoms,
            "total_atoms": atoms,
            "facets": int(facets),
            "total_trees": int(trees),
            "index_trusted": True,
            "needs_organization": False,
            "pending_atoms": 0,
            "vault": str(state.vault) if state.vault else None,
            "uptime_s": round(time.time() - state.started_at, 3),
        }

    @app.post("/api/query")
    def query(req: QueryRequest) -> dict[str, Any]:
        assert state.router is not None
        text = req.query if req.query is not None else req.query_text
        if not text:
            return {"query": "", "query_text": "", "mode": req.mode, "total_results": 0,
                    "anchors": [], "items": [], "tier_used": 3, "elapsed_ms": 0.0,
                    "sufficiency_state": "insufficient_evidence"}
        limit = req.limit if req.limit is not None else req.top_k if req.top_k is not None else 10
        with guard:
            result = state.router.route(text, limit=limit, mode=req.mode)
        anchors = [
            {"doc_id": a.get("doc_id", ""), "snippet": str(a.get("text", ""))[:300],
             "score": a.get("score", 0.0)}
            for a in result.anchors
        ]
        items = [
            {"atom_id": str(a.get("id", "")), "score": a.get("score", 0.0),
             "source_artifact": {"artifact_id": a.get("doc_id", ""),
                                 "document_name": a.get("doc_id", "")},
             "atom": {"atom_id": str(a.get("id", "")),
                      "content": str(a.get("text", ""))[:1500]}}
            for a in result.anchors
        ]
        return {
            "query": text,
            "query_text": text,
            "mode": req.mode,
            "total_results": len(items),
            "anchors": anchors,
            "items": items,
            "tier_used": result.tier_used,
            "elapsed_ms": result.elapsed_ms,
            "sufficiency_state": "answerable" if result.verdict == "answerable"
            else "insufficient_evidence",
        }

    @app.post("/api/ingest")
    def ingest(req: IngestRequest) -> dict[str, Any]:
        """Remote push path used by the Obsidian plugin (no vault filesystem needed)."""
        assert state.db is not None and state.taxonomy is not None and state.engine is not None

        with guard:
            assert state.db is not None and state.taxonomy is not None and state.engine is not None
            old_aids = [
                r[0]
                for r in state.db.conn.execute(
                    "SELECT id FROM atom WHERE doc_id = ?", (req.document_name,)
                ).fetchall()
            ]
            atom_id = ingest_note(state.db, state.taxonomy, state.engine,
                                  req.document_name, req.text, event_type="note.ingested")
            if state.router is not None:
                for aid in old_aids:
                    state.router.remove_atom(aid)
                note = parse_note(req.document_name, req.text)
                body = note.body.strip() or req.text.strip()
                state.router.add_atom(atom_id, body)
                state.schedule_rewarm(guard)
            atoms = state.db.count_atoms()
        return {"ok": True, "document_name": req.document_name, "doc_id": req.document_name,
                "atom_id": atom_id, "atoms": atoms, "total_atoms": atoms}

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
