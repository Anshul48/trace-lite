"""FastAPI application server for the trace-lite workspace."""

import ipaddress
import json
import logging
import re
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, Literal

from trace_lite.adapters.llm import (
    EmptyResponseError,
    EndpointUnavailableError,
    LLMPreflightError,
    OllamaAvailabilityError,
    OllamaModelNotFoundError,
    ProviderResponseError,
    RejectedCredentialError,
    TemporaryProviderError,
    UnsupportedModelError,
    redact_diagnostic,
)
from trace_lite.db import (
    IndexValidationError,
    QueryBlockedError,
    TraceLite,
)
from trace_lite.engines.raptor import SummaryGenerationError
from trace_lite.engines.router import TreeNamingError
from trace_lite.cortex import VectorStoreError
from trace_lite.projects import (
    NO_ACTIVE_PROJECT_MESSAGE,
    ProjectDeletionError,
    ProjectManager,
)
from trace_lite.providers import (
    POPULAR_PROVIDERS,
    load_config_data,
    save_provider_key,
    auto_load_models_enabled,
    set_auto_load_models,
    verify_provider_connection,
    ProviderVerificationError,
    CredentialStorageError,
    ProviderConfigurationError,
)
from trace_lite.visualizer.serializers import (
    serialize_dag,
    serialize_forest,
    serialize_query_result,
    serialize_spine,
    serialize_status,
    serialize_vectors,
    serialize_workspace,
)


logger = logging.getLogger("trace_lite.ui")


def _is_loopback_host(host: str) -> bool:
    """Return whether a server bind is local-only."""
    normalized = (host or "127.0.0.1").strip().lower()
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _safe_config_payload(config: dict[str, Any]) -> dict[str, Any]:
    """Redact provider secrets before a config object crosses the API boundary."""
    safe_providers: dict[str, dict[str, Any]] = {}
    for provider_id, info in (config.get("providers", {}) or {}).items():
        if not isinstance(info, dict):
            continue
        safe_providers[provider_id] = {
            "provider_id": info.get("provider_id", provider_id),
            "name": info.get("name", provider_id),
            "model": info.get("model"),
            "api_base": info.get("api_base"),
            "api_version": info.get("api_version"),
            "env_var": info.get("env_var"),
            "has_api_key": bool(info.get("has_api_key")),
            "credential_state": info.get("credential_state", "missing"),
        }
    return {
        "active_provider": config.get("active_provider"),
        "active_model": config.get("active_model"),
        "api_base": config.get("api_base"),
        "api_version": config.get("api_version"),
        "auto_load_models": config.get("auto_load_models") is not False,
        "providers": safe_providers,
    }


def create_app(
    data_dir: str | Path | None,
    host: str = "127.0.0.1",
    data_dir_locked: bool = False,
    db: TraceLite | None = None,
    start_model_warmup: bool = False,
) -> Any:
    """Create the trace-lite web app.

    ``data_dir_locked`` is used by ``tl ui --data-dir``.  It keeps an isolated
    workspace from being silently changed by the global project selector.
    Server-path and folder controls are only available for loopback servers.
    """
    if not _is_loopback_host(host):
        raise ValueError("The trace-lite workspace must bind to a loopback address.")
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel, Field
    except ImportError as e:
        raise ImportError(
            "Web Visualizer requires optional UI dependencies. "
            'Install with: pip install "trace-lite[ui]"'
        ) from e

    app = FastAPI(title="trace-lite Visualizer", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["app://obsidian.md", "http://localhost"],
        allow_origin_regex=r"^http://localhost(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    pm = ProjectManager()
    initial_dir = Path(data_dir).resolve() if data_dir is not None else None
    db_container: dict[str, Any] = {
        "data_dir": initial_dir,
        "db": db or (TraceLite(initial_dir) if initial_dir is not None else None),
    }
    active_watchers: dict[str, Any] = {}
    # Invariant: organizer failures expose actionable stage/error diagnostics;
    # they must never be collapsed into an unexplained 409.  Terminal logging
    # remains operationally complete while sensitive payloads are redacted.
    # All server-side derived builds share one lock.  The lock is intentionally
    # app-scoped: a second organize request waits for the first and then calls
    # TraceLite.organize again, whose state check makes that call idempotent.
    build_lock = threading.RLock()
    allow_server_paths = _is_loopback_host(host)

    def get_db() -> TraceLite:
        selected_db = db_container.get("db")
        if selected_db is None:
            raise HTTPException(status_code=409, detail=NO_ACTIVE_PROJECT_MESSAGE)
        return selected_db

    def _model_status_without_db() -> dict[str, Any]:
        enabled = auto_load_models_enabled()
        return {
            "auto_load_models": enabled,
            "enabled": enabled,
            "model": None,
            "embedding_model": None,
            "status": "not_loaded",
            "loaded": False,
            "loading": False,
            "error": None,
            "embedding": {
                "model": None,
                "status": "not_loaded",
                "loaded": False,
                "error": None,
            },
            "llm": {"status": "not_requested", "contacted": False},
        }

    def _server_diagnostic_sink(event: dict[str, Any]) -> None:
        """Forward safe organizer progress to the ``tl ui`` terminal."""
        payload = redact_diagnostic(json.dumps(event, ensure_ascii=False, default=str))
        logger.info("organizer event: %s", payload)

    def _log_build_failure(action: str, db: TraceLite, exc: BaseException) -> None:
        """Log a concise safe failure, retaining verbose detail only at DEBUG."""
        concise = " ".join(redact_diagnostic(str(exc)).split())[:500]
        logger.error("%s failed: %s", action, concise or type(exc).__name__)
        rendered = redact_diagnostic(traceback.format_exc())
        logger.debug("%s sanitized traceback:\n%s", action, rendered)
        snapshot = getattr(getattr(db, "llm", None), "debug_snapshot", None)
        if callable(snapshot):
            try:
                logger.debug(
                    "%s provider diagnostics:\n%s",
                    action,
                    redact_diagnostic(snapshot(exc)),
                )
            except Exception as diagnostic_error:
                logger.debug(
                    "%s provider diagnostics unavailable: %s",
                    action,
                    redact_diagnostic(str(diagnostic_error)),
                )

    def _build_failure_response(action: str, db: TraceLite, exc: BaseException) -> Any:
        """Map build failures to stable status/code/stage fields, never 409."""
        stage = "build"
        error_code = "build_failed"
        status_code = 500
        if isinstance(exc, OllamaModelNotFoundError):
            status_code, stage, error_code = 422, "preflight", "ollama_model_not_found"
            detail = f"Ollama configuration error: {redact_diagnostic(str(exc))}"
        elif isinstance(exc, OllamaAvailabilityError):
            status_code, stage, error_code = 503, "preflight", "ollama_unavailable"
            detail = f"Ollama service unavailable: {redact_diagnostic(str(exc))}"
        elif isinstance(exc, (ProviderConfigurationError, CredentialStorageError, ProviderVerificationError)):
            status_code, stage, error_code = 422, "preflight", "provider_configuration_error"
            detail = redact_diagnostic(str(exc))
        elif isinstance(exc, (RejectedCredentialError, UnsupportedModelError)):
            status_code, stage, error_code = 422, "preflight", "provider_configuration_error"
            detail = redact_diagnostic(str(exc))
        elif isinstance(exc, (EndpointUnavailableError, TemporaryProviderError, EmptyResponseError)):
            status_code, stage, error_code = 503, "preflight", "provider_unavailable"
            detail = redact_diagnostic(str(exc))
        elif isinstance(exc, (SummaryGenerationError, TreeNamingError)):
            status_code, stage = 422, (
                "summary_generation" if isinstance(exc, SummaryGenerationError) else "tree_naming"
            )
            error_code = getattr(exc, "failure_code", "staged_build_failed")
            detail = redact_diagnostic(str(exc))
        elif isinstance(exc, IndexValidationError):
            status_code, stage, error_code = 422, "validation", "validation_failed"
            detail = redact_diagnostic(str(exc))
        elif isinstance(exc, ValueError):
            status_code, stage, error_code = 422, "validation", "invalid_build"
            detail = redact_diagnostic(str(exc))
        elif isinstance(exc, LLMPreflightError):
            status_code, stage, error_code = 503, "preflight", "provider_unavailable"
            detail = redact_diagnostic(str(exc))
        else:
            detail = (
                f"{action} failed during the staged build; activation was prevented and the "
                "previous active index was preserved. Check the terminal diagnostic and retry "
                "after correcting the underlying issue."
            )
        _log_build_failure(action, db, exc)
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": detail,
                "error_code": str(error_code),
                "stage": stage,
            },
        )

    def ollama_availability_http_error(exc: OllamaAvailabilityError) -> Any:
        """Translate preflight failures into stable, actionable API errors."""
        if isinstance(exc, OllamaModelNotFoundError):
            return HTTPException(
                status_code=422,
                detail=f"Ollama configuration error: {exc}",
            )
        return HTTPException(
            status_code=503,
            detail=f"Ollama service unavailable: {exc}",
        )

    def require_server_paths() -> None:
        if not allow_server_paths:
            raise HTTPException(
                status_code=403,
                detail="Server-path and folder controls require a loopback-bound UI.",
            )

    def ingestion_payload(result: Any) -> dict[str, Any]:
        status = get_db().status()
        return {
            "status": "ok",
            "artifact_id": result.artifact_id,
            "atom_count": result.atom_count,
            "tree_ids": result.tree_ids,
            "queued": True,
            "pending_atoms": status.pending_atoms,
            "pending_trees": status.pending_trees,
            "needs_organization": status.needs_organization,
            "needs_recovery": status.needs_recovery,
        }

    class QueryRequest(BaseModel):
        query_text: str = Field(min_length=1)
        top_k: int = Field(default=5, ge=1, le=100)
        mode: Literal["hybrid", "tree", "flat"] = "hybrid"
        force: bool = False

    class IngestRequest(BaseModel):
        text: str | None = None
        content: str | None = None
        file_path: str | None = None
        document_name: str | None = None

    class UploadSourceRequest(BaseModel):
        content: str
        file_name: str | None = None
        document_name: str | None = None

    class ConsolidateRequest(BaseModel):
        tree_id: str | None = None

    class VaultSyncRequest(BaseModel):
        vault_path: str
        auto_consolidate: bool = False
        organize_after_sync: bool | None = None

    class FolderSyncRequest(BaseModel):
        folder_path: str | None = None
        vault_path: str | None = None
        organize_after_sync: bool = False
        auto_consolidate: bool | None = None

    class WatchFolderRequest(BaseModel):
        folder_path: str
        poll_interval: float = Field(default=2.0, ge=0.5, le=3600)
        organize_after_sync: bool = False

    class SwitchProjectRequest(BaseModel):
        name: str

    class CreateProjectRequest(BaseModel):
        name: str
        path: str | None = None
        description: str = ""

    class SaveConfigRequest(BaseModel):
        provider_id: str
        api_key: str | None = None
        model: str | None = None
        api_base: str | None = None
        api_version: str | None = None

    class TestConfigRequest(BaseModel):
        """Retained endpoint shape for testing the already-saved runtime only."""
        pass

    class AutoLoadModelsRequest(BaseModel):
        enabled: bool

    @app.get("/api/status")
    def get_status() -> Dict[str, Any]:
        return serialize_status(get_db())

    @app.get("/api/models/status")
    def get_models_status() -> Dict[str, Any]:
        db = db_container.get("db")
        if db is None:
            return _model_status_without_db()
        return db.model_status()

    @app.post("/api/config/auto-load")
    def update_auto_load_models(req: AutoLoadModelsRequest) -> Dict[str, Any]:
        try:
            config = set_auto_load_models(req.enabled)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        db = db_container.get("db")
        # Enabling while the workspace is open starts the same non-blocking
        # operation used at ``tl ui`` startup.  Disabling never unloads a
        # resident model and only prevents future automatic starts.
        if req.enabled and db is not None:
            db.start_model_warmup()
        model_status = db.model_status() if db is not None else _model_status_without_db()
        return {
            "status": "ok",
            "enabled": config.get("auto_load_models") is not False,
            "auto_load_models": config.get("auto_load_models") is not False,
            "models": model_status,
        }

    @app.get("/api/workspace")
    @app.get("/api/overview")
    def get_workspace() -> Dict[str, Any]:
        active_name = pm.get_active_project_name()
        if active_name is None or db_container.get("db") is None:
            return {
                "project": None,
                "status": None,
                "sources": {"count": 0, "artifacts": []},
                "folders": [],
                "next_action": "create_project",
            }
        return serialize_workspace(get_db(), pm.get_project(active_name))

    @app.get("/api/trees")
    def get_trees() -> Dict[str, Any]:
        return serialize_forest(get_db())

    @app.get("/api/vectors")
    def get_vectors() -> Dict[str, Any]:
        try:
            return serialize_vectors(get_db())
        except VectorStoreError as exc:
            # An extraction failure is operationally different from an empty
            # index; clients must be able to display the warning and retry.
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/index/health")
    @app.get("/api/index-health")
    @app.get("/api/validate")
    @app.get("/api/health")
    def get_index_health() -> Dict[str, Any]:
        return get_db().validate_index()

    @app.get("/api/spine")
    def get_spine() -> Dict[str, Any]:
        return serialize_spine(get_db())

    @app.get("/api/sources")
    def get_sources() -> Dict[str, Any]:
        return serialize_spine(get_db())

    @app.get("/api/sources/folders")
    @app.get("/api/folders")
    def get_source_folders() -> Dict[str, Any]:
        return {"folders": get_db().spine.list_source_connections()}

    @app.post("/api/query")
    def run_query(req: QueryRequest) -> Dict[str, Any]:
        if not req.query_text.strip():
            raise HTTPException(status_code=400, detail="Query text cannot be empty.")
        needed_organization = get_db().status().needs_organization
        try:
            result = get_db().query(
                query_text=req.query_text.strip(), top_k=req.top_k, mode=req.mode, force=req.force
            )
        except QueryBlockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OllamaAvailabilityError as exc:
            raise ollama_availability_http_error(exc) from exc
        payload = serialize_query_result(result)
        payload["organized_before_query"] = needed_organization
        payload["needs_organization"] = get_db().status().needs_organization
        payload["forced"] = req.force
        return payload

    @app.post("/api/ingest")
    def run_ingest(req: IngestRequest) -> Dict[str, Any]:
        db = get_db()
        try:
            if req.file_path:
                require_server_paths()
                result = db.ingest_file(req.file_path, document_name=req.document_name)
            else:
                text = req.text if req.text is not None else req.content
                if text is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Provide text, content, or a file_path.",
                    )
                result = db.ingest(text, document_name=req.document_name)
            return ingestion_payload(result)
        except HTTPException:
            raise
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/sources/text")
    def ingest_source_text(req: IngestRequest) -> Dict[str, Any]:
        if req.text is None and req.content is None:
            raise HTTPException(status_code=400, detail="Text content is required.")
        result = get_db().ingest(
            req.text if req.text is not None else req.content or "",
            document_name=req.document_name,
        )
        return ingestion_payload(result)

    @app.post("/api/sources/upload")
    def upload_source(req: UploadSourceRequest) -> Dict[str, Any]:
        name = req.document_name or req.file_name or "Uploaded source"
        result = get_db().ingest(req.content, document_name=name)
        payload = ingestion_payload(result)
        payload["file_name"] = req.file_name
        return payload

    def _organize_result(result: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "trees_updated": result.trees_updated,
            "summaries_generated": result.summaries_generated,
            "pending_atoms": result.pending_atoms,
            "pending_trees": result.pending_trees,
            "orphaned_atoms": result.orphaned_atoms,
            "needs_organization": result.needs_organization,
            "needs_recovery": result.needs_recovery,
        }

    @app.post("/api/organize")
    def organize() -> Dict[str, Any]:
        db = get_db()
        try:
            with build_lock:
                # The state check inside organize() runs after a concurrent
                # request has released this lock, yielding a normal idempotent
                # zero-work result instead of a duplicate staged build.
                return _organize_result(db.organize(diagnostic_sink=_server_diagnostic_sink))
        except Exception as exc:
            return _build_failure_response("Organization", db, exc)

    @app.post("/api/consolidate")
    def run_consolidate(req: ConsolidateRequest | None = None) -> Dict[str, Any]:
        # This endpoint intentionally remains the full rebuild operation.  The
        # web workspace uses /api/organize for pending work.
        tree_id = req.tree_id if req else None
        db = get_db()
        try:
            with build_lock:
                return _organize_result(
                    db.consolidate(tree_id=tree_id, diagnostic_sink=_server_diagnostic_sink)
                )
        except Exception as exc:
            return _build_failure_response("Rebuild", db, exc)

    @app.post("/api/reindex")
    def reindex_all() -> Dict[str, Any]:
        db = get_db()
        try:
            with build_lock:
                result = db.reindex_all(diagnostic_sink=_server_diagnostic_sink)
                return {"status": "ok", **result.to_dict()}
        except Exception as exc:
            return _build_failure_response("Reindex", db, exc)

    def _sync_folder(path_value: str, organize_after_sync: bool) -> dict[str, Any]:
        require_server_paths()
        from trace_lite.watcher import sync_vault

        path = Path(path_value).resolve()
        if not path.exists() or not path.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"Folder path does not exist or is not a directory: {path_value}",
            )
        try:
            def run_sync() -> dict[str, Any]:
                return sync_vault(
                    vault_path=path,
                    db=get_db(),
                    auto_consolidate=organize_after_sync,
                    diagnostic_sink=_server_diagnostic_sink if organize_after_sync else None,
                    build_lock=build_lock,
                )

            if organize_after_sync:
                with build_lock:
                    return run_sync()
            return run_sync()
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/sources/folders/sync")
    @app.post("/api/folders/sync")
    @app.post("/api/folders")
    def sync_folder(req: FolderSyncRequest) -> Dict[str, Any]:
        path_value = req.folder_path or req.vault_path
        if not path_value:
            raise HTTPException(status_code=400, detail="folder_path is required.")
        organize = req.organize_after_sync
        if req.auto_consolidate is not None:
            organize = req.auto_consolidate
        return _sync_folder(path_value, organize)

    @app.post("/api/sources/folders/watch")
    def watch_folder(req: WatchFolderRequest) -> Dict[str, Any]:
        require_server_paths()
        from trace_lite.watcher import VaultWatcher

        path = Path(req.folder_path).resolve()
        if not path.exists() or not path.is_dir():
            raise HTTPException(status_code=400, detail="Folder path is not a directory.")
        key = str(path)
        old_watcher = active_watchers.get(key)
        if old_watcher is not None:
            old_watcher.stop()
        watcher = VaultWatcher(
            path,
            db=get_db(),
            poll_interval=req.poll_interval,
            auto_consolidate=req.organize_after_sync,
            diagnostic_sink=_server_diagnostic_sink if req.organize_after_sync else None,
            build_lock=build_lock,
        )
        first_scan = watcher.scan_once()
        watcher.start()
        active_watchers[key] = watcher
        return {"status": "watching", "folder_path": key, "initial_sync": first_scan}

    @app.delete("/api/sources/folders/watch")
    def stop_folder_watch(folder_path: str = Query(...)) -> Dict[str, Any]:
        require_server_paths()
        key = str(Path(folder_path).resolve())
        watcher = active_watchers.pop(key, None)
        if watcher is None:
            raise HTTPException(status_code=404, detail="Folder watcher is not running.")
        watcher.stop()
        return {"status": "stopped", "folder_path": key}

    @app.get("/api/projects")
    def list_projects() -> Dict[str, Any]:
        return {
            "active_project": pm.get_active_project_name(),
            "projects": pm.list_projects(),
            "data_dir": str(db_container["data_dir"]) if db_container["data_dir"] else None,
            "data_dir_locked": data_dir_locked,
            "server_paths_allowed": allow_server_paths,
        }

    @app.post("/api/projects/switch")
    def switch_project(req: SwitchProjectRequest) -> Dict[str, Any]:
        if not allow_server_paths:
            raise HTTPException(status_code=403, detail="Project switching requires a loopback-bound UI.")
        if data_dir_locked:
            raise HTTPException(
                status_code=409,
                detail="This UI is locked to its explicit --data-dir workspace.",
            )
        try:
            info = pm.switch_project(req.name)
            new_path = Path(info["path"]).resolve()
            db_container["data_dir"] = new_path
            db_container["db"] = TraceLite(new_path)
            if start_model_warmup:
                db_container["db"].start_model_warmup()
            return {"status": "ok", "active_project": req.name, "path": str(new_path)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/projects/create")
    def create_project(req: CreateProjectRequest) -> Dict[str, Any]:
        if not allow_server_paths:
            raise HTTPException(status_code=403, detail="Project management requires a loopback-bound UI.")
        if data_dir_locked:
            raise HTTPException(
                status_code=409,
                detail="This UI is locked to its explicit --data-dir workspace.",
            )
        if req.path and not allow_server_paths:
            raise HTTPException(
                status_code=403,
                detail="Custom project paths require a loopback-bound UI.",
            )
        try:
            info = pm.create_project(
                name=req.name, path=req.path, description=req.description
            )
            if not data_dir_locked:
                new_path = Path(info["path"]).resolve()
                db_container["data_dir"] = new_path
                db_container["db"] = TraceLite(new_path)
                if start_model_warmup:
                    db_container["db"].start_model_warmup()
            return {"status": "ok", "project": info}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/projects/{name}")
    def delete_project(name: str, purge_external: bool = Query(False)) -> Dict[str, Any]:
        if not allow_server_paths:
            raise HTTPException(status_code=403, detail="Project management requires a loopback-bound UI.")
        if data_dir_locked:
            raise HTTPException(
                status_code=409,
                detail="This UI is locked to its explicit --data-dir workspace.",
            )
        try:
            pm.delete_project(
                name,
                delete_files=True,
                purge_external=purge_external,
            )
            active_name = pm.get_active_project_name()
            if active_name is None:
                db_container["data_dir"] = None
                db_container["db"] = None
            else:
                active_path = pm.get_active_project_path()
                db_container["data_dir"] = active_path
                db_container["db"] = TraceLite(active_path)
                if start_model_warmup:
                    db_container["db"].start_model_warmup()
            return {"status": "ok", "active_project": active_name}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProjectDeletionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/config")
    def get_config() -> Dict[str, Any]:
        cfg = load_config_data()
        providers = [
            {
                "id": provider.id,
                "name": provider.name,
                "description": provider.description,
                "default_model": provider.default_model,
                "popular_models": provider.popular_models,
                "requires_api_key": provider.requires_api_key,
                "requires_api_base": provider.requires_api_base,
                "default_api_base": provider.default_api_base,
                "requires_api_version": provider.requires_api_version,
                "default_api_version": provider.default_api_version,
            }
            for provider in POPULAR_PROVIDERS
        ]
        safe = _safe_config_payload(cfg)
        return {
            "active_provider": safe["active_provider"],
            "active_model": safe["active_model"],
            "api_base": safe["api_base"],
            "api_version": safe["api_version"],
            "auto_load_models": safe["auto_load_models"],
            "saved_providers": safe["providers"],
            "available_providers": providers,
        }

    @app.post("/api/config")
    def update_config(req: SaveConfigRequest) -> Dict[str, Any]:
        try:
            result = save_provider_key(
                provider_id=req.provider_id,
                api_key=req.api_key,
                model=req.model,
                api_base=req.api_base,
                api_version=req.api_version,
                set_active=True,
            )
        except ProviderConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "Provider save failed: %s",
                " ".join(redact_diagnostic(str(exc)).split())[:500] or type(exc).__name__,
            )
            logger.debug(
                "Provider save sanitized traceback:\n%s",
                redact_diagnostic(traceback.format_exc()),
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Provider was not saved. Check Credential Manager and try Save & Verify again.",
                    "error_code": "provider_save_failed",
                    "stage": "provider_save",
                },
            )
        return {"status": "ok", "config": _safe_config_payload(result)}

    @app.post("/api/config/test")
    def test_config(req: TestConfigRequest) -> Dict[str, Any]:
        # This intentionally tests the saved runtime resolver, never a key
        # transiently entered into a browser form.
        success, message = verify_provider_connection(
            config_path=None
        )
        return {"success": success, "message": message}

    @app.get("/api/export")
    def export_database() -> Any:
        active_name = pm.get_active_project_name()
        if active_name is None:
            raise HTTPException(status_code=409, detail=NO_ACTIVE_PROJECT_MESSAGE)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", active_name)
        export_zip = Path(tempfile.gettempdir()) / f"tracelite_export_{safe_name}.zip"
        get_db().export(export_zip)
        return FileResponse(
            path=str(export_zip), filename=export_zip.name, media_type="application/zip"
        )

    @app.get("/api/benchmarks/runs")
    def list_benchmark_runs() -> Dict[str, Any]:
        """List all benchmark run results in benchmarks/results."""
        results_dir = Path("benchmarks/results")
        runs = []
        if results_dir.exists():
            for p in sorted(results_dir.glob("run_*.json"), reverse=True):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    runs.append({
                        "run_id": data.get("run_id", p.stem),
                        "dataset_name": data.get("dataset_name", ""),
                        "dataset_version": data.get("dataset_version", ""),
                        "timestamp": data.get("timestamp", ""),
                        "total_cases": data.get("total_cases", 0),
                        "baselines": list(data.get("baselines", {}).keys()),
                        "path": str(p),
                    })
                except Exception:
                    pass
        return {"runs": runs}

    @app.get("/api/benchmarks/report/{run_id}", response_class=HTMLResponse)
    def get_benchmark_html_report(run_id: str) -> HTMLResponse:
        """Serve generated HTML benchmark report for a specific run_id."""
        from benchmarks.reporter import generate_html_report
        from benchmarks.metrics.aggregator import BenchmarkRunResult

        results_dir = Path("benchmarks/results")
        target = results_dir / f"{run_id}.json"
        if not target.exists():
            # Try searching by exact stem
            matches = list(results_dir.glob(f"*{run_id}*.json"))
            if matches:
                target = matches[0]
            else:
                raise HTTPException(status_code=404, detail=f"Benchmark run '{run_id}' not found.")

        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        run_res = BenchmarkRunResult.from_dict(data)
        html_content = generate_html_report(run_res)
        return HTMLResponse(content=html_content)

    # Existing v1 routes remain compatible with the initial UI/API release.

    @app.post("/api/v1/vault/sync")
    def sync_vault_endpoint(req: VaultSyncRequest) -> Dict[str, Any]:
        organize = req.auto_consolidate
        if req.organize_after_sync is not None:
            organize = req.organize_after_sync
        return _sync_folder(req.vault_path, organize)

    @app.get("/api/v1/trees/{tree_id}/dag")
    def get_tree_dag(tree_id: str) -> Dict[str, Any]:
        if not get_db().forest.get_tree(tree_id):
            raise HTTPException(status_code=404, detail=f"Tree '{tree_id}' not found.")
        return serialize_dag(get_db(), tree_id)

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    index_file = static_dir / "index.html"
    if not index_file.exists():
        index_file.write_text("<h1>trace-lite UI static bundle missing</h1>", encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def serve_dashboard() -> HTMLResponse:
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    if start_model_warmup and db_container.get("db") is not None:
        db_container["db"].start_model_warmup()
    return app
