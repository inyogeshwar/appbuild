# AGENTS.md

## Repo
Python CLI package `appbuild` (entry point `appbuild = appbuild.cli:app`). No test suite currently.

## Commands
- Install dev: `pip install -e .`
- Run CLI: `appbuild --help`, `appbuild doctor`, `appbuild config`, `appbuild new "idea"`
- No lint/typecheck/test config present; verify with `appbuild --help` and `appbuild doctor`.

## Architecture
- `appbuild/cli.py` — Typer commands; `config` command name shadows the `config` module, so imports use `cfgmod` alias.
- `appbuild/agent.py` — ReAct loop + `LLMClient` adapter (OpenAI + Anthropic).
- `appbuild/tools.py` — `ask_user`, `run_command`, `write_file`, `read_file`.
- `appbuild/config.py` — `~/.appbuild/config.json`, env fallback chain.

## Gotchas
- Canonical message history stays OpenAI-shaped; Anthropic conversion happens in `LLMClient`.
- `run_command` uses `shell=True`; pass `cwd` for project-scoped Flutter commands.
- `appbuild install --user` can shadow the editable dev install; uninstall user copy to use local repo.
- API 401 "User not found" is an account/key issue, not an app bug.
