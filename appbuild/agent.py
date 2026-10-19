"""ReAct agent loop and universal LLM client adapter."""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
from rich.console import Console
from rich.panel import Panel

from . import tools

SYSTEM_PROMPT = """You are appbuild, an autonomous mobile app generator for Flutter.

You MUST follow this workflow strictly:

PHASE 1 - CLARIFICATION:
- If the user's idea is high-level (e.g. "todo app", "chat app"), call the ask_user tool FIRST.
- Ask 2-3 focused clarification questions in a single ask_user call: state management approach (Provider/Riverpod/Bloc), local vs cloud database, and color scheme / theme.
- Do NOT write any code until you have the user's answers.

PHASE 2 - SCAFFOLDING & CODE GENERATION:
- Derive a snake_case project name from the idea.
- Run `flutter create <project_name>` via run_command (cwd = current working directory).
- If additional packages are needed, run `flutter pub add <pkg>` via run_command with cwd set to the project directory.
- Use write_file to write COMPLETE, production-ready Dart code. No placeholder comments, no TODOs, no stubs.
- Structure code properly: models, services, providers/state, screens, widgets.

PHASE 3 - SELF-HEALING:
- After generating files, run `flutter analyze` via run_command inside the project directory (pass the project path as `cwd`).
- If errors or warnings are returned, read only the files reported by analyze, patch them with write_file, and re-run `flutter analyze` (pass `cwd`).
- Do NOT rewrite files that are already correct.
- Repeat until analysis passes cleanly (exit code 0 and no issues).

GENERAL RULES:
- Always use run_command for Flutter/SDK commands; never pretend a command ran.
- Use write_file for all disk writes; paths may be relative to the current working directory.
- When a tool returns an error, fix the cause and retry.
- Keep responses concise; summarize what was built at the end.
- Never include TODO or placeholder comments in generated Dart code.
"""

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user clarification questions and wait for their answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question(s) to ask the user."}
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command (e.g. flutter create, flutter analyze).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run."},
                    "cwd": {"type": "string", "description": "Working directory (optional)."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (optional)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write complete contents to a file on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (relative or absolute)."},
                    "content": {"type": "string", "description": "Full file contents."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's contents from disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (relative or absolute)."}
                },
                "required": ["path"],
            },
        },
    },
]


def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    """Dispatch a tool call and return a string result for the model."""
    try:
        if name == "ask_user":
            result = tools.ask_user(arguments.get("question", ""))
            return str(result)
        if name == "run_command":
            result = tools.run_command(
                arguments.get("command", ""),
                cwd=arguments.get("cwd"),
                timeout=arguments.get("timeout", 300),
            )
            return json.dumps(result, ensure_ascii=False)
        if name == "write_file":
            result = tools.write_file(arguments.get("path", ""), arguments.get("content", ""))
            return json.dumps(result, ensure_ascii=False)
        if name == "read_file":
            result = tools.read_file(arguments.get("path", ""))
            # Truncate very large file contents to keep context sane.
            if isinstance(result, dict) and isinstance(result.get("content"), str):
                if len(result["content"]) > 20000:
                    result["content"] = result["content"][:20000] + "\n...[truncated]..."
            return json.dumps(result, ensure_ascii=False)
        return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as exc:  # pragma: no cover - defensive
        return json.dumps({"error": str(exc)})


class LLMClient:
    """Universal adapter for OpenAI-compatible and Anthropic-compatible providers."""

    def __init__(self, provider_type: str, base_url: str, api_key: str, model: str):
        self.provider_type = (provider_type or "openai").lower()
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self._http = httpx.Client(timeout=300.0)

    def _openai_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def _anthropic_url(self) -> str:
        if self.base_url.endswith("/v1/messages") or self.base_url.endswith("/messages"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return self.base_url + "/messages"
        return self.base_url + "/v1/messages"

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        last_exc = None
        for attempt in range(4):
            try:
                if self.provider_type == "anthropic":
                    return self._chat_anthropic(messages, tools)
                return self._chat_openai(messages, tools)
            except RuntimeError as exc:
                last_exc = exc
                text = str(exc)
                if "429" not in text and "rate limit" not in text.lower():
                    raise
                if attempt >= 3:
                    raise
                wait = 5.0
                match = re.search(r"try again in ([0-9]+(?:\.[0-9]+)?)s", text, re.IGNORECASE)
                if match:
                    try:
                        wait = float(match.group(1)) + 1.0
                    except ValueError:
                        wait = 5.0
                # Keep waits bounded.
                wait = min(max(wait, 2.0), 30.0)
                time.sleep(wait)
        if last_exc:
            raise last_exc
        raise RuntimeError("LLM request failed")

    def _chat_openai(self, messages, tools):
        url = self._openai_url()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if "openrouter.ai" in (self.base_url or ""):
            headers["HTTP-Referer"] = "https://github.com/appbuild"
            headers["X-Title"] = "appbuild"
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        try:
            resp = self._http.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Network error contacting {url}: {exc}")
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM request failed ({resp.status_code}): {resp.text[:1000]}")
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"Invalid JSON response: {resp.text[:500]}")
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"No choices in response: {json.dumps(data)[:500]}")
        msg = choices[0].get("message", {}) or {}
        tool_calls = msg.get("tool_calls") or []
        # Normalize arguments to strings for consistency (OpenAI already strings).
        for tc in tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments", "{}")
            if args is None:
                args = {}
            if isinstance(args, (dict, list)):
                fn["arguments"] = json.dumps(args)
            elif not isinstance(args, str):
                fn["arguments"] = json.dumps(args)
            else:
                # Ensure it is a valid stringified JSON object.
                try:
                    parsed = json.loads(args)
                    if not isinstance(parsed, (dict, list)):
                        parsed = {}
                    fn["arguments"] = json.dumps(parsed)
                except json.JSONDecodeError:
                    fn["arguments"] = "{}"
        return {"content": msg.get("content"), "tool_calls": tool_calls}

    def _convert_messages_to_anthropic(self, messages):
        system_parts = []
        non_system = []
        for m in messages:
            if m.get("role") == "system":
                if m.get("content"):
                    system_parts.append(m["content"])
                continue
            non_system.append(m)

        anth_messages = []
        i = 0
        n = len(non_system)
        while i < n:
            m = non_system[i]
            role = m.get("role")
            if role == "user":
                content = m.get("content", "")
                if isinstance(content, list):
                    anth_messages.append({"role": "user", "content": content})
                else:
                    anth_messages.append({"role": "user", "content": content if content is not None else ""})
                i += 1
            elif role == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": fn.get("name"),
                            "input": args if isinstance(args, dict) else {},
                        }
                    )
                if not blocks:
                    blocks = [{"type": "text", "text": ""}]
                anth_messages.append({"role": "assistant", "content": blocks})
                i += 1
            elif role == "tool":
                # Group consecutive tool results into one user message.
                results = []
                while i < n and non_system[i].get("role") == "tool":
                    tm = non_system[i]
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tm.get("tool_call_id"),
                            "content": tm.get("content", ""),
                        }
                    )
                    i += 1
                anth_messages.append({"role": "user", "content": results})
            else:
                # Unknown role; treat as user text.
                anth_messages.append({"role": "user", "content": str(m.get("content", ""))})
                i += 1

        system_text = "\n\n".join(system_parts).strip()
        return system_text, anth_messages

    def _convert_tools_to_anthropic(self, tools):
        out = []
        for t in tools:
            fn = t.get("function", {})
            out.append(
                {
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return out

    def _chat_anthropic(self, messages, tools):
        url = self._anthropic_url()
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        system_text, anth_messages = self._convert_messages_to_anthropic(messages)
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": anth_messages,
            "tools": self._convert_tools_to_anthropic(tools),
        }
        if system_text:
            payload["system"] = system_text
        try:
            resp = self._http.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Network error contacting {url}: {exc}")
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM request failed ({resp.status_code}): {resp.text[:1000]}")
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"Invalid JSON response: {resp.text[:500]}")

        blocks = data.get("content", []) or []
        text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        content = "\n".join(p for p in text_parts if p).strip() or None
        tool_calls = []
        for b in blocks:
            if b.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": b.get("id"),
                        "type": "function",
                        "function": {
                            "name": b.get("name"),
                            "arguments": json.dumps(b.get("input", {}) or {}),
                        },
                    }
                )
        return {"content": content, "tool_calls": tool_calls}


class Agent:
    def __init__(self, client: LLMClient, console: Optional[Console] = None, max_iterations: int = 40):
        self.client = client
        self.console = console or Console()
        self.max_iterations = max_iterations

    def run(self, idea: str) -> None:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Build a Flutter app for this idea: {idea}"},
        ]

        for iteration in range(1, self.max_iterations + 1):
            try:
                with self.console.status("[bold green]Thinking...[/bold green]", spinner="dots"):
                    response = self.client.chat(messages, TOOLS_SCHEMA)
            except Exception as exc:
                self.console.print(Panel(str(exc), title="[red]LLM Error[/red]", border_style="red"))
                return

            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": response.get("content")}
            tool_calls = response.get("tool_calls") or []
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if response.get("content"):
                self.console.print(Panel(response["content"], title="[bold blue]Assistant[/bold blue]", border_style="blue"))

            if not tool_calls:
                self.console.print("[bold green]Done.[/bold green]")
                return

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                self.console.print(f"[dim]→ tool: {name}[/dim]")
                result = execute_tool(name, args)
                # Pretty-print command results.
                if name == "run_command":
                    try:
                        parsed = json.loads(result)
                        code = parsed.get("exit_code")
                        out = parsed.get("output", "")
                        style = "green" if code == 0 else "red"
                        self.console.print(f"[{style}]exit {code}[/{style}]")
                        if out:
                            self.console.print(Panel(out[:4000], title="Command output", border_style="dim"))
                    except Exception:
                        pass
                elif name == "write_file":
                    try:
                        parsed = json.loads(result)
                        self.console.print(f"[green]wrote {parsed.get('path')}[/green]")
                    except Exception:
                        pass
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": result})

        self.console.print("[yellow]Reached maximum iterations without finishing.[/yellow]")
