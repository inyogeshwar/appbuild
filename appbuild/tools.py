"""Tool implementations used by the appbuild agent."""

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()


def ask_user(question: str) -> str:
    """Ask the user a clarification question and return their answer."""
    console.print(Panel(question, title="[bold cyan]Question for you[/bold cyan]", border_style="cyan"))
    try:
        answer = Prompt.ask("[bold green]Your answer[/bold green]")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    return answer.strip()


def run_command(command: str, cwd: Optional[str] = None, timeout: int = 300) -> Dict[str, Any]:
    """Run a shell command and capture its output."""
    if not command or not isinstance(command, str):
        return {"exit_code": -1, "output": "Invalid command"}
    workdir = cwd or os.getcwd()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        output = proc.stdout or ""
        if proc.stderr:
            output = output + ("\n" if output else "") + proc.stderr
        return {"exit_code": proc.returncode, "output": output.strip()}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "output": f"Command timed out after {timeout}s: {command}"}
    except FileNotFoundError as exc:
        return {"exit_code": -1, "output": f"Command not found: {exc}"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"exit_code": -1, "output": f"Error running command: {exc}"}


def _resolve(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    return p


def write_file(path: str, content: str) -> Dict[str, Any]:
    """Write content to a file, creating parent directories as needed."""
    try:
        target = _resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content is not None else "", encoding="utf-8")
        return {"status": "ok", "path": str(target), "bytes": len(content or "")}
    except OSError as exc:
        return {"status": "error", "path": path, "error": str(exc)}


def read_file(path: str) -> Dict[str, Any]:
    """Read a file's contents."""
    try:
        target = _resolve(path)
        if not target.exists():
            return {"status": "error", "path": str(target), "error": "File not found"}
        return {"status": "ok", "path": str(target), "content": target.read_text(encoding="utf-8")}
    except OSError as exc:
        return {"status": "error", "path": path, "error": str(exc)}
