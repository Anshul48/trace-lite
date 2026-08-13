"""Keyboard selector behavior and project-selection routing."""

from __future__ import annotations

import sys
import types

from trace_lite.tui_selectors import SELECT_CANCEL, SELECT_QUIT, select_option


def _questionary_double(answer):
    calls = {}

    class Choice:
        def __init__(self, title, value=None, shortcut_key=None):
            self.title = title
            self.value = value
            self.shortcut_key = shortcut_key

    class Prompt:
        def ask(self):
            return answer

    def select(message, **kwargs):
        calls["message"] = message
        calls.update(kwargs)
        return Prompt()

    return types.SimpleNamespace(Choice=Choice, select=select), calls


def test_selector_passes_values_defaults_and_keyboard_instruction(monkeypatch):
    questionary, calls = _questionary_double("openai")
    monkeypatch.setitem(sys.modules, "questionary", questionary)

    answer = select_option(
        "Select provider",
        [("OpenAI", "openai"), ("Quit", SELECT_QUIT)],
        default="openai",
    )

    assert answer == "openai"
    assert calls["default"] == "openai"
    assert calls["use_shortcuts"] is True
    assert "Up/Down" in calls["instruction"]
    assert [choice.value for choice in calls["choices"]] == ["openai", SELECT_QUIT]
    assert calls["choices"][1].shortcut_key == "q"


def test_selector_normalizes_escape_and_q_to_cancel_or_quit(monkeypatch):
    questionary, _calls = _questionary_double(None)
    monkeypatch.setitem(sys.modules, "questionary", questionary)
    assert select_option("Select model", [("Cancel", SELECT_CANCEL)]) == SELECT_CANCEL

    questionary, _calls = _questionary_double("q")
    monkeypatch.setitem(sys.modules, "questionary", questionary)
    assert select_option("Select action", [("Quit", SELECT_QUIT)]) == SELECT_QUIT


def test_selector_fallback_keeps_explicit_cancel(monkeypatch):
    monkeypatch.setitem(sys.modules, "questionary", None)
    monkeypatch.setattr("trace_lite.tui_selectors.Prompt.ask", lambda *_args, **_kwargs: "2")
    assert select_option("Select model", [("Custom", "custom"), ("Cancel", SELECT_CANCEL)]) == SELECT_CANCEL


def test_model_selector_keeps_every_catalog_model_and_appends_custom():
    from trace_lite.tui import _model_choices

    provider = types.SimpleNamespace(
        popular_models=[f"openai/model-{index}" for index in range(200)]
    )

    choices = _model_choices(provider, "openai/model-199")

    assert len(choices) == 202
    assert choices[0][1] == "openai/model-0"
    assert choices[-2] == ("Enter custom model string", "__trace_lite_custom_model__")
    assert choices[-1] == ("Cancel", SELECT_CANCEL)


def test_project_selection_uses_shared_selector_and_active_default(monkeypatch):
    import trace_lite.tui_projects as tui_projects

    seen = {}

    def fake_select(message, options, *, default=None):
        seen.update(message=message, options=options, default=default)
        return "alpha"

    monkeypatch.setattr(tui_projects, "select_option", fake_select)
    result = tui_projects._select_project(
        [{"name": "alpha"}, {"name": "beta"}], "alpha", "Select project"
    )

    assert result == "alpha"
    assert seen["default"] == "alpha"
    assert seen["options"][-1] == ("Cancel", SELECT_CANCEL)
