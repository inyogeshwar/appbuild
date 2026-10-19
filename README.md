# appbuild

Autonomous mobile app generator for Flutter. `appbuild` is a CLI tool that takes a high-level app idea, clarifies requirements with you, scaffolds a Flutter project, writes production-ready Dart code, and self-heals using `flutter analyze`.

## Features

- ReAct agent loop with tool calling (ask user, run commands, read/write files).
- Universal LLM backend: OpenAI-compatible (OpenRouter, Groq, DeepSeek, Ollama, OpenAI) and Anthropic-compatible.
- Persistent config in `~/.appbuild/config.json`.
- Self-healing loop driven by `flutter analyze`.

## Install

```bash
pip install -e .
```

## Configure

Interactive:

```bash
appbuild config
```

Or with flags:

```bash
appbuild config --provider openai --base-url https://openrouter.ai/api/v1 --model cohere/north-mini-code:free --api-key YOUR_KEY
```

Environment variable fallbacks: `APPBUILD_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY`.

## Usage

```bash
appbuild new "todo app"
appbuild new "chat app" --provider anthropic --model claude-3-5-sonnet-20241022
appbuild doctor
appbuild --help
```

## Requirements

- Python 3.10+
- Flutter SDK in PATH
- Android build tools (for Android targets)
