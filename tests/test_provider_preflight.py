"""Safe LiteLLM routing and provider verification categories."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from trace_lite.adapters.llm import (
    EmptyResponseError,
    EndpointUnavailableError,
    LiteLLMAdapter,
    RejectedCredentialError,
    TemporaryProviderError,
    UnsupportedModelError,
)
from trace_lite import providers


def _exception_type(name: str, base: type[Exception] = RuntimeError):
    return type(name, (base,), {})


def _fake_litellm(completion, route=None):
    return SimpleNamespace(
        get_llm_provider=route or (lambda *, model: (model.split("/", 1)[1], model.split("/", 1)[0], None, None)),
        completion=completion,
    )


def test_routing_rejection_happens_before_completion_and_is_quiet(monkeypatch, capsys):
    calls: list[str] = []
    bad_request = _exception_type("BadRequestError")

    def route(*, model):
        calls.append(f"route:{model}")
        raise bad_request("Provider List: openai, anthropic; api_key=sk-secret")

    def completion(**_kwargs):
        calls.append("completion")
        print("Provider List: leaked diagnostic")

    adapter = LiteLLMAdapter("gpt-5.6-luna-pro", api_key="sk-secret")
    monkeypatch.setattr(adapter, "_load_litellm", lambda: _fake_litellm(completion, route))

    with pytest.raises(UnsupportedModelError, match="provider-qualified"):
        adapter.preflight()

    assert calls == ["route:gpt-5.6-luna-pro"]
    output = capsys.readouterr()
    assert "Provider List" not in output.out + output.err
    assert "sk-secret" not in output.out + output.err
    assert "\x1b[" not in output.out + output.err


@pytest.mark.parametrize(
    ("error_name", "message", "expected", "phrase"),
    [
        ("AuthenticationError", "api_key=sk-secret rejected", RejectedCredentialError, "rejected the credential"),
        ("APIConnectionError", "response body contains sk-secret", EndpointUnavailableError, "endpoint is unavailable"),
        ("RateLimitError", "Provider List and sk-secret", TemporaryProviderError, "rate-limited"),
    ],
)
def test_provider_failures_are_categorized_without_raw_diagnostics(
    monkeypatch,
    capsys,
    error_name,
    message,
    expected,
    phrase,
):
    error_type = _exception_type(error_name)

    def completion(**_kwargs):
        print("\x1b[31mProvider List: raw diagnostic\x1b[0m")
        raise error_type(message)

    adapter = LiteLLMAdapter("openai/gpt-5.6-luna-pro", api_key="sk-secret")
    monkeypatch.setattr(adapter, "_load_litellm", lambda: _fake_litellm(completion))

    with pytest.raises(expected, match=phrase):
        adapter.preflight()

    output = capsys.readouterr()
    assert "Provider List" not in output.out + output.err
    assert "sk-secret" not in output.out + output.err
    assert "\x1b[" not in output.out + output.err


def test_empty_response_is_actionable_and_safe(monkeypatch):
    def completion(**_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="  "),
                    finish_reason="length",
                )
            ]
        )

    adapter = LiteLLMAdapter("openai/gpt-5.6-luna-pro", api_key="sk-secret")
    monkeypatch.setattr(adapter, "_load_litellm", lambda: _fake_litellm(completion))

    with pytest.raises(EmptyResponseError, match="no visible response") as caught:
        adapter.preflight()
    assert caught.value.finish_reason == "length"


def test_deepseek_short_visible_completion_disables_thinking(monkeypatch):
    seen = {}

    def completion(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Knowledge Systems"),
                    finish_reason="stop",
                )
            ]
        )

    adapter = LiteLLMAdapter("deepseek/deepseek-v4-flash", api_key="sk-secret")
    monkeypatch.setattr(adapter, "_load_litellm", lambda: _fake_litellm(completion))

    assert adapter.complete("Return a short title.", max_tokens=128) == "Knowledge Systems"
    assert seen["extra_body"]["thinking"] == {"type": "disabled"}


def test_deepseek_explicit_thinking_mode_is_preserved(monkeypatch):
    seen = {}

    def completion(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Knowledge Systems"),
                    finish_reason="stop",
                )
            ]
        )

    adapter = LiteLLMAdapter(
        "deepseek/deepseek-v4-flash",
        extra_body={"thinking": {"type": "enabled"}},
    )
    monkeypatch.setattr(adapter, "_load_litellm", lambda: _fake_litellm(completion))

    assert adapter.complete("Return a short title.", max_tokens=128) == "Knowledge Systems"
    assert seen["extra_body"]["thinking"] == {"type": "enabled"}


def test_reasoning_only_response_is_accepted_for_preflight(monkeypatch):
    seen = {}

    def completion(**_kwargs):
        seen.update(_kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, reasoning_content="OK")
                )
            ]
        )

    adapter = LiteLLMAdapter("deepseek/deepseek-v4-flash", api_key="sk-secret")
    monkeypatch.setattr(adapter, "_load_litellm", lambda: _fake_litellm(completion))

    adapter.preflight()
    assert seen["max_tokens"] == 128


def test_verify_runtime_returns_only_safe_category_message(monkeypatch, capsys):
    error_type = _exception_type("AuthenticationError")

    def completion(**_kwargs):
        print("Provider List: api_key=sk-secret response-body=secret-body")
        raise error_type("api_key=sk-secret response-body=secret-body")

    adapter = LiteLLMAdapter("openai/gpt-5.6-luna-pro", api_key="sk-secret")
    monkeypatch.setattr(adapter, "_load_litellm", lambda: _fake_litellm(completion))
    monkeypatch.setattr(providers, "LiteLLMAdapter", lambda **_kwargs: adapter)

    ok, message = providers._verify_runtime("openai/gpt-5.6-luna-pro", "sk-secret", None)

    assert ok is False
    assert message == "Provider rejected the credential. Check the API key and try again."
    output = capsys.readouterr()
    assert "Provider List" not in output.out + output.err
    assert "sk-secret" not in output.out + output.err
    assert "secret-body" not in output.out + output.err
