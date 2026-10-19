"""Typer-based CLI for appbuild."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
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

    os_name = _platform.system()  # Windows / Darwin / Linux
    found_flutter = None
    missing_hints = []

    # ---------- Flutter Ecosystem ----------
    flutter_table = Table(title="Flutter Ecosystem", show_lines=False)
    flutter_table.add_column("Component", style="cyan", width=22)
    flutter_table.add_column("Status", width=14)
    flutter_table.add_column("Details", style="dim")

    flutter_path = shutil.which("flutter")
    if flutter_path:
        found_flutter = flutter_path
        flutter_table.add_row("Flutter binary", "[green]✔ Found[/green]", flutter_path)
    else:
        # Check standard install directories.
        candidates = []
        home = Path.home()
        if os_name == "Windows":
            candidates = [
                Path("C:/src/flutter"),
                Path("C:/flutter"),
                Path("D:/flutter"),
                home / "development" / "flutter",
                home / "flutter",
            ]
        elif os_name == "Darwin":
            candidates = [
                home / "development" / "flutter",
                Path("/usr/local/flutter"),
                Path("/opt/flutter"),
            ]
        else:
            candidates = [
                home / "development" / "flutter",
                Path("/opt/flutter"),
                Path("/usr/local/flutter"),
            ]
        found_dir = None
        for c in candidates:
            flutter_bin = c / "bin" / ("flutter.bat" if os_name == "Windows" else "flutter")
            if flutter_bin.exists():
                found_dir = c
                break
        if found_dir:
            flutter_table.add_row("Flutter (standard dir)", "[yellow]⚠ Warning[/yellow]", f"Found at {found_dir} but not on PATH")
            missing_hints.append(f'Add to PATH: {found_dir / "bin"}')
        else:
            flutter_table.add_row("Flutter binary", "[red]✘ Missing[/red]", "flutter not found in PATH or standard directories")
            missing_hints.append("Install Flutter: https://docs.flutter.dev/get-started/install")

    dart_path = shutil.which("dart")
    if dart_path:
        flutter_table.add_row("Dart binary", "[green]✔ Found[/green]", dart_path)
    else:
        # Try alongside flutter.
        if flutter_path:
            sibling = Path(flutter_path).parent / ("dart.exe" if os_name == "Windows" else "dart")
            if sibling.exists():
                flutter_table.add_row("Dart binary", "[green]✔ Found[/green]", str(sibling))
            else:
                flutter_table.add_row("Dart binary", "[yellow]⚠ Warning[/yellow]", "dart not on PATH (bundled with Flutter)")
        else:
            flutter_table.add_row("Dart binary", "[red]✘ Missing[/red]", "dart not found")

    # Flutter version
    if flutter_path:
        code, out = _run("flutter --version", timeout=30)
        first_line = out.splitlines()[0] if out else ""
        if code == 0 and first_line:
            flutter_table.add_row("Flutter version", "[green]✔ Found[/green]", first_line)
        else:
            flutter_table.add_row("Flutter version", "[yellow]⚠ Warning[/yellow]", out[:120] or "Could not determine")
    else:
        flutter_table.add_row("Flutter version", "[red]✘ Missing[/red]", "N/A")

    console.print(flutter_table)

    # ---------- Android Toolchain ----------
    android_table = Table(title="Android Toolchain", show_lines=False)
    android_table.add_column("Component", style="cyan", width=22)
    android_table.add_column("Status", width=14)
    android_table.add_column("Details", style="dim")

    # Environment variables
    android_home_env = os.getenv("ANDROID_HOME")
    android_sdk_root_env = os.getenv("ANDROID_SDK_ROOT")
    sdk_root = None
    if android_home_env and Path(android_home_env).exists():
        sdk_root = android_home_env
        android_table.add_row("ANDROID_HOME", "[green]✔ Found[/green]", android_home_env)
    elif android_home_env:
        android_table.add_row("ANDROID_HOME", "[red]✘ Missing[/red]", f"Set but path does not exist: {android_home_env}")
    else:
        android_table.add_row("ANDROID_HOME", "[yellow]⚠ Warning[/yellow]", "Not set")

    if android_sdk_root_env and Path(android_sdk_root_env).exists():
        if sdk_root is None:
            sdk_root = android_sdk_root_env
        android_table.add_row("ANDROID_SDK_ROOT", "[green]✔ Found[/green]", android_sdk_root_env)
    elif android_sdk_root_env:
        android_table.add_row("ANDROID_SDK_ROOT", "[red]✘ Missing[/red]", f"Set but path does not exist: {android_sdk_root_env}")
    else:
        android_table.add_row("ANDROID_SDK_ROOT", "[yellow]⚠ Warning[/yellow]", "Not set")

    # Default locations
    if sdk_root is None:
        if os_name == "Windows":
            default_sdk = Path(os.getenv("LOCALAPPDATA", "")) / "Android" / "Sdk"
        elif os_name == "Darwin":
            default_sdk = Path.home() / "Library" / "Android" / "sdk"
        else:
            default_sdk = Path.home() / "Android" / "Sdk"
        if default_sdk.exists():
            sdk_root = str(default_sdk)
            android_table.add_row("Android SDK (default)", "[green]✔ Found[/green]", str(default_sdk))
        else:
            android_table.add_row("Android SDK (default)", "[red]✘ Missing[/red]", f"Not found at {default_sdk}")
            missing_hints.append(f"Install Android SDK (Android Studio) or set ANDROID_HOME to your SDK path")

    # adb
    adb_path = shutil.which("adb")
    if adb_path:
        android_table.add_row("adb", "[green]✔ Found[/green]", adb_path)
    elif sdk_root and Path(sdk_root, "platform-tools", "adb.exe" if os_name == "Windows" else "adb").exists():
        adb_candidate = Path(sdk_root, "platform-tools", "adb.exe" if os_name == "Windows" else "adb")
        android_table.add_row("adb", "[yellow]⚠ Warning[/yellow]", f"Found in SDK but not on PATH: {adb_candidate}")
        missing_hints.append(f'Add to PATH: {Path(sdk_root) / "platform-tools"}')
    else:
        android_table.add_row("adb", "[red]✘ Missing[/red]", "adb not found")
        missing_hints.append("Install Android platform-tools")

    # platform-tools and build-tools dirs
    if sdk_root and Path(sdk_root).exists():
        pt = Path(sdk_root) / "platform-tools"
        bt = Path(sdk_root) / "build-tools"
        if pt.exists():
            android_table.add_row("platform-tools dir", "[green]✔ Found[/green]", str(pt))
        else:
            android_table.add_row("platform-tools dir", "[red]✘ Missing[/red]", str(pt))
        if bt.exists():
            versions = [p.name for p in bt.iterdir() if p.is_dir()]
            detail = f"{bt} ({', '.join(versions[:5])})" if versions else str(bt)
            android_table.add_row("build-tools dir", "[green]✔ Found[/green]", detail)
        else:
            android_table.add_row("build-tools dir", "[red]✘ Missing[/red]", str(bt))

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
        android_table.add_row("Android Studio", "[green]✔ Found[/green]", str(studio_found))
    else:
        android_table.add_row("Android Studio", "[yellow]⚠ Warning[/yellow]", "Not found in default locations")

    # Java / JDK
    java_home = os.getenv("JAVA_HOME")
    if java_home and Path(java_home).exists():
        android_table.add_row("JAVA_HOME", "[green]✔ Found[/green]", java_home)
    elif java_home:
        android_table.add_row("JAVA_HOME", "[red]✘ Missing[/red]", f"Set but path does not exist: {java_home}")
    else:
        android_table.add_row("JAVA_HOME", "[yellow]⚠ Warning[/yellow]", "Not set")

    java_path = shutil.which("java")
    if java_path:
        android_table.add_row("java binary", "[green]✔ Found[/green]", java_path)
        code, out = _run("java -version", timeout=15)
        # java -version prints to stderr; capture combined.
        ver_line = (out.splitlines()[0] if out else "").strip()
        if ver_line:
            android_table.add_row("java version", "[green]✔ Found[/green]", ver_line)
    else:
        android_table.add_row("java binary", "[red]✘ Missing[/red]", "java not found in PATH")
        missing_hints.append("Install a JDK (e.g. Temurin) and set JAVA_HOME")

    console.print(android_table)

    # ---------- LLM Credentials ----------
    llm_table = Table(title="LLM Credentials", show_lines=False)
    llm_table.add_column("Component", style="cyan", width=22)
    llm_table.add_column("Status", width=14)
    llm_table.add_column("Details", style="dim")

    cfg = cfgmod.load_config()
    provider = cfg.get("provider_type", "openai")
    key = cfgmod.resolve_api_key(provider, None, cfg.get("api_key"))
    if key:
        llm_table.add_row("API key", "[green]✔ Found[/green]", f"{provider}: {cfgmod.mask_key(key)}")
    else:
        llm_table.add_row("API key", "[red]✘ Missing[/red]", "Configure via `appbuild config` or env vars")
        missing_hints.append("Run: appbuild config")

    if cfgmod.CONFIG_PATH.exists():
        llm_table.add_row("Config file", "[green]✔ Found[/green]", str(cfgmod.CONFIG_PATH))
    else:
        llm_table.add_row("Config file", "[yellow]⚠ Warning[/yellow]", "Using defaults / env vars")

    llm_table.add_row("Provider", "[green]✔ Found[/green]", provider)
    llm_table.add_row("Base URL", "[green]✔ Found[/green]", str(cfg.get("base_url")))
    llm_table.add_row("Model", "[green]✔ Found[/green]", str(cfg.get("model")))
    console.print(llm_table)

    # Hints
    if missing_hints:
        console.print("\n[bold yellow]Suggested fixes:[/bold yellow]")
        for h in missing_hints:
            console.print(f"  • {h}")
    else:
        console.print("\n[bold green]All critical components detected.[/bold green]")

@app.command()
def version():
    """Show appbuild version."""
    console.print(f"appbuild {__version__}")


if __name__ == "__main__":
    app()
