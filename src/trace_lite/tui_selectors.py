"""Reusable keyboard-first selectors for the interactive terminal flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from rich.prompt import Prompt


SELECT_CANCEL = "__trace_lite_cancel__"
SELECT_QUIT = "__trace_lite_quit__"


@dataclass(frozen=True)
class SelectorOption:
    """A displayed selector label and the value returned to its caller."""

    title: str
    value: Any


def _normalise_options(options: Iterable[Any]) -> list[SelectorOption]:
    normalised: list[SelectorOption] = []
    for option in options:
        if isinstance(option, SelectorOption):
            normalised.append(option)
        elif isinstance(option, tuple) and len(option) == 2:
            normalised.append(SelectorOption(str(option[0]), option[1]))
        else:
            normalised.append(SelectorOption(str(option), option))
    return normalised


def _same_value(left: Any, right: Any) -> bool:
    """Compare option values without requiring them to be hashable."""
    try:
        return bool(left == right)
    except Exception:
        return left is right


def _has_value(options: list[SelectorOption], value: Any) -> bool:
    return any(_same_value(option.value, value) for option in options)


def _fallback_select(message: str, options: list[SelectorOption], default: Any) -> Any:
    """Use a readable numbered prompt when a cursor terminal is unavailable."""
    default_index = next(
        (index for index, option in enumerate(options, 1) if _same_value(option.value, default)),
        1,
    )
    while True:
        lines = [f"  [{index}] {option.title}" for index, option in enumerate(options, 1)]
        prompt = f"{message}\n" + "\n".join(lines)
        answer = Prompt.ask(
            f"{prompt}\nChoose a number (Q to cancel)",
            default=str(default_index),
        ).strip()
        if answer.casefold() in {"q", "quit", "exit", "esc", "escape"}:
            return SELECT_QUIT if _has_value(options, SELECT_QUIT) else SELECT_CANCEL
        if answer.isdigit():
            index = int(answer)
            if 1 <= index <= len(options):
                return options[index - 1].value
        for option in options:
            if _same_value(answer, option.value) or answer.casefold() == option.title.casefold():
                return option.value


def select_option(message: str, options: Iterable[Any], *, default: Any = None) -> Any:
    """Select with Up/Down and Enter, with Escape/Q normalized to cancellation.

    Questionary's prompt-toolkit control owns viewport scrolling, so a model
    catalog can contain every eligible provider model without turning into a
    wall of numbered prompts.
    """
    choices = _normalise_options(options)
    if not choices:
        return SELECT_CANCEL

    try:
        import questionary
        if questionary is None:
            raise ImportError
    except (ImportError, ModuleNotFoundError):
        return _fallback_select(message, choices, default)

    questionary_choices = []
    for option in choices:
        shortcut = "q" if option.value in (SELECT_CANCEL, SELECT_QUIT) else None
        try:
            questionary_choices.append(
                questionary.Choice(option.title, value=option.value, shortcut_key=shortcut)
            )
        except TypeError:
            # Compatibility with older Questionary releases and lightweight
            # test doubles that do not accept shortcut_key.
            questionary_choices.append(questionary.Choice(option.title, value=option.value))

    kwargs = {
        "choices": questionary_choices,
        "default": next(
            (option.value for option in choices if _same_value(option.value, default)),
            choices[0].value,
        ),
        "instruction": "(Up/Down move, Enter select, Esc or Q cancel)",
        "use_shortcuts": True,
    }
    try:
        prompt = questionary.select(message, **kwargs)
    except TypeError:
        kwargs.pop("use_shortcuts", None)
        prompt = questionary.select(message, **kwargs)

    try:
        answer = prompt.ask()
    except (KeyboardInterrupt, EOFError):
        return SELECT_CANCEL
    if answer is None:
        return SELECT_CANCEL
    if isinstance(answer, str) and answer.casefold() in {"q", "quit", "escape", "esc"}:
        return SELECT_QUIT if _has_value(choices, SELECT_QUIT) else SELECT_CANCEL
    for option in choices:
        if _same_value(answer, option.value) or answer == option.title:
            return option.value
    return answer


select_from_list = select_option
choose_option = select_option
select_with_arrows = select_option


__all__ = [
    "SELECT_CANCEL",
    "SELECT_QUIT",
    "SelectorOption",
    "select_option",
    "select_from_list",
    "choose_option",
    "select_with_arrows",
]
