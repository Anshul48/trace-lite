import os
from pathlib import Path
import pytest
from click.testing import CliRunner

from trace_lite.providers import (
    POPULAR_PROVIDERS,
    get_provider_by_id,
    save_provider_key,
    load_config_data,
    apply_saved_config,
    verify_provider_connection,
)
from trace_lite.config import TraceLiteConfig
from trace_lite.cli import main


def test_popular_providers_list():
    provider_ids = [p.id for p in POPULAR_PROVIDERS]
    assert "openai" in provider_ids
    assert "anthropic" in provider_ids
    assert "gemini" in provider_ids
    assert "groq" in provider_ids
    assert "mistral" in provider_ids
    assert "deepseek" in provider_ids
    assert "cohere" in provider_ids
    assert "together" in provider_ids
    assert "ollama" in provider_ids
    assert "custom" in provider_ids


def test_save_provider_key_and_apply_config(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setenv("TRACE_LITE_CONFIG_PATH", str(cfg_file))

    # Clear env var if set
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    res = save_provider_key(
        provider_id="openai",
        api_key="sk-testkey12345",
        model="gpt-4o-mini",
        config_path=cfg_file,
    )

    assert res["active_provider"] == "openai"
    assert res["active_model"] == "gpt-4o-mini"
    assert os.environ.get("OPENAI_API_KEY") == "sk-testkey12345"

    # Test load_config_data
    loaded = load_config_data(cfg_file)
    assert loaded["providers"]["openai"]["api_key"] == "sk-testkey12345"

    # Test apply_saved_config
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    active_model, active_api_base = apply_saved_config(cfg_file)
    assert active_model == "gpt-4o-mini"
    assert os.environ.get("OPENAI_API_KEY") == "sk-testkey12345"

    # Test TraceLiteConfig.default() integration
    config = TraceLiteConfig.default()
    assert config.llm_model == "gpt-4o-mini"


def test_cli_providers_list(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setenv("TRACE_LITE_CONFIG_PATH", str(cfg_file))

    save_provider_key(
        provider_id="anthropic",
        api_key="sk-ant-testkey",
        model="claude-3-5-sonnet-20240620",
        config_path=cfg_file,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["providers", "--list"])

    assert result.exit_code == 0
    assert "LLM Provider Configurations" in result.output
    assert "Anthropic" in result.output
    assert "★ Active" in result.output


def test_verify_provider_connection_mock(monkeypatch):
    import types
    import sys

    class DummyChoice:
        class DummyMessage:
            content = "Hello from LLM!"
        message = DummyMessage()

    class DummyResponse:
        choices = [DummyChoice()]

    def mock_completion(**kwargs):
        return DummyResponse()

    dummy_litellm = types.ModuleType("litellm")
    dummy_litellm.completion = mock_completion
    monkeypatch.setitem(sys.modules, "litellm", dummy_litellm)

    success, msg = verify_provider_connection("gpt-4o-mini", api_key="sk-mock")
    assert success is True
    assert "Hello from LLM!" in msg


