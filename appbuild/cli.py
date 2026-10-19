"""Typer-based CLI for appbuild."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from . import agent as agent_module
from . import config as cfgmod

app = typer.Typer(
    name="appbuild",
    help="Autonomous Flutter app generator powered by LLMs.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _resolve_settings(provider, base_url, api_key, model):
    cfg = cfgmod.load_config()
    provider = provider or cfg.get("provider_type", "openai")
    provider = provider.lower()
    if provider not in cfgmod.VALID_PROVIDERS:
        console.print(f"[red]Invalid provider '{provider}'. Use 'openai' or 'anthropic'.[/red]")
        raise typer.Exit(code=1)

    if base_url is None:
        base_url = cfg.get("base_url")
    if model is None:
        model = cfg.get("model")
    if api_key is None:
        api_key = cfgmod.resolve_api_key(provider, None, cfg.get("api_key"))

    if base_url is None or not str(base_url).strip():
        base_url, default_model = cfgmod.defaults_for_provider(provider)
        if model is None:
            model = default_model
    if model is None or not str(model).strip():
        _, model = cfgmod.defaults_for_provider(provider)
    if base_url is None or not str(base_url).strip():
        base_url, _ = cfgmod.defaults_for_provider(provider)

    if not api_key:
        console.print(
            "[red]No API key found.[/red] Set one via `appbuild config`, "
            "or env vars (APPBUILD_API_KEY / OPENROUTER_API_KEY / ANTHROPIC_API_KEY)."
        )
        raise typer.Exit(code=1)
    return provider, base_url, api_key, model


@app.command()
def new(
    idea: Optional[str] = typer.Argument(None, help="High-level app idea."),
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Provider type: openai or anthropic."),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="API base URL."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="API key."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model name."),
):
    """Plan, clarify, and build a Flutter project from an idea."""
    if not idea:
        idea = typer.prompt("Describe the app you want to build")
    provider, base_url, api_key, model = _resolve_settings(provider, base_url, api_key, model)

    console.print(f"[bold]Provider:[/bold] {provider}")
    console.print(f"[bold]Base URL:[/bold] {base_url}")
    console.print(f"[bold]Model:[/bold] {model}")
    console.print(f"[bold]Idea:[/bold] {idea}\n")

    client = agent_module.LLMClient(provider, base_url, api_key, model)
    agent = agent_module.Agent(client, console=console)
    try:
        agent.run(idea)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        raise typer.Exit(code=130)


@app.command()
def config(
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Provider type: openai or anthropic."),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="API base URL."),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="API key."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model name."),
    show: bool = typer.Option(False, "--show", help="Show current configuration."),
):
    """Configure and persist appbuild settings in ~/.appbuild/config.json."""
    cfg = cfgmod.load_config()

    if show:
        table = Table(title="appbuild configuration")
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("provider_type", str(cfg.get("provider_type")))
        table.add_row("base_url", str(cfg.get("base_url")))
        table.add_row("api_key", cfgmod.mask_key(cfg.get("api_key")))
        table.add_row("model", str(cfg.get("model")))
        console.print(table)
        return

    if provider is None:
        provider = typer.prompt("provider_type (openai/anthropic)", default=cfg.get("provider_type", "openai"))
    provider = (provider or "").lower()
    if provider not in cfgmod.VALID_PROVIDERS:
        console.print(f"[red]Invalid provider '{provider}'.[/red]")
        raise typer.Exit(code=1)

    if base_url is None:
        default_url, _ = cfgmod.defaults_for_provider(provider)
        base_url = typer.prompt("base_url", default=cfg.get("base_url", default_url))
    if model is None:
        _, default_model = cfgmod.defaults_for_provider(provider)
        model = typer.prompt("model", default=cfg.get("model", default_model))
    if api_key is None:
        api_key = typer.prompt(
            "api_key (leave blank to keep current / use env)",
            default="",
            show_default=False,
        )
        if not api_key.strip():
            api_key = cfg.get("api_key")

    new_cfg = {
        "provider_type": provider,
        "base_url": base_url.strip() if isinstance(base_url, str) else base_url,
        "api_key": api_key.strip() if isinstance(api_key, str) and api_key.strip() else (api_key if api_key else None),
        "model": model.strip() if isinstance(model, str) else model,
    }
    cfgmod.save_config(new_cfg)
    console.print("[green]Configuration saved to ~/.appbuild/config.json[/green]")
    console.print(f"  provider_type: {new_cfg['provider_type']}")
    console.print(f"  base_url:      {new_cfg['base_url']}")
    console.print(f"  model:         {new_cfg['model']}")
    console.print(f"  api_key:       {cfgmod.mask_key(new_cfg['api_key'])}")


@app.command()
def doctor():
    """Exhaustive diagnostic check for Flutter, Android SDK, toolchains, and LLM credentials."""
    import platform as _platform

    def _run(cmd, timeout=15):
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            out = (proc.stdout or "") + (proc.stderr or "")
            return proc.returncode, out.strip()
        except Exception as exc:
            return -1, str(exc)

    os_name = _platform.system()
    missing_hints = []

    # ---------- Flutter & Dart Ecosystem ----------
    flutter_lines = []
    flutter_path = shutil.which("flutter")

    if flutter_path:
        code, out = _run("flutter --version", timeout=30)
        first_line = out.splitlines()[0] if out else ""
        version_token = ""
        for token in first_line.split():
            if token and token[0].isdigit():
                version_token = token
                break
        v_suffix = f" (v{version_token})" if version_token else ""
        flutter_lines.append(f"[green]✔[/green] Flutter SDK   : {flutter_path}{v_suffix}")
    else:
        candidates = []
        home = Path.home()
        if os_name == "Windows":
            candidates = [Path("C:/src/flutter"), Path("C:/flutter"), Path("D:/flutter"), home / "development" / "flutter", home / "flutter"]
        elif os_name == "Darwin":
            candidates = [home / "development" / "flutter", Path("/usr/local/flutter"), Path("/opt/flutter")]
        else:
            candidates = [home / "development" / "flutter", Path("/opt/flutter"), Path("/usr/local/flutter")]
        found_dir = None
        for c in candidates:
            flutter_bin = c / "bin" / ("flutter.bat" if os_name == "Windows" else "flutter")
            if flutter_bin.exists():
                found_dir = c
                break
        if found_dir:
            flutter_lines.append(f"[yellow]⚠[/yellow] Flutter SDK   : {found_dir} (not on PATH)")
            missing_hints.append(f'Add to PATH: {found_dir / "bin"}')
        else:
            flutter_lines.append("[red]✘[/red] Flutter SDK   : Not found")
            missing_hints.append("Install Flutter: https://docs.flutter.dev/get-started/install")

    # Dart SDK
    dart_path = shutil.which("dart")
    dart_sdk_dir = None
    if flutter_path:
        try:
            dart_sdk_dir = Path(flutter_path).resolve().parent.parent / "bin" / "cache" / "dart-sdk"
        except Exception:
            dart_sdk_dir = None
    if dart_sdk_dir and dart_sdk_dir.exists():
        flutter_lines.append(f"[green]✔[/green] Dart SDK      : {dart_sdk_dir}")
    elif dart_path:
        flutter_lines.append(f"[green]✔[/green] Dart SDK      : {Path(dart_path).parent}")
    else:
        if flutter_path:
            flutter_lines.append("[yellow]⚠[/yellow] Dart SDK      : bundled with Flutter (not on PATH)")
        else:
            flutter_lines.append("[red]✘[/red] Dart SDK      : Not found")

    console.print(Panel("\n".join(flutter_lines), title="Flutter & Dart Ecosystem", border_style="cyan"))

    # ---------- Android Toolchain & Studio ----------
    android_lines = []

    # Android Studio
    studio_candidates = []
    if os_name == "Windows":
        studio_candidates = [
            Path("C:/Program Files/Android/Android Studio"),
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Android Studio",
        ]
    elif os_name == "Darwin":
        studio_candidates = [Path("/Applications/Android Studio.app")]
    else:
        studio_candidates = [Path("/opt/android-studio"), Path("/snap/android-studio")]
    studio_found = None
    for sc in studio_candidates:
        if sc.exists():
            studio_found = sc
            break
    if studio_found:
        android_lines.append(f"[green]✔[/green] Android Studio: {studio_found}")
    else:
        android_lines.append("[yellow]⚠[/yellow] Android Studio: Not found in default locations")

    # ANDROID_HOME / SDK
    android_home_env = os.getenv("ANDROID_HOME")
    android_sdk_root_env = os.getenv("ANDROID_SDK_ROOT")
    sdk_root = None
    if android_home_env and Path(android_home_env).exists():
        sdk_root = android_home_env
    elif android_sdk_root_env and Path(android_sdk_root_env).exists():
        sdk_root = android_sdk_root_env
    else:
        if os_name == "Windows":
            default_sdk = Path(os.getenv("LOCALAPPDATA", "")) / "Android" / "Sdk"
        elif os_name == "Darwin":
            default_sdk = Path.home() / "Library" / "Android" / "sdk"
        else:
            default_sdk = Path.home() / "Android" / "Sdk"
        if default_sdk.exists():
            sdk_root = str(default_sdk)

    if sdk_root:
        android_lines.append(f"[green]✔[/green] ANDROID_HOME  : {sdk_root}")
    else:
        android_lines.append("[red]✘[/red] ANDROID_HOME  : Not found")
        missing_hints.append("Install Android SDK or set ANDROID_HOME")

    # adb
    adb_path = shutil.which("adb")
    if adb_path:
        android_lines.append(f"[green]✔[/green] ADB Tool      : {adb_path}")
    elif sdk_root and Path(sdk_root, "platform-tools", "adb.exe" if os_name == "Windows" else "adb").exists():
        adb_candidate = Path(sdk_root, "platform-tools", "adb.exe" if os_name == "Windows" else "adb")
        android_lines.append(f"[yellow]⚠[/yellow] ADB Tool      : {adb_candidate} (not on PATH)")
        missing_hints.append(f'Add to PATH: {Path(sdk_root) / "platform-tools"}')
    else:
        android_lines.append("[red]✘[/red] ADB Tool      : Not found")
        missing_hints.append("Install Android platform-tools")

    # Java
    java_home = os.getenv("JAVA_HOME")
    java_path = shutil.which("java")
    code, out = _run("java -version", timeout=15)
    java_ver = ""
    if out:
        for line in out.splitlines():
            if "version" in line.lower():
                java_ver = line.strip()
                break
    if not java_ver and out:
        java_ver = out.splitlines()[0].strip()
    java_location = java_path or (str(Path(java_home) / "bin" / ("java.exe" if os_name == "Windows" else "java")) if java_home else "")
    if java_path or (java_home and Path(java_home).exists()):
        detail = java_ver if java_ver else (java_location or "installed")
        if java_location and java_location not in detail:
            detail = f"{detail} ({java_location})"
        android_lines.append(f"[green]✔[/green] Java Runtime  : {detail}")
    else:
        android_lines.append("[red]✘[/red] Java Runtime  : Not found")
        missing_hints.append("Install a JDK (e.g. Temurin) and set JAVA_HOME")

    console.print(Panel("\n".join(android_lines), title="Android Toolchain & Studio", border_style="cyan"))

    # ---------- Config & API Provider ----------
    cfg = cfgmod.load_config()
    provider = cfg.get("provider_type", "openai")
    key = cfgmod.resolve_api_key(provider, None, cfg.get("api_key"))
    config_lines = []
    if key:
        config_lines.append(f"[green]✔[/green] API Key       : {cfgmod.mask_key(key)}")
    else:
        config_lines.append("[red]✘[/red] API Key       : Not found")
        missing_hints.append("Run: appbuild config")
    config_lines.append(f"[green]✔[/green] Provider      : {provider} ({cfg.get('base_url')})")
    config_lines.append(f"[green]✔[/green] Default Model : {cfg.get('model')}")
    console.print(Panel("\n".join(config_lines), title="Config & API Provider", border_style="cyan"))

    if missing_hints:
        console.print("\n[bold yellow]Suggested fixes:[/bold yellow]")
        for h in missing_hints:
            console.print(f"  • {h}")
    else:
        console.print("\n[bold green]All critical components detected.[/bold green]")


@app.command()
def uninstall(
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm without prompt."),
    keep_config: bool = typer.Option(False, "--keep-config", help="Keep ~/.appbuild configuration."),
):
    """Uninstall appbuild and remove its local configuration."""
    if not yes:
        if not typer.confirm("Remove appbuild config and show uninstall steps?"):
            raise typer.Exit()
    if not keep_config and cfgmod.CONFIG_DIR.exists():
        shutil.rmtree(cfgmod.CONFIG_DIR, ignore_errors=True)
        console.print(f"[green]Removed configuration directory:[/green] {cfgmod.CONFIG_DIR}")
    elif not keep_config:
        console.print(f"[dim]No configuration directory found at {cfgmod.CONFIG_DIR}[/dim]")

    console.print("\n[bold yellow]To fully uninstall the package, run:[/bold yellow]")
    console.print("  [cyan]pip uninstall appbuild -y[/cyan]")
    console.print("\n[dim]If 'appbuild' is still available after that, remove appbuild.exe")
    console.print("[dim]from your Python Scripts folder or restart the terminal.[/dim]")


@app.command()
def version():
    """Show appbuild version."""
    console.print(f"appbuild {__version__}")


if __name__ == "__main__":
    app()
