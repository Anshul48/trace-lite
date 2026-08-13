#!/usr/bin/env python3
"""Refresh the checked-in OpenRouter-backed model snapshot.

The provider catalog is the source of truth for namespace mappings and
LiteLLM rendering.  This updater only replaces the generated model lists after
one complete, authenticated catalog fetch and a second registry validation.
The API key is supplied in memory for one invocation and is never persisted.
"""

from __future__ import annotations

import argparse
import copy
import getpass
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Callable
import urllib.error
import urllib.request


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
PROJECT_OUTPUT_PATH = Path(__file__).resolve().parent / "src" / "trace_lite" / "models.json"
FETCH_TIMEOUT_SECONDS = 15

# These are capability/category markers, not model or provider mappings.  The
# mappings themselves must remain in models.json so a new provider namespace or
# model ID never requires a Python source change.
_EXCLUDED_MODEL_MARKERS = (
    "batch",
    "embedding",
    "embeddings",
    "image",
    "audio",
    "speech",
    "moderation",
    "guard",
    "rerank",
    "re-rank",
    "transcription",
    "transcribe",
)
_EXCLUDED_MODALITIES = frozenset(
    {
        "embedding",
        "embeddings",
        "image",
        "audio",
        "speech",
        "moderation",
        "rerank",
        "transcription",
    }
)
_GENERATED_REGISTRY_FIELDS = frozenset({"default_model", "popular_models"})
_SECRET_FLAG_NAMES = frozenset(
    {"-k", "--key", "--api-key", "--openrouter-api-key"}
)


class OpenRouterRefreshError(RuntimeError):
    """A safe, user-facing refresh failure with no request diagnostics."""


class RegistryValidationError(OpenRouterRefreshError):
    """The input or transformed provider registry is not usable."""


Opener = Callable[..., Any]


def _validate_model_catalog(raw_models: Any) -> list[dict[str, Any]]:
    """Validate the small part of the OpenRouter schema used by this tool."""
    if not isinstance(raw_models, list):
        raise OpenRouterRefreshError("OpenRouter returned an invalid model catalog.")

    for item in raw_models:
        if not isinstance(item, dict):
            raise OpenRouterRefreshError("OpenRouter returned a malformed model record.")

        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise OpenRouterRefreshError("OpenRouter returned a malformed model record.")

        if "created" in item:
            created = item["created"]
            if (
                isinstance(created, bool)
                or not isinstance(created, (int, float))
                or not math.isfinite(float(created))
            ):
                raise OpenRouterRefreshError("OpenRouter returned a malformed model record.")

        architecture = item.get("architecture")
        if architecture is not None and not isinstance(architecture, dict):
            raise OpenRouterRefreshError("OpenRouter returned a malformed model record.")
        if isinstance(architecture, dict):
            for field in ("input_modalities", "output_modalities"):
                modalities = architecture.get(field)
                if modalities is not None and (
                    not isinstance(modalities, list)
                    or any(not isinstance(value, str) for value in modalities)
                ):
                    raise OpenRouterRefreshError("OpenRouter returned a malformed model record.")
            modality = architecture.get("modality")
            if modality is not None and not isinstance(modality, str):
                raise OpenRouterRefreshError("OpenRouter returned a malformed model record.")

    return raw_models


def _response_status(response: Any) -> int | None:
    """Read a response status without depending on a particular test double."""
    status = getattr(response, "status", None)
    if status is None:
        getcode = getattr(response, "getcode", None)
        if callable(getcode):
            try:
                status = getcode()
            except Exception:
                return None
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def fetch_openrouter_models(api_key: str, opener: Opener | None = None) -> list[dict[str, Any]]:
    """Fetch and validate OpenRouter's live model catalog.

    ``opener`` is injectable for offline tests.  Error messages deliberately
    contain no response body, request object, or credential material.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise OpenRouterRefreshError("An OpenRouter API key is required.")

    request = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
            "User-Agent": "trace-lite-model-refresh/1.0",
        },
    )
    open_url = opener or urllib.request.urlopen

    try:
        with open_url(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            status = _response_status(response)
            if status is not None and not 200 <= status < 300:
                if status in (401, 403):
                    raise OpenRouterRefreshError("OpenRouter authentication failed.")
                raise OpenRouterRefreshError("OpenRouter returned an unsuccessful response.")
            body = response.read()
    except OpenRouterRefreshError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise OpenRouterRefreshError("OpenRouter authentication failed.") from None
        raise OpenRouterRefreshError("OpenRouter request failed.") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise OpenRouterRefreshError("OpenRouter request failed.") from None
    except Exception:
        # Do not expose arbitrary opener/HTTP diagnostics: a test double or an
        # HTTP library may include headers, response bodies, or the key.
        raise OpenRouterRefreshError("OpenRouter request failed.") from None

    try:
        if isinstance(body, bytes):
            payload = json.loads(body.decode("utf-8"))
        elif isinstance(body, str):
            payload = json.loads(body)
        else:
            raise ValueError
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        raise OpenRouterRefreshError("OpenRouter returned invalid JSON.") from None

    if not isinstance(payload, dict) or "data" not in payload:
        raise OpenRouterRefreshError("OpenRouter returned an invalid model catalog.")
    return _validate_model_catalog(payload["data"])


def _marker_in_model_id(model_id: str) -> bool:
    normalized = model_id.casefold()
    if re.search(r"(^|[/:._-])batch($|[/:._-])", normalized):
        return True
    return any(marker in normalized for marker in _EXCLUDED_MODEL_MARKERS)


def is_real_production_model(model: dict[str, Any]) -> bool:
    """Return whether a catalog record is an eligible text chat model.

    The API does not expose one universal ``is_chat`` flag.  Text output,
    modality metadata, and conservative category markers together provide a
    stable filter while allowing multimodal *input* on a text-response model.
    """
    model_id = model.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return False
    if _marker_in_model_id(model_id):
        return False

    architecture = model.get("architecture")
    if architecture is not None and not isinstance(architecture, dict):
        return False
    if not isinstance(architecture, dict):
        return True

    output_modalities = architecture.get("output_modalities")
    if output_modalities is not None:
        if not isinstance(output_modalities, list) or any(
            not isinstance(value, str) for value in output_modalities
        ):
            return False
        normalized = {value.casefold() for value in output_modalities}
        if "text" not in normalized or normalized & _EXCLUDED_MODALITIES:
            return False

    modality = architecture.get("modality")
    if isinstance(modality, str):
        normalized_modality = modality.casefold()
        # Image/audio input is compatible with a text-response chat model.
        # Reject non-chat output categories represented in a modality string,
        # while output_modalities above remains the authoritative check when
        # the API provides it.
        non_chat_markers = (
            "batch",
            "embedding",
            "moderation",
            "guard",
            "rerank",
            "re-rank",
            "transcription",
            "transcribe",
        )
        if any(marker in normalized_modality for marker in non_chat_markers):
            return False
        if "->" in normalized_modality:
            output_description = normalized_modality.split("->", 1)[1]
            if any(marker in output_description for marker in ("image", "audio", "speech")):
                return False

    return True


def _created_value(model: dict[str, Any]) -> float:
    value = model.get("created", 0)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _registry_prefixes(entry: dict[str, Any]) -> list[str]:
    value = entry.get("openrouter_prefixes")
    if value is None:
        # Accept equivalent names in fixture registries while keeping one
        # canonical field in the checked-in catalog.
        value = entry.get(
            "openrouter_namespaces",
            entry.get(
                "openrouter_namespace_prefixes",
                entry.get("openrouter_prefix", entry.get("openrouter_namespace")),
            ),
        )
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(prefix, str) for prefix in value):
        return value
    return []


def _normalise_prefix(prefix: str) -> str:
    if prefix in ("", "*") or prefix.endswith("/"):
        return prefix
    return f"{prefix}/"


def _matching_prefix(model_id: str, prefixes: list[str]) -> str | None:
    matches = [
        _normalise_prefix(prefix)
        for prefix in prefixes
        if prefix == "*" or model_id.startswith(_normalise_prefix(prefix))
    ]
    if not matches:
        return None
    # Longest-prefix matching makes overlapping namespace metadata
    # deterministic and lets a broad provider mapping coexist with a narrow
    # one in a fixture catalog.
    return max(matches, key=len)


def _litellm_format(entry: dict[str, Any]) -> str:
    value = entry.get("litellm_format", entry.get("litellm_model_format", "openrouter/<raw-id>"))
    return value if isinstance(value, str) else ""


def _render_model(entry: dict[str, Any], model_id: str, matched_prefix: str) -> str:
    prefix = "" if matched_prefix == "*" else matched_prefix
    raw_suffix = model_id[len(prefix) :] if prefix and model_id.startswith(prefix) else model_id
    rendering = _litellm_format(entry).casefold().replace("_", "-")

    if rendering in {"direct", "provider", "provider-prefixed", "provider-prefixed/<raw-id>"}:
        provider_prefix = entry.get(
            "litellm_provider",
            entry.get("litellm_provider_prefix", entry.get("provider_prefix", entry.get("id", ""))),
        )
        if not isinstance(provider_prefix, str) or not provider_prefix.strip():
            raise RegistryValidationError("The provider catalog has an invalid LiteLLM mapping.")
        return f"{provider_prefix.strip()}/{raw_suffix}"
    if rendering in {"openrouter", "openrouter/<raw-id>"}:
        return f"openrouter/{model_id}"
    raise RegistryValidationError("The provider catalog has an invalid LiteLLM mapping.")


def _is_refreshable(entry: dict[str, Any]) -> bool:
    value = entry.get("openrouter_refreshable", entry.get("refreshable", False))
    return value is True


def _models_for(entry: dict[str, Any], raw_models: list[dict[str, Any]]) -> list[str]:
    prefixes = _registry_prefixes(entry)
    if not _is_refreshable(entry) or not prefixes:
        return []

    candidates: list[tuple[dict[str, Any], str]] = []
    seen_ids: set[str] = set()
    for model in raw_models:
        if not is_real_production_model(model):
            continue
        model_id = model["id"]
        matched_prefix = _matching_prefix(model_id, prefixes)
        if matched_prefix is not None and model_id not in seen_ids:
            candidates.append((model, matched_prefix))
            seen_ids.add(model_id)

    candidates.sort(key=lambda item: (-_created_value(item[0]), item[0]["id"]))
    # Keep the complete eligible catalog.  The terminal selector is
    # scrollable, so truncating here silently hides provider models and makes
    # a refreshed model impossible to select.
    return [
        _render_model(entry, model["id"], matched_prefix)
        for model, matched_prefix in candidates
    ]


def _validate_registry(registry: Any) -> None:
    """Validate the fixed provider registry and its generated fields."""
    if not isinstance(registry, list) or len(registry) != 19:
        raise RegistryValidationError("The provider registry must contain exactly 19 entries.")

    ids: list[str] = []
    required_fields = {
        "id",
        "name",
        "description",
        "env_var",
        "default_model",
        "popular_models",
        "requires_api_key",
        "requires_api_base",
        "default_api_base",
        "openrouter_prefixes",
        "litellm_format",
        "openrouter_refreshable",
    }
    valid_formats = {"direct", "provider", "provider-prefixed", "provider-prefixed/<raw-id>", "openrouter", "openrouter/<raw-id>"}

    for entry in registry:
        if not isinstance(entry, dict) or not required_fields.issubset(entry):
            raise RegistryValidationError("The provider registry has an invalid entry.")
        provider_id = entry.get("id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise RegistryValidationError("The provider registry has an invalid entry.")
        ids.append(provider_id)
        if not isinstance(entry.get("name"), str) or not isinstance(entry.get("description"), str):
            raise RegistryValidationError("The provider registry has an invalid entry.")
        if entry.get("env_var") is not None and not isinstance(entry.get("env_var"), str):
            raise RegistryValidationError("The provider registry has an invalid entry.")
        if not isinstance(entry.get("default_model"), str) or not entry["default_model"].strip():
            raise RegistryValidationError("The provider registry has an invalid entry.")
        popular_models = entry.get("popular_models")
        if not isinstance(popular_models, list) or not popular_models or any(
            not isinstance(model, str) or not model.strip() for model in popular_models
        ):
            raise RegistryValidationError("The provider registry has an invalid entry.")
        if entry["default_model"] not in popular_models:
            raise RegistryValidationError("The provider registry has inconsistent model defaults.")
        if not isinstance(entry.get("requires_api_key"), bool) or not isinstance(entry.get("requires_api_base"), bool):
            raise RegistryValidationError("The provider registry has invalid authentication metadata.")
        if entry.get("default_api_base") is not None and not isinstance(entry.get("default_api_base"), str):
            raise RegistryValidationError("The provider registry has invalid endpoint metadata.")
        if "requires_api_version" in entry and not isinstance(entry["requires_api_version"], bool):
            raise RegistryValidationError("The provider registry has invalid endpoint metadata.")
        if "default_api_version" in entry and entry["default_api_version"] is not None and not isinstance(
            entry["default_api_version"], str
        ):
            raise RegistryValidationError("The provider registry has invalid endpoint metadata.")

        prefixes = entry.get("openrouter_prefixes")
        if not isinstance(prefixes, list) or any(not isinstance(prefix, str) for prefix in prefixes):
            raise RegistryValidationError("The provider registry has an invalid namespace mapping.")
        if not isinstance(entry.get("openrouter_refreshable"), bool):
            raise RegistryValidationError("The provider registry has an invalid refresh flag.")
        if entry["openrouter_refreshable"] and not prefixes:
            raise RegistryValidationError("The provider registry has an incomplete namespace mapping.")
        rendering = entry.get("litellm_format")
        if not isinstance(rendering, str) or rendering.casefold().replace("_", "-") not in valid_formats:
            raise RegistryValidationError("The provider registry has an invalid LiteLLM mapping.")
        if "litellm_provider" in entry and not isinstance(entry["litellm_provider"], str):
            raise RegistryValidationError("The provider registry has an invalid LiteLLM mapping.")

    if len(ids) != len(set(ids)):
        raise RegistryValidationError("The provider registry must contain unique provider IDs.")
    if any(provider_id.casefold() in {"bedrock", "amazon"} for provider_id in ids):
        raise RegistryValidationError("The provider registry contains an unsupported provider.")


def _provider_identity(registry: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in entry.items() if key not in _GENERATED_REGISTRY_FIELDS}
        for entry in registry
    ]


def build_popular_providers_from_openrouter(
    raw_models: list[dict[str, Any]],
    registry: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build a refreshed registry copy from only the supplied live records."""
    _validate_model_catalog(raw_models)
    if registry is None:
        registry = _load_registry(PROJECT_OUTPUT_PATH)
    else:
        _validate_registry(registry)

    refreshed = copy.deepcopy(registry)
    for entry in refreshed:
        if not isinstance(entry, dict):
            raise RegistryValidationError("The provider registry has an invalid entry.")
        models = _models_for(entry, raw_models)
        if models:
            entry["popular_models"] = models
            entry["default_model"] = models[0]
    return refreshed


def _load_registry(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            registry = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        raise RegistryValidationError("The provider registry could not be read or parsed.") from None
    _validate_registry(registry)
    return registry


def update_models_json(providers: list[dict[str, Any]], output_path: Path | str = PROJECT_OUTPUT_PATH) -> None:
    """Atomically write a complete, already-validated provider registry."""
    _validate_registry(providers)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(providers, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
    except Exception:
        raise OpenRouterRefreshError("The provider registry could not be replaced.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def refresh(
    output_path: Path | str = PROJECT_OUTPUT_PATH,
    api_key: str | None = None,
    *,
    opener: Opener | None = None,
) -> tuple[int, int]:
    """Fetch, transform, validate, and atomically refresh the model snapshot."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise OpenRouterRefreshError("An OpenRouter API key is required.")

    output = Path(output_path)
    registry = _load_registry(output)
    raw_models = (
        fetch_openrouter_models(api_key, opener=opener)
        if opener is not None
        else fetch_openrouter_models(api_key)
    )
    refreshed = build_popular_providers_from_openrouter(raw_models, registry=registry)
    _validate_registry(refreshed)
    if _provider_identity(refreshed) != _provider_identity(registry):
        raise RegistryValidationError("The provider registry metadata changed during refresh.")

    changed = sum(
        old.get("popular_models") != new.get("popular_models")
        or old.get("default_model") != new.get("default_model")
        for old, new in zip(registry, refreshed)
    )
    skipped = len(registry) - changed
    # A no-match catalog is a valid offline-refresh outcome.  Avoid even a
    # formatting-only rewrite so the last-known-good snapshot stays byte-for-byte
    # intact when no provider produced a replacement list.
    if changed:
        update_models_json(refreshed, output)
    return changed, skipped


def _contains_secret_flag(arguments: list[str]) -> bool:
    for argument in arguments:
        if argument in _SECRET_FLAG_NAMES:
            return True
        if any(argument.startswith(f"{flag}=") for flag in _SECRET_FLAG_NAMES):
            return True
        if argument.startswith("-k") and argument != "--":
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if _contains_secret_flag(arguments):
        print("error: enter the OpenRouter API key at the masked prompt; secret flags are not accepted.", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description="Refresh the generated OpenRouter model snapshot.")
    parser.add_argument("--output", type=Path, default=PROJECT_OUTPUT_PATH, help="Registry JSON to update")
    args = parser.parse_args(arguments)

    try:
        api_key = getpass.getpass("OpenRouter API key: ")
    except (EOFError, KeyboardInterrupt):
        print("Refresh aborted; no files changed.", file=sys.stderr)
        return 1
    if not isinstance(api_key, str) or not api_key.strip():
        print("Refresh aborted; no API key supplied and no files changed.", file=sys.stderr)
        return 1

    try:
        changed, skipped = refresh(output_path=args.output, api_key=api_key)
    except OpenRouterRefreshError as exc:
        print(f"Refresh failed; no files changed. {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Keep an unexpected library/runtime diagnostic from echoing a secret
        # that a lower-level implementation might have included.
        print("Refresh failed; no files changed.", file=sys.stderr)
        return 1

    print(f"Refreshed {changed} provider(s); skipped {skipped} provider(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
