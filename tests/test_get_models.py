import json
from pathlib import Path

import pytest

import get_models
from get_models import (
    OpenRouterRefreshError,
    build_popular_providers_from_openrouter,
    refresh,
    update_models_json,
)


def _registry() -> list[dict]:
    return json.loads((Path(__file__).parents[1] / "src" / "trace_lite" / "models.json").read_text(encoding="utf-8"))


def _provider(registry: list[dict], provider_id: str) -> dict:
    return next(item for item in registry if item["id"] == provider_id)


def _model(model_id: str, created: int = 0, **extra) -> dict:
    return {
        "id": model_id,
        "created": created,
        "architecture": {"output_modalities": ["text"]},
        **extra,
    }


def test_refresh_changes_only_live_mapped_model_lists():
    original = _registry()
    refreshed = build_popular_providers_from_openrouter([
        _model("meta-llama/new"),
        _model("qwen/new"),
        _model("openai/not-allowed"),
    ], registry=original)
    assert [item["id"] for item in refreshed] == [item["id"] for item in original]
    assert _provider(refreshed, "meta-llama")["default_model"] == "openrouter/meta-llama/new"
    assert _provider(refreshed, "openai")["default_model"] == "openai/not-allowed"
    assert _provider(refreshed, "azure") == _provider(original, "azure")


def test_novel_catalog_ids_are_used_without_source_changes():
    original = _registry()
    refreshed = build_popular_providers_from_openrouter(
        [_model("openai/catalog-novel", 100), _model("openai/catalog-newer", 200)],
        registry=original,
    )
    assert _provider(refreshed, "openai")["popular_models"] == ["openai/catalog-newer", "openai/catalog-novel"]


def test_newest_models_order_ties_and_free_models_are_deterministic():
    records = [
        _model(f"openai/model-{index}", 10 if index < 2 else index)
        for index in range(9)
    ]
    records.extend(
        [
            _model("openai/embedding-new", 100, architecture={"output_modalities": ["embedding"]}),
            _model("openai/vision-new", 100, architecture={"output_modalities": ["image"]}),
            _model("openai/speech-new", 100, architecture={"output_modalities": ["audio"]}),
            _model("openai/safe:batch", 100),
            _model("openai/free-new:free", 101),
        ]
    )
    refreshed = build_popular_providers_from_openrouter(records, registry=_registry())
    assert _provider(refreshed, "openai")["popular_models"] == [
        "openai/free-new:free",
        "openai/model-0",
        "openai/model-1",
        "openai/model-8",
        "openai/model-7",
        "openai/model-6",
        "openai/model-5",
        "openai/model-4",
        "openai/model-3",
        "openai/model-2",
    ]


def test_all_mapped_providers_refresh_and_endpoint_presets_are_unchanged():
    original = _registry()
    records = []
    for entry in original:
        if entry["openrouter_refreshable"] and entry["openrouter_prefixes"] != ["*"]:
            records.append(_model(f"{entry['openrouter_prefixes'][0]}catalog-novel", 100))
    records.append(_model("catalog/novel", 100))
    refreshed = build_popular_providers_from_openrouter(records, registry=original)

    for entry in original:
        current = _provider(refreshed, entry["id"])
        if entry["openrouter_refreshable"]:
            assert current["popular_models"]
            assert current["default_model"] == current["popular_models"][0]
        else:
            assert current == entry


def test_empty_provider_matches_preserve_the_last_known_good_snapshot():
    original = _registry()
    refreshed = build_popular_providers_from_openrouter(
        [_model("unmatched/novel")], registry=original
    )
    for provider_id in ("openai", "anthropic", "meta-llama", "minimax"):
        assert _provider(refreshed, provider_id) == _provider(original, provider_id)


def test_direct_provider_models_are_always_litellm_qualified():
    original = _registry()
    records = [
        _model("openai/catalog-model", 3),
        _model("anthropic/catalog-model", 2),
        _model("cohere/catalog-model", 1),
    ]
    refreshed = build_popular_providers_from_openrouter(records, registry=original)
    for provider_id in ("openai", "anthropic", "cohere"):
        provider = _provider(refreshed, provider_id)
        assert provider["default_model"].startswith(f"{provider_id}/")
        assert all(model.startswith(f"{provider_id}/") for model in provider["popular_models"])


def test_openrouter_models_keep_the_openrouter_prefix():
    refreshed = build_popular_providers_from_openrouter(
        [_model("vendor/model-new", 10)], registry=_registry()
    )
    assert _provider(refreshed, "openrouter")["popular_models"] == [
        "openrouter/vendor/model-new"
    ]


def test_refresh_keeps_the_complete_catalog_for_standard_and_openrouter():
    records = [_model(f"openai/model-{index}", index) for index in range(20)]
    records.extend(_model(f"vendor/model-{index}", index) for index in range(30))
    refreshed = build_popular_providers_from_openrouter(records, registry=_registry())
    assert len(_provider(refreshed, "openai")["popular_models"]) == 20
    assert len(_provider(refreshed, "openrouter")["popular_models"]) == 50
    assert _provider(refreshed, "openai")["popular_models"][0] == "openai/model-19"
    assert _provider(refreshed, "openrouter")["popular_models"][0] == "openrouter/vendor/model-29"


def test_fetch_sends_bearer_key_and_never_exposes_it_on_failure():
    secret = "sk-test-secret-never-print"
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"data":[{"id":"openai/novel"}]}'

    def opener(request, timeout):
        seen["authorization"] = request.headers["Authorization"]
        seen["timeout"] = timeout
        return Response()

    assert get_models.fetch_openrouter_models(secret, opener=opener)[0]["id"] == "openai/novel"
    assert seen == {"authorization": f"Bearer {secret}", "timeout": 15}

    def failing_opener(_request, **_kwargs):
        raise RuntimeError(f"request failed with {secret}")

    with pytest.raises(OpenRouterRefreshError) as error:
        get_models.fetch_openrouter_models(secret, opener=failing_opener)
    assert secret not in str(error.value)


def test_masked_prompt_precedes_network_and_blank_prompt_aborts(monkeypatch, capsys):
    events = []
    secret = "sk-prompt-secret"
    monkeypatch.setattr(get_models.getpass, "getpass", lambda _prompt: events.append("prompt") or secret)
    monkeypatch.setattr(
        get_models,
        "refresh",
        lambda *, output_path, api_key: events.append(("network", api_key)) or (2, 17),
    )
    assert get_models.main(["--output", "models.json"]) == 0
    assert events == ["prompt", ("network", secret)]
    assert secret not in capsys.readouterr().out

    events.clear()
    monkeypatch.setattr(get_models.getpass, "getpass", lambda _prompt: events.append("prompt") or "")
    monkeypatch.setattr(get_models, "refresh", lambda **_kwargs: events.append("network"))
    assert get_models.main(["--output", "models.json"]) == 1
    assert events == ["prompt"]
    assert secret not in capsys.readouterr().err


def test_cli_rejects_secret_flags_and_does_not_read_environment(monkeypatch, capsys):
    secret = "sk-environment-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    monkeypatch.setattr(get_models.getpass, "getpass", lambda _prompt: pytest.fail("prompt should not run"))
    assert get_models.main(["--key", secret]) == 2
    output = capsys.readouterr().err
    assert secret not in output
    assert "not accepted" in output


def test_invalid_catalog_or_validation_failure_keeps_output_bytes(tmp_path: Path, monkeypatch):
    target = tmp_path / "models.json"
    target.write_text(json.dumps(_registry(), indent=2) + "\n", encoding="utf-8")
    before = target.read_bytes()

    monkeypatch.setattr(get_models, "fetch_openrouter_models", lambda _key: [{"id": None}])
    with pytest.raises(OpenRouterRefreshError):
        refresh(target, api_key="sk-key")
    assert target.read_bytes() == before

    target.write_bytes(b"not-json")
    before = target.read_bytes()
    with pytest.raises(OpenRouterRefreshError):
        refresh(target, api_key="sk-key")
    assert target.read_bytes() == before

    target.write_text(json.dumps(_registry(), indent=2) + "\n", encoding="utf-8")
    before = target.read_bytes()
    monkeypatch.setattr(get_models, "fetch_openrouter_models", lambda _key: [_model("openai/novel")])
    monkeypatch.setattr(get_models, "build_popular_providers_from_openrouter", lambda *_args, **_kwargs: [])
    with pytest.raises(OpenRouterRefreshError):
        refresh(target, api_key="sk-key")
    assert target.read_bytes() == before


def test_update_requires_the_fixed_registry_and_writes_atomically(tmp_path: Path):
    target = tmp_path / "models.json"
    registry = _registry()
    update_models_json(registry, target)
    assert json.loads(target.read_text(encoding="utf-8")) == registry
