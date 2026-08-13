"""Fail-closed durable provider configuration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from trace_lite.cli import main
from trace_lite.config import TraceLiteConfig
from trace_lite import providers
from trace_lite.providers import (
    CredentialStorageError,
    POPULAR_PROVIDERS,
    ProviderVerificationError,
    load_config_data,
    resolve_active_provider,
    save_provider_key,
    verify_provider_connection,
)


def _credential_manager(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values: dict[str, str] = {}

    def get(provider_id: str, *, required: bool = False) -> str | None:
        return values.get(provider_id)

    def set_value(provider_id: str, value: str) -> None:
        values[provider_id] = value

    def delete(provider_id: str) -> None:
        values.pop(provider_id, None)

    monkeypatch.setattr(providers, "_keyring_get", get)
    monkeypatch.setattr(providers, "_keyring_set", set_value)
    monkeypatch.setattr(providers, "_keyring_delete", delete)
    return values


def test_popular_providers_list():
    provider_ids = [provider.id for provider in POPULAR_PROVIDERS]
    assert len(provider_ids) == len(set(provider_ids)) == 19
    assert "bedrock" not in provider_ids
    assert "deepseek/deepseek-v4-pro" in providers.get_provider_by_id("deepseek").popular_models


def test_save_verify_is_fresh_runtime_resolvable_and_never_uses_environment(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    values = _credential_manager(monkeypatch)
    monkeypatch.setattr(providers, "_verify_runtime", lambda *_args: (True, "Provider verified."))
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-process-value")

    result = save_provider_key("openai", "sk-durable-key", "gpt-4o-mini", config_path=config_path)

    assert result["active_provider"] == "openai"
    assert values == {"openai": "sk-durable-key"}
    assert "wrong-process-value" == __import__("os").environ["OPENAI_API_KEY"]
    assert "sk-durable-key" not in config_path.read_text(encoding="utf-8")
    assert "sk-durable-key" not in json.dumps(load_config_data(config_path))

    # This resolver is what a separately started TraceLite process uses; it is
    # independent of the request that supplied the candidate key.
    runtime = resolve_active_provider(config_path)
    assert runtime.api_key == "sk-durable-key"
    assert runtime.credential_state == "available"
    assert TraceLiteConfig.default().llm_model  # default remains metadata-only


def test_unavailable_credential_manager_does_not_create_configuration(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"

    def unavailable(_provider_id: str, _value: str) -> None:
        raise CredentialStorageError("Windows Credential Manager is unavailable.")

    monkeypatch.setattr(providers, "_keyring_set", unavailable)
    with pytest.raises(CredentialStorageError):
        save_provider_key("openai", "sk-key", config_path=config_path)
    assert not config_path.exists()


def test_failed_credential_reread_rolls_back_without_activation(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    _credential_manager(monkeypatch)
    monkeypatch.setattr(providers, "_keyring_get", lambda _provider_id, *, required=False: None)

    with pytest.raises(CredentialStorageError, match="did not return"):
        save_provider_key("openai", "sk-key", config_path=config_path)
    assert not config_path.exists()


def test_failed_live_verification_restores_previous_provider_and_key(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    values = _credential_manager(monkeypatch)
    monkeypatch.setattr(providers, "_verify_runtime", lambda *_args: (True, "Provider verified."))
    save_provider_key("openai", "sk-old", "gpt-4o-mini", config_path=config_path)
    before = config_path.read_text(encoding="utf-8")

    monkeypatch.setattr(providers, "_verify_runtime", lambda *_args: (False, "Provider verification failed (AuthenticationError)."))
    with pytest.raises(ProviderVerificationError):
        save_provider_key("openai", "sk-new", "gpt-4.1-mini", config_path=config_path)

    assert values["openai"] == "sk-old"
    assert config_path.read_text(encoding="utf-8") == before
    assert load_config_data(config_path)["active_model"] == "gpt-4o-mini"


def test_saved_connection_test_never_accepts_transient_form_key(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    _credential_manager(monkeypatch)
    monkeypatch.setattr(providers, "_verify_runtime", lambda *_args: (True, "Provider verified."))
    save_provider_key("openai", "sk-saved", config_path=config_path)

    seen: list[str | None] = []

    def verify(_model: str, key: str | None, _base: str | None):
        seen.append(key)
        return True, "Provider verified."

    monkeypatch.setattr(providers, "_verify_runtime", verify)
    success, _message = verify_provider_connection("wrong-model", "sk-transient", config_path=config_path)
    assert success is True
    assert seen == ["sk-saved"]


def test_unknown_saved_provider_is_preserved_but_fails_closed(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"active_provider": "removed", "providers": {"removed": {"model": "old"}}}), encoding="utf-8")
    public = load_config_data(config_path)
    assert public["providers"]["removed"]["credential_state"] == "unsupported"
    assert resolve_active_provider(config_path).credential_state == "unsupported"
    with pytest.raises(providers.ProviderConfigurationError, match="no longer supported"):
        providers.require_active_provider(config_path)


def test_azure_endpoint_version_reaches_litellm(tmp_path: Path, monkeypatch):
    values = _credential_manager(monkeypatch)
    seen = {}
    monkeypatch.setattr(providers, "_verify_runtime", lambda model, key, base, version=None: seen.update(model=model, key=key, base=base, version=version) or (True, "ok"))
    save_provider_key("azure", "secret", "azure/deployment", "https://example.openai.azure.com", "2024-10-21", config_path=tmp_path / "config.json")
    assert values["azure"] == "secret"
    assert seen["base"] == "https://example.openai.azure.com"
    assert seen["version"] == "2024-10-21"


def test_cli_config_list_uses_secret_free_metadata(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("TRACE_LITE_CONFIG_PATH", str(config_path))
    _credential_manager(monkeypatch)
    monkeypatch.setattr(providers, "_verify_runtime", lambda *_args: (True, "Provider verified."))
    save_provider_key("anthropic", "sk-ant-key", config_path=config_path)

    result = CliRunner().invoke(main, ["config", "list"])
    assert result.exit_code == 0
    assert "Anthropic" in result.output
    assert "sk-ant-key" not in result.output
