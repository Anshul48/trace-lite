"""FastAPI application server for trace-lite web visualizer."""

from pathlib import Path
from typing import Any, Dict

from trace_lite.db import TraceLite
from trace_lite.visualizer.serializers import (
    serialize_status,
    serialize_forest,
    serialize_vectors,
    serialize_query_result,
    serialize_spine,
    serialize_dag,
)


def create_app(data_dir: str | Path) -> Any:
    """Create FastAPI app for trace-lite web dashboard."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import HTMLResponse, JSONResponse
        from pydantic import BaseModel
    except ImportError as e:
        raise ImportError(
            "\n\n"
            "┌─────────────────────────────────────────────────────────────┐\n"
            "│ Web Visualizer requires optional UI dependencies.           │\n"
            "│ Please install using:                                       │\n"
            "│   pip install \"trace-lite[ui]\"                              │\n"
            "└─────────────────────────────────────────────────────────────┘\n"
        ) from e

    app = FastAPI(title="trace-lite Visualizer", version="0.1.0")

    # Add CORS middleware for Obsidian app & localhost
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["app://obsidian.md", "http://localhost"],
        allow_origin_regex=r"^http://localhost(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    db = TraceLite(data_dir)

    class QueryRequest(BaseModel):
        query_text: str
        top_k: int = 5
        mode: str = "hybrid"

    class IngestRequest(BaseModel):
        text: str | None = None
        file_path: str | None = None
        document_name: str | None = None

    class VaultSyncRequest(BaseModel):
        vault_path: str
        auto_consolidate: bool = True

    @app.get("/api/status")
    def get_status() -> Dict[str, Any]:
        return serialize_status(db)

    @app.get("/api/trees")
    def get_trees() -> Dict[str, Any]:
        return serialize_forest(db)

    @app.get("/api/vectors")
    def get_vectors() -> Dict[str, Any]:
        return serialize_vectors(db)

    @app.get("/api/spine")
    def get_spine() -> Dict[str, Any]:
        return serialize_spine(db)

    @app.post("/api/query")
    def run_query(req: QueryRequest) -> Dict[str, Any]:
        if not req.query_text.strip():
            raise HTTPException(status_code=400, detail="Query text cannot be empty.")
        res = db.query(query_text=req.query_text, top_k=req.top_k, mode=req.mode) # type: ignore
        return serialize_query_result(res)

    @app.post("/api/ingest")
    def run_ingest(req: IngestRequest) -> Dict[str, Any]:
        if req.file_path:
            res = db.ingest_file(req.file_path, document_name=req.document_name)
        elif req.text is not None:
            res = db.ingest(req.text, document_name=req.document_name)
        else:
            raise HTTPException(status_code=400, detail="Must provide either text or file_path.")
        return {"status": "ok", "atom_count": res.atom_count, "artifact_id": res.artifact_id, "tree_ids": res.tree_ids}

    # Extended REST API v1 endpoints
    @app.post("/api/v1/vault/sync")
    def sync_vault_endpoint(req: VaultSyncRequest) -> Dict[str, Any]:
        from trace_lite.watcher import sync_vault
        p = Path(req.vault_path).resolve()
        if not p.exists() or not p.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"Vault path does not exist or is not a directory: {req.vault_path}",
            )
        try:
            return sync_vault(vault_path=p, db=db, auto_consolidate=req.auto_consolidate)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/v1/trees/{tree_id}/dag")
    def get_tree_dag(tree_id: str) -> Dict[str, Any]:
        tree = db.forest.get_tree(tree_id)
        if not tree:
            raise HTTPException(status_code=404, detail=f"Tree '{tree_id}' not found.")
        return serialize_dag(db, tree_id)

    # Static assets serving
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    index_file = static_dir / "index.html"
    if not index_file.exists():
        index_file.write_text("<h1>trace-lite UI static bundle missing</h1>", encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def serve_dashboard():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app
