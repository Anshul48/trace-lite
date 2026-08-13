# Copyright 2026 trace-lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Provider metadata and durable, verified credential configuration.

Provider metadata is stored in JSON.  API keys are deliberately never stored
there or copied into environment variables: on Windows the operating system's
Credential Manager, accessed through :mod:`keyring`, is the only durable store
used for setup-entered keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import platform
from pathlib import Path
import tempfile
from typing import Any

from trace_lite.adapters.llm import LLMPreflightError, LiteLLMAdapter, OllamaAvailabilityError


class ProviderConfigurationError(RuntimeError):
    """A saved provider cannot safely be used by a new runtime."""


class CredentialStorageError(ProviderConfigurationError):
    """Windows Credential Manager is unavailable or rejected a credential."""


class ProviderVerificationError(ProviderConfigurationError):
    """A candidate provider failed its live verification request."""


_CREDENTIAL_SERVICE = "trace-lite"


def credential_store_label() -> str:
    """Name the recommended native credential store for this platform."""
    return {
        "Windows": "Windows Credential Manager",
        "Darwin": "macOS Keychain",
    }.get(platform.system(), "the system credential store")


def get_config_file_path(custom_path: Path | str | None = None) -> Path:
    """Return the location of the metadata-only persistent configuration."""
    if custom_path:
        return Path(custom_path)
    env_path = os.environ.get("TRACE_LITE_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return Path.home() / ".trace_lite" / "config.json"


@dataclass
class LLMProvider:
    id: str
    name: str
    description: str
    env_var: str | None
    default_model: str
    popular_models: list[str] = field(default_factory=list)
    requires_api_key: bool = True
    requires_api_base: bool = False
    default_api_base: str | None = None
    requires_api_version: bool = False
    default_api_version: str | None = None


@dataclass(frozen=True)
class RuntimeProvider:
    """The resolved runtime configuration, including a non-persisted key."""

    provider_id: str | None
    model: str | None
    api_base: str | None
    api_version: str | None
    api_key: str | None
    requires_api_key: bool
    credential_state: str


def load_popular_providers(json_path: Path | str | None = None) -> list[LLMProvider]:
    """Load the provider registry from the package's single source of truth."""
    search_paths: list[Path] = []
    if json_path:
        search_paths.append(Path(json_path))
    env_models_path = os.environ.get("TRACE_LITE_MODELS_PATH")
    if env_models_path:
        search_paths.append(Path(env_models_path))
    search_paths.append(Path(__file__).parent / "models.json")

    for path in search_paths:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return [
                LLMProvider(
                    id=item["id"],
                    name=item["name"],
                    description=item.get("description", ""),
                    env_var=item.get("env_var"),
                    default_model=item.get("default_model", ""),
                    popular_models=item.get("popular_models", []),
                    requires_api_key=item.get("requires_api_key", True),
                    requires_api_base=item.get("requires_api_base", False),
                    default_api_base=item.get("default_api_base"),
                    requires_api_version=item.get("requires_api_version", False),
                    default_api_version=item.get("default_api_version"),
                )
                for item in data
            ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue
    raise FileNotFoundError(f"models.json registry file could not be found at {search_paths[-1]}.")


POPULAR_PROVIDERS: list[LLMProvider] = load_popular_providers()


def get_provider_by_id(provider_id: str | None) -> LLMProvider | None:
    """Find a provider definition by ID."""
    if not provider_id:
        return None
    normalized = provider_id.lower()
    return next((provider for provider in POPULAR_PROVIDERS if provider.id == normalized), None)


def _empty_config() -> dict[str, Any]:
    return {
        "active_provider": None,
        "active_model": None,
        "api_base": None,
        "api_version": None,
        # Missing legacy settings intentionally resolve to enabled.  The key is
        # written the next time any global provider setting is saved.
        "auto_load_models": True,
        "providers": {},
    }


def _load_raw_config_data(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load metadata for internal migration/rollback use only."""
    path = get_config_file_path(config_path)
    if not path.exists():
        return _empty_config()
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return _empty_config()
        payload.setdefault("providers", {})
        # Existing installations predate model warm-up.  Only an explicit
        # boolean false opts out; malformed or missing values remain enabled.
        payload["auto_load_models"] = payload.get("auto_load_models") is not False
        return payload
    except (OSError, json.JSONDecodeError):
        return _empty_config()


def _keyring_backend() -> Any:
    """Return only a recommended native OS credential backend.

    Keyring's null, plaintext/file, and fallback/chainer backends may be
    operational but do not meet the setup security guarantee.
    """
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        module = type(backend).__module__.lower()
        backend_name = type(backend).__name__.lower()
        system = platform.system()
        native_modules = {
            "Windows": ("keyring.backends.windows",),
            "Darwin": ("keyring.backends.macos",),
            "Linux": ("keyring.backends.secretservice", "keyring.backends.kwallet"),
        }
        allowed = native_modules.get(system, ())
        unsafe = isinstance(backend, fail.Keyring) or any(
            word in module or word in backend_name
            for word in ("null", "fail", "file", "plaintext", "chainer")
        )
        if unsafe or not allowed or not any(module.startswith(item) for item in allowed):
            raise CredentialStorageError(f"{credential_store_label()} is unavailable or not selected by keyring.")
        return keyring
    except CredentialStorageError:
        raise
    except Exception as exc:
        raise CredentialStorageError(
            "A native system credential store is unavailable. Install/configure keyring and try again."
        ) from exc


def _keyring_get(provider_id: str, *, required: bool = False) -> str | None:
    try:
        value = _keyring_backend().get_password(_CREDENTIAL_SERVICE, provider_id)
    except CredentialStorageError:
        if required:
            raise
        return None
    except Exception as exc:
        if required:
            raise CredentialStorageError(
                f"{credential_store_label()} could not read the saved API key."
            ) from exc
        return None
    return str(value) if value else None


def _keyring_set(provider_id: str, value: str) -> None:
    try:
        _keyring_backend().set_password(_CREDENTIAL_SERVICE, provider_id, value)
    except CredentialStorageError:
        raise
    except Exception as exc:
        raise CredentialStorageError(
            f"{credential_store_label()} could not store the API key."
        ) from exc


def _keyring_delete(provider_id: str) -> None:
    try:
        keyring = _keyring_backend()
        keyring.delete_password(_CREDENTIAL_SERVICE, provider_id)
    except Exception:
        # Rollback is best effort.  Its caller reports the primary error and
        # never activates a configuration whose candidate was not verified.
        pass


def credential_manager_available() -> bool:
    """Whether the configured OS credential backend can be used."""
    try:
        _keyring_backend()
        return True
    except CredentialStorageError:
        return False


def _credential_state(provider_id: str, info: dict[str, Any], provider: LLMProvider | None) -> str:
    if provider is not None and not provider.requires_api_key:
        return "not_required"
    if info.get("api_key"):
        return "legacy_plaintext"
    try:
        return "available" if _keyring_get(provider_id, required=True) else "missing"
    except CredentialStorageError:
        return "unavailable"


def load_config_data(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load a secret-free public view of persisted provider metadata.

    This intentionally never returns ``api_key`` -- even when reading a legacy
    config file that still contains one.  Use the explicit migration command
    to move legacy plaintext into Credential Manager.
    """
    raw = _load_raw_config_data(config_path)
    result = _empty_config()
    result.update({key: raw.get(key) for key in ("active_provider", "active_model", "api_base", "api_version")})
    result["auto_load_models"] = raw.get("auto_load_models") is not False
    providers: dict[str, dict[str, Any]] = {}
    for provider_id, value in (raw.get("providers", {}) or {}).items():
        if not isinstance(value, dict):
            continue
        info = {key: value.get(key) for key in ("provider_id", "name", "model", "api_base", "api_version", "env_var")}
        provider = get_provider_by_id(provider_id)
        state = "unsupported" if provider is None else _credential_state(provider_id, value, provider)
        info["provider_id"] = info["provider_id"] or provider_id
        info["credential_state"] = state
        info["credential_source"] = "credential_manager" if state == "available" else state
        info["has_api_key"] = state == "available"
        providers[provider_id] = info
    result["providers"] = providers
    return result


def sanitize_config_data(config: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe metadata view with no credential material."""
    providers: dict[str, dict[str, Any]] = {}
    for provider_id, info in (config.get("providers", {}) or {}).items():
        if not isinstance(info, dict):
            continue
        state = str(info.get("credential_state") or "missing")
        providers[provider_id] = {
            "provider_id": info.get("provider_id", provider_id),
            "name": info.get("name", provider_id),
            "model": info.get("model"),
            "api_base": info.get("api_base"),
            "api_version": info.get("api_version"),
            "env_var": info.get("env_var"),
            "has_api_key": bool(info.get("has_api_key")),
            "credential_state": state,
            "credential_source": info.get("credential_source"),
        }
    return {
        "active_provider": config.get("active_provider"),
        "active_model": config.get("active_model"),
        "api_base": config.get("api_base"),
        "api_version": config.get("api_version"),
        "auto_load_models": config.get("auto_load_models") is not False,
        "providers": providers,
    }


def save_config_data(data: dict[str, Any], config_path: Path | str | None = None) -> None:
    """Atomically save metadata after defensively stripping all secrets."""
    path = get_config_file_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = json.loads(json.dumps(data))
    safe["auto_load_models"] = safe.get("auto_load_models") is not False
    for info in (safe.get("providers", {}) or {}).values():
        if isinstance(info, dict):
            info.pop("api_key", None)
            info.pop("has_api_key", None)
            info.pop("credential_state", None)
            info.pop("credential_source", None)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    )
    temporary_path = Path(handle.name)
    try:
        with handle:
            json.dump(safe, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def resolve_active_provider(config_path: Path | str | None = None) -> RuntimeProvider:
    """Resolve the active provider through Credential Manager for this runtime."""
    raw = _load_raw_config_data(config_path)
    provider_id = raw.get("active_provider")
    if not provider_id:
        # The built-in local default does not use a credential.
        return RuntimeProvider(None, raw.get("active_model"), raw.get("api_base"), raw.get("api_version"), None, False, "not_configured")
    info = (raw.get("providers", {}) or {}).get(provider_id, {})
    if not isinstance(info, dict):
        info = {}
    provider = get_provider_by_id(provider_id)
    if provider is None:
        return RuntimeProvider(str(provider_id), raw.get("active_model") or info.get("model"), raw.get("api_base") or info.get("api_base"), raw.get("api_version") or info.get("api_version"), None, True, "unsupported")
    requires_key = provider.requires_api_key if provider else True
    key: str | None = None
    state = "not_required"
    if requires_key:
        if info.get("api_key"):
            state = "legacy_plaintext"
        else:
            try:
                key = _keyring_get(str(provider_id), required=True)
                state = "available" if key else "missing"
            except CredentialStorageError:
                state = "unavailable"
    return RuntimeProvider(
        provider_id=str(provider_id),
        model=raw.get("active_model") or info.get("model"),
        api_base=raw.get("api_base") if raw.get("api_base") is not None else info.get("api_base"),
        api_version=raw.get("api_version") if raw.get("api_version") is not None else info.get("api_version"),
        api_key=key,
        requires_api_key=requires_key,
        credential_state=state,
    )


def require_active_provider(config_path: Path | str | None = None) -> RuntimeProvider:
    """Resolve a provider or raise a safe, actionable configuration error."""
    runtime = resolve_active_provider(config_path)
    if runtime.credential_state == "unsupported":
        raise ProviderConfigurationError("The active provider is no longer supported. Configure a curated provider or Custom.")
    if not runtime.requires_api_key:
        return runtime
    if runtime.credential_state == "legacy_plaintext":
        raise ProviderConfigurationError(
            "The active provider has a legacy plaintext key. Run 'tl config "
            "migrate-credentials --yes', then Save & Verify it again."
        )
    if runtime.credential_state == "unavailable":
        raise CredentialStorageError(
            f"{credential_store_label()} is unavailable. Enable it and Save & Verify the provider again."
        )
    if not runtime.api_key:
        raise ProviderConfigurationError(
            f"No API key is available in {credential_store_label()} for the active provider. "
            "Use Save & Verify in Settings or run 'tl config' before organizing."
        )
    return runtime


def _provider_metadata(
    provider_id: str, model: str | None, api_base: str | None, api_version: str | None
) -> tuple[LLMProvider, dict[str, Any]]:
    provider = get_provider_by_id(provider_id)
    if provider is None:
        raise ProviderConfigurationError("Unknown provider ID. Choose a curated provider or Custom.")
    selected_model = model or provider.default_model
    selected_api_base = api_base if api_base is not None else provider.default_api_base
    selected_api_version = api_version if api_version is not None else provider.default_api_version
    if provider.requires_api_base and not (selected_api_base or "").strip():
        raise ProviderConfigurationError(f"{provider.name} requires an endpoint.")
    if provider.requires_api_version and not (selected_api_version or "").strip():
        raise ProviderConfigurationError(f"{provider.name} requires an API version.")
    return provider, {
        "provider_id": provider_id,
        "name": provider.name,
        "model": selected_model,
        "api_base": selected_api_base,
        "api_version": selected_api_version,
        "env_var": provider.env_var,
    }


def _verify_runtime(model: str, api_key: str | None, api_base: str | None, api_version: str | None = None) -> tuple[bool, str]:
    try:
        adapter = LiteLLMAdapter(model=model, api_base=api_base, api_version=api_version, api_key=api_key)
        adapter.preflight()
        return True, "Provider verified."
    except OllamaAvailabilityError as exc:
        return False, str(exc)
    except LLMPreflightError as exc:
        # These messages are deliberately authored by the adapter and contain
        # only a safe failure category and recovery action.  Never include the
        # underlying LiteLLM exception, response body, or request details.
        return False, str(exc)
    except Exception as exc:
        del exc
        return False, "Provider verification failed. Check the selected model, endpoint, and API key."


def save_provider_key(
    provider_id: str,
    api_key: str | None = None,
    model: str | None = None,
    api_base: str | None = None,
    api_version: str | None = None,
    set_active: bool = True,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Store, re-resolve, live-verify, and then atomically activate a provider.

    On every failure the previous active metadata and previous credential for
    this provider are restored.  A successful return therefore means a fresh
    TraceLite process will resolve the same credential and can use it.
    """
    provider_id = provider_id.strip().lower()
    if not provider_id:
        raise ProviderConfigurationError("A provider ID is required.")
    provider, metadata = _provider_metadata(provider_id, model, api_base, api_version)
    previous_raw = _load_raw_config_data(config_path)
    target_path = get_config_file_path(config_path)
    config_existed = target_path.exists()
    # Reading the native backend before any write both validates the secure
    # store and captures the credential needed for rollback.
    previous_key = _keyring_get(provider_id) if provider.requires_api_key else None
    if provider.requires_api_key and not (api_key or previous_key):
        raise ProviderConfigurationError("An API key is required for this provider.")
    wrote_candidate = False
    try:
        if provider.requires_api_key:
            if api_key:
                _keyring_set(provider_id, api_key)
                wrote_candidate = True
            # Read through the exact resolver a future process uses; never
            # accept a successful write that cannot be retrieved durably.
            candidate_key = _keyring_get(provider_id, required=True)
            if not candidate_key:
                raise CredentialStorageError(
                    f"{credential_store_label()} did not return the saved API key. "
                    "The previous provider configuration was kept."
                )
        else:
            candidate_key = None

        ok, message = _verify_runtime(str(metadata["model"]), candidate_key, metadata["api_base"], metadata["api_version"])
        if not ok:
            raise ProviderVerificationError(message)

        candidate = _load_raw_config_data(config_path)
        providers = candidate.setdefault("providers", {})
        providers[provider_id] = metadata
        if set_active:
            candidate["active_provider"] = provider_id
            candidate["active_model"] = metadata["model"]
            candidate["api_base"] = metadata["api_base"]
            candidate["api_version"] = metadata["api_version"]
        save_config_data(candidate, config_path)
    except Exception:
        if provider.requires_api_key:
            if previous_key:
                try:
                    _keyring_set(provider_id, previous_key)
                except CredentialStorageError:
                    pass
            elif wrote_candidate:
                _keyring_delete(provider_id)
        # The metadata write is atomic and happens only after verification.
        # Restore an existing file only if an injected writer unexpectedly
        # failed after replacement; do not create an empty config for a first
        # setup that failed before activation.
        if config_existed:
            try:
                save_config_data(previous_raw, config_path)
            except Exception:
                pass
        elif target_path.exists():
            try:
                target_path.unlink()
            except OSError:
                pass
        raise
    return load_config_data(config_path)


def apply_saved_config(config_path: Path | str | None = None) -> tuple[str | None, str | None]:
    """Return saved model/base metadata without mutating process environment."""
    runtime = resolve_active_provider(config_path)
    return runtime.model, runtime.api_base


def auto_load_models_enabled(config_path: Path | str | None = None) -> bool:
    """Return the persisted embedding-model warm-up preference.

    The default is deliberately enabled for legacy configuration files.  This
    function never creates a file, which keeps read-only commands side-effect
    free; the setting is persisted by :func:`set_auto_load_models` or the next
    successful provider save.
    """
    return _load_raw_config_data(config_path).get("auto_load_models") is not False


def set_auto_load_models(
    enabled: bool, config_path: Path | str | None = None
) -> dict[str, Any]:
    """Persist the global local-model warm-up preference and return safe data."""
    raw = _load_raw_config_data(config_path)
    raw["auto_load_models"] = bool(enabled)
    save_config_data(raw, config_path)
    return load_config_data(config_path)


def migrate_plaintext_credentials(
    config_path: Path | str | None = None, *, confirmed: bool = False
) -> dict[str, Any]:
    """Move legacy plaintext keys to Credential Manager after confirmation."""
    if not confirmed:
        raise ValueError("Credential migration requires explicit confirmation.")
    raw = _load_raw_config_data(config_path)
    migrated: list[str] = []
    for provider_id, info in (raw.get("providers", {}) or {}).items():
        if not isinstance(info, dict) or not info.get("api_key"):
            continue
        key = str(info["api_key"])
        _keyring_set(provider_id, key)
        if _keyring_get(provider_id, required=True) != key:
            raise CredentialStorageError(
                f"{credential_store_label()} could not verify the migrated key for {provider_id}."
            )
        info.pop("api_key", None)
        migrated.append(provider_id)
    save_config_data(raw, config_path)
    return {"migrated": migrated, "config": sanitize_config_data(load_config_data(config_path))}


def verify_provider_connection(
    model: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    api_version: str | None = None,
    *,
    config_path: Path | str | None = None,
) -> tuple[bool, str]:
    """Verify only the already-saved runtime configuration.

    Parameters are retained for source compatibility but intentionally ignored
    so a form-entered or process-local key can never produce a misleading
    successful test.
    """
    del model, api_key, api_base, api_version
    try:
        runtime = require_active_provider(config_path)
        selected_model = runtime.model
        if not selected_model:
            return False, "No active model is configured. Use Save & Verify first."
        if runtime.api_version is None:
            return _verify_runtime(selected_model, runtime.api_key, runtime.api_base)
        return _verify_runtime(selected_model, runtime.api_key, runtime.api_base, runtime.api_version)
    except ProviderConfigurationError as exc:
        return False, str(exc)
