"""Interactive TUI for selecting LLM providers and setting API keys using LiteLLM."""

import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.text import Text

from trace_lite.providers import (
    POPULAR_PROVIDERS,
    LLMProvider,
    load_config_data,
    save_provider_key,
    verify_provider_connection,
    get_config_file_path,
)


def run_provider_tui(config_path: Path | str | None = None) -> None:
    """Run interactive TUI wizard to select an LLM provider and configure its API key."""
    console = Console()
    target_path = get_config_file_path(config_path)

    # 1. Header Banner
    header_text = Text()
    header_text.append("trace-lite ", style="bold cyan")
    header_text.append("LLM Provider Setup\n", style="bold white")
    header_text.append(
        "Powered by LiteLLM — Select provider, set API keys, and manage models.",
        style="dim white",
    )
    console.print(Panel(header_text, border_style="cyan", expand=False))

    while True:
        cfg = load_config_data(target_path)
        active_provider_id = cfg.get("active_provider")
        saved_providers = cfg.get("providers", {})

        # 2. Table of Providers
        table = Table(
            title="Available LLM Providers",
            header_style="bold magenta",
            show_header=True,
            expand=False,
        )
        table.add_column("#", style="bold yellow", justify="right")
        table.add_column("Status", justify="center")
        table.add_column("Provider Name", style="bold white")
        table.add_column("Default Model", style="green")
        table.add_column("Environment Variable", style="dim cyan")
        table.add_column("Description", style="dim white")

        for idx, provider in enumerate(POPULAR_PROVIDERS, 1):
            is_active = active_provider_id == provider.id
            is_saved = provider.id in saved_providers and (
                not provider.requires_api_key or bool(saved_providers[provider.id].get("api_key"))
            )

            if is_active:
                status = "[bold green]★ Active[/bold green]"
            elif is_saved:
                status = "[blue]✓ Ready[/blue]"
            else:
                status = "[dim]-[/dim]"

            env_display = provider.env_var if provider.env_var else "[dim]N/A (Local)[/dim]"

            table.add_row(
                str(idx),
                status,
                provider.name,
                provider.default_model,
                env_display,
                provider.description,
            )

        console.print(table)
        console.print()

        # 3. Prompt selection
        choice = Prompt.ask(
            "[bold yellow]Select provider number [1-10] (or 'q' to quit)[/bold yellow]",
            default="q",
        )

        if choice.lower() in ["q", "quit", "exit"]:
            console.print("[dim]Exiting LLM provider setup.[/dim]")
            break

        if not choice.isdigit() or not (1 <= int(choice) <= len(POPULAR_PROVIDERS)):
            console.print("[bold red]Invalid selection. Please enter a number between 1 and 10.[/bold red]\n")
            continue

        selected_provider: LLMProvider = POPULAR_PROVIDERS[int(choice) - 1]

        # 4. Detail Panel for Selected Provider
        console.print()
        console.print(
            Panel(
                f"[bold cyan]{selected_provider.name}[/bold cyan]\n"
                f"{selected_provider.description}\n"
                f"Env Var: [bold]{selected_provider.env_var or 'N/A'}[/bold]",
                title="Configuring Provider",
                border_style="yellow",
            )
        )

        # Existing saved info for this provider
        existing_info = saved_providers.get(selected_provider.id, {})
        existing_key = existing_info.get("api_key")
        existing_model = existing_info.get("model") or selected_provider.default_model
        existing_base = existing_info.get("api_base") or selected_provider.default_api_base

        # 5. Model Selection
        console.print("\n[bold]Popular Models:[/bold]")
        for m_idx, m_name in enumerate(selected_provider.popular_models, 1):
            marker = " (default)" if m_name == existing_model else ""
            console.print(f"  [{m_idx}] {m_name}{marker}")
        console.print(f"  [{len(selected_provider.popular_models) + 1}] Enter custom model string")

        model_choice = Prompt.ask(
            f"Select model [1-{len(selected_provider.popular_models) + 1}]",
            default="1",
        )

        selected_model = existing_model
        if model_choice.isdigit():
            m_num = int(model_choice)
            if 1 <= m_num <= len(selected_provider.popular_models):
                selected_model = selected_provider.popular_models[m_num - 1]
            elif m_num == len(selected_provider.popular_models) + 1:
                selected_model = Prompt.ask("Enter custom model name", default=selected_provider.default_model)

        # 6. API Key Input
        api_key = existing_key
        if selected_provider.requires_api_key:
            if existing_key:
                masked_key = existing_key[:4] + "..." + existing_key[-4:] if len(existing_key) > 8 else "****"
                console.print(f"[dim]Existing API key found: {masked_key}[/dim]")
                enter_key = Prompt.ask(
                    f"Enter API key for [bold]{selected_provider.name}[/bold] (leave blank to keep existing key)",
                    password=True,
                    default="",
                )
                if enter_key.strip():
                    api_key = enter_key.strip()
            else:
                api_key = Prompt.ask(
                    f"Enter API key for [bold]{selected_provider.name}[/bold] ({selected_provider.env_var})",
                    password=True,
                )

        # 7. API Base Input
        api_base = existing_base
        if selected_provider.requires_api_base or selected_provider.id == "custom":
            api_base = Prompt.ask(
                "Enter API Base URL",
                default=existing_base or "http://localhost:11434",
            )

        # 8. Test Connection Option
        if Confirm.ask("Would you like to test the API connection now?", default=True):
            with console.status("[bold green]Testing connection via LiteLLM...[/bold green]"):
                success, msg = verify_provider_connection(
                    model=selected_model,
                    api_key=api_key,
                    api_base=api_base,
                )
            if success:
                console.print(f"[bold green]✓ {msg}[/bold green]")
            else:
                console.print(f"[bold red]✗ {msg}[/bold red]")
                if not Confirm.ask("Do you still want to save this configuration?", default=True):
                    console.print("[yellow]Provider setup aborted.[/yellow]\n")
                    continue

        # 9. Save Provider Key and Settings
        save_provider_key(
            provider_id=selected_provider.id,
            api_key=api_key,
            model=selected_model,
            api_base=api_base,
            set_active=True,
            config_path=target_path,
        )

        console.print(
            f"\n[bold green]✓ Successfully configured {selected_provider.name}![/bold green]"
        )
        console.print(f"  Active Model: [bold]{selected_model}[/bold]")
        console.print(f"  Saved to: [dim]{target_path}[/dim]\n")

        if not Confirm.ask("Configure another provider?", default=False):
            break
