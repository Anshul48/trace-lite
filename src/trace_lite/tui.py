"""Interactive provider setup with keyboard-first navigation."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

from trace_lite.providers import (
    POPULAR_PROVIDERS,
    LLMProvider,
    ProviderConfigurationError,
    credential_store_label,
    get_config_file_path,
    load_config_data,
    save_provider_key,
)
from trace_lite.tui_selectors import SELECT_CANCEL, SELECT_QUIT, select_option


_CUSTOM_MODEL = "__trace_lite_custom_model__"


def _provider_choice_title(
    provider: LLMProvider,
    active_provider_id: str | None,
    saved_providers: dict,
) -> str:
    """Build a compact status label for the single provider directory."""
    if provider.id == active_provider_id:
        status = "[active]"
    else:
        info = saved_providers.get(provider.id, {})
        state = str(info.get("credential_state") or "")
        status = "[ready]" if state in {"available", "not_required"} else "[setup]"
    return f"{status} {provider.name} | {provider.id}"


def _model_choices(provider: LLMProvider, existing_model: str) -> list[tuple[str, str]]:
    """Return every catalog model once, followed by custom and cancel rows."""
    seen: set[str] = set()
    choices: list[tuple[str, str]] = []
    for raw_model in provider.popular_models:
        model = str(raw_model).strip()
        if not model or model in seen:
            continue
        seen.add(model)
        suffix = " (current)" if model == existing_model else ""
        choices.append((f"{model}{suffix}", model))
    choices.extend(
        [
            ("Enter custom model string", _CUSTOM_MODEL),
            ("Cancel", SELECT_CANCEL),
        ]
    )
    return choices


def run_provider_tui(config_path: Path | str | None = None) -> None:
    """Select, verify, and activate a provider without exposing credentials."""
    console = Console()
    store_name = credential_store_label()
    target_path = get_config_file_path(config_path)

    header = Text()
    header.append("trace-lite ", style="bold cyan")
    header.append("Provider Setup\n", style="bold white")
    header.append(
        f"Choose a provider and model. Keys remain in {store_name}.",
        style="dim white",
    )
    console.print(Panel(header, border_style="cyan", expand=False))

    while True:
        cfg = load_config_data(target_path)
        active_provider_id = cfg.get("active_provider")
        saved_providers = cfg.get("providers", {})
        active_provider = next(
            (provider for provider in POPULAR_PROVIDERS if provider.id == active_provider_id),
            None,
        )

        if active_provider is None:
            summary = "No provider is active. Select one below to verify it."
            border = "yellow"
        else:
            current_model = cfg.get("active_model") or active_provider.default_model
            summary = (
                f"[bold green]{escape(active_provider.name)}[/bold green]  "
                f"[dim]{escape(str(current_model))}[/dim]"
            )
            border = "green"
        console.print(Panel(summary, title="Current configuration", border_style=border, expand=False))

        provider_options = [
            (_provider_choice_title(provider, active_provider_id, saved_providers), provider.id)
            for provider in POPULAR_PROVIDERS
        ]
        provider_options.append(("Quit", SELECT_QUIT))
        provider_ids = {provider.id for provider in POPULAR_PROVIDERS}
        provider_choice = select_option(
            "Select provider",
            provider_options,
            default=active_provider_id if active_provider_id in provider_ids else None,
        )
        if provider_choice in {SELECT_CANCEL, SELECT_QUIT}:
            console.print("[dim]Exiting provider setup.[/dim]")
            break

        selected_provider = next(
            (provider for provider in POPULAR_PROVIDERS if provider.id == provider_choice),
            None,
        )
        if selected_provider is None:
            console.print("[bold red]Invalid provider selection.[/bold red]\n")
            continue

        existing_info = saved_providers.get(selected_provider.id, {})
        has_existing_key = bool(existing_info.get("has_api_key"))
        existing_model = existing_info.get("model") or selected_provider.default_model
        existing_base = existing_info.get("api_base") or selected_provider.default_api_base
        existing_version = existing_info.get("api_version") or selected_provider.default_api_version
        model_choices = _model_choices(selected_provider, str(existing_model))
        catalog_count = len(model_choices) - 2

        console.print()
        provider_panel = Text()
        provider_panel.append(f"{selected_provider.name}\n", style="bold cyan")
        provider_panel.append(f"{selected_provider.description}\n", style="white")
        provider_panel.append(f"Credential: {store_name}\n", style="dim white")
        provider_panel.append(f"Models in catalog: {catalog_count}", style="dim white")
        console.print(Panel(provider_panel, title="Configuring Provider", border_style="yellow"))

        model_choice = select_option(
            "Select model",
            model_choices,
            default=(
                str(existing_model)
                if str(existing_model) in {value for _, value in model_choices}
                else _CUSTOM_MODEL
            ),
        )
        if model_choice in {SELECT_CANCEL, SELECT_QUIT}:
            continue
        selected_model = (
            Prompt.ask("Enter custom model name", default=str(existing_model)).strip()
            if model_choice == _CUSTOM_MODEL
            else str(model_choice).strip()
        )

        # API-key, endpoint, version, and confirmation prompts intentionally
        # remain free-form so pasted values and existing workflows behave the
        # same as before.
        api_key = None
        if selected_provider.requires_api_key:
            if has_existing_key:
                console.print(f"[dim]A credential is already stored in {store_name}.[/dim]")
                entered_key = Prompt.ask(
                    f"Enter a new API key for [bold]{selected_provider.name}[/bold] (leave blank to keep existing credential)",
                    password=True,
                    default="",
                )
                if entered_key.strip():
                    api_key = entered_key.strip()
            else:
                api_key = Prompt.ask(
                    f"Enter API key for [bold]{selected_provider.name}[/bold]",
                    password=True,
                )

        api_base = existing_base
        if selected_provider.requires_api_base or selected_provider.id == "custom":
            api_base = Prompt.ask(
                "Enter API Base URL",
                default=existing_base or "http://localhost:11434",
            )

        api_version = existing_version
        if selected_provider.requires_api_version:
            api_version = Prompt.ask("Enter Azure API version", default=existing_version or "")

        try:
            with console.status("[bold green]Saving and verifying provider...[/bold green]"):
                save_provider_key(
                    provider_id=selected_provider.id,
                    api_key=api_key,
                    model=selected_model,
                    api_base=api_base,
                    api_version=api_version,
                    set_active=True,
                    config_path=target_path,
                )
        except ProviderConfigurationError as exc:
            console.print(f"[bold red][FAIL] Provider was not activated: {escape(str(exc))}[/bold red]\n")
            continue
        except Exception as exc:
            console.print(
                f"[bold red][FAIL] Provider was not activated ({type(exc).__name__}).[/bold red]\n"
            )
            continue

        console.print(f"\n[bold green][OK] Saved and verified {selected_provider.name}![/bold green]")
        console.print(f"  Active Model: [bold]{escape(selected_model)}[/bold]")
        console.print(f"  Credential: [dim]{store_name}[/dim]")
        console.print(f"  Metadata: [dim]{escape(str(target_path))}[/dim]\n")

        if not Confirm.ask("Configure another provider?", default=False):
            break
