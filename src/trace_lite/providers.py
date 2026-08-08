# Copyright 2026 trace-lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""LLM provider registry and key management using LiteLLM."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any


def get_config_file_path(custom_path: Path | str | None = None) -> Path:
    """Return the location of the persistent configuration file."""
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


POPULAR_PROVIDERS: list[LLMProvider] = [
    LLMProvider(
        id="openai",
        name="OpenAI",
        description="GPT-4o, GPT-4o-mini, GPT-4 Turbo, GPT-3.5-Turbo",
        env_var="OPENAI_API_KEY",
        default_model="gpt-4o-mini",
        popular_models=["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
        requires_api_key=True,
    ),
    LLMProvider(
        id="anthropic",
        name="Anthropic",
        description="Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku",
        env_var="ANTHROPIC_API_KEY",
        default_model="claude-3-5-sonnet-20240620",
        popular_models=[
            "claude-3-5-sonnet-20240620",
            "claude-3-haiku-20240307",
            "claude-3-opus-20240229",
        ],
        requires_api_key=True,
    ),
    LLMProvider(
        id="gemini",
        name="Google Gemini",
        description="Gemini 1.5 Flash, Gemini 1.5 Pro, Gemini 2.0 Flash",
        env_var="GEMINI_API_KEY",
        default_model="gemini/gemini-1.5-flash",
        popular_models=[
            "gemini/gemini-1.5-flash",
            "gemini/gemini-1.5-pro",
            "gemini/gemini-2.0-flash-exp",
        ],
        requires_api_key=True,
    ),
    LLMProvider(
        id="groq",
        name="Groq",
        description="Ultra-fast Llama 3.1 70B, Llama 3.1 8B, Mixtral",
        env_var="GROQ_API_KEY",
        default_model="groq/llama-3.1-8b-instant",
        popular_models=[
            "groq/llama-3.1-8b-instant",
            "groq/llama-3.1-70b-versatile",
            "groq/mixtral-8x7b-32768",
        ],
        requires_api_key=True,
    ),
    LLMProvider(
        id="mistral",
        name="Mistral AI",
        description="Mistral Large, Mistral Small, Codestral",
        env_var="MISTRAL_API_KEY",
        default_model="mistral/mistral-small-latest",
        popular_models=[
            "mistral/mistral-small-latest",
            "mistral/mistral-large-latest",
            "mistral/codestral-latest",
        ],
        requires_api_key=True,
    ),
    LLMProvider(
        id="deepseek",
        name="DeepSeek",
        description="DeepSeek V3, DeepSeek R1, DeepSeek Coder",
        env_var="DEEPSEEK_API_KEY",
        default_model="deepseek/deepseek-chat",
        popular_models=[
            "deepseek/deepseek-chat",
            "deepseek/deepseek-coder",
            "deepseek/deepseek-reasoner",
        ],
        requires_api_key=True,
    ),
    LLMProvider(
        id="cohere",
        name="Cohere",
        description="Command R+, Command R",
        env_var="COHERE_API_KEY",
        default_model="command-r-plus",
        popular_models=["command-r-plus", "command-r"],
        requires_api_key=True,
    ),
    LLMProvider(
        id="together",
        name="Together AI",
        description="Hosted Llama 3.1, Qwen, DeepSeek models",
        env_var="TOGETHERAI_API_KEY",
        default_model="together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        popular_models=[
            "together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
            "together_ai/mistralai/Mixtral-8x7B-Instruct-v0.1",
        ],
        requires_api_key=True,
    ),
    LLMProvider(
        id="ollama",
        name="Ollama (Local)",
        description="Run local models (Llama 3.1, Qwen 2.5, etc.) via LiteLLM",
        env_var=None,
        default_model="ollama/llama3.1:8b",
        popular_models=["ollama/llama3.1:8b", "ollama/mistral", "ollama/qwen2.5:7b"],
        requires_api_key=False,
        requires_api_base=True,
        default_api_base="http://localhost:11434",
    ),
    LLMProvider(
        id="custom",
        name="Custom OpenAI-Compatible API",
        description="Custom API Base endpoint, Key, and Model ID",
        env_var="CUSTOM_LLM_API_KEY",
        default_model="openai/custom-model",
        popular_models=["openai/custom-model"],
        requires_api_key=True,
        requires_api_base=True,
    ),
]


def get_provider_by_id(provider_id: str) -> LLMProvider | None:
    """Find provider definition by ID."""
    for provider in POPULAR_PROVIDERS:
        if provider.id == provider_id.lower():
            return provider
    return None


def load_config_data(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load configuration dictionary from JSON file."""
    path = get_config_file_path(config_path)
    if not path.exists():
        return {"active_provider": None, "active_model": None, "api_base": None, "providers": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active_provider": None, "active_model": None, "api_base": None, "providers": {}}


def save_config_data(data: dict[str, Any], config_path: Path | str | None = None) -> None:
    """Save configuration dictionary to JSON file."""
    path = get_config_file_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_provider_key(
    provider_id: str,
    api_key: str | None = None,
    model: str | None = None,
    api_base: str | None = None,
    set_active: bool = True,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Save API key and settings for a specific LLM provider."""
    provider = get_provider_by_id(provider_id)
    provider_name = provider.name if provider else provider_id
    env_var = provider.env_var if provider else f"{provider_id.upper()}_API_KEY"

    cfg = load_config_data(config_path)
    providers = cfg.get("providers", {})

    selected_model = model or (provider.default_model if provider else "gpt-4o-mini")
    selected_api_base = api_base or (provider.default_api_base if provider else None)

    providers[provider_id] = {
        "provider_id": provider_id,
        "name": provider_name,
        "api_key": api_key,
        "model": selected_model,
        "api_base": selected_api_base,
        "env_var": env_var,
    }

    cfg["providers"] = providers
    if set_active:
        cfg["active_provider"] = provider_id
        cfg["active_model"] = selected_model
        cfg["api_base"] = selected_api_base

    save_config_data(cfg, config_path)

    # Also update current process environment variables immediately
    if env_var and api_key:
        os.environ[env_var] = api_key

    return cfg


def apply_saved_config(config_path: Path | str | None = None) -> tuple[str | None, str | None]:
    """
    Load saved provider configuration, inject API keys into environment variables,
    and return (active_model, active_api_base).
    """
    cfg = load_config_data(config_path)
    providers = cfg.get("providers", {})

    # Populate env vars for all configured providers
    for p_id, p_info in providers.items():
        env_var = p_info.get("env_var")
        api_key = p_info.get("api_key")
        if env_var and api_key and not os.environ.get(env_var):
            os.environ[env_var] = api_key

    active_model = cfg.get("active_model")
    active_api_base = cfg.get("api_base")

    return active_model, active_api_base


def verify_provider_connection(
    model: str, api_key: str | None = None, api_base: str | None = None
) -> tuple[bool, str]:
    """Test LLM provider connection using LiteLLM."""

    import litellm

    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base

        response = litellm.completion(**kwargs)
        content = response.choices[0].message.content
        return True, f"Success! LLM Response: '{content.strip()}'"
    except Exception as e:
        return False, f"Connection failed: {str(e)}"
