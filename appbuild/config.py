"""Persistent configuration for appbuild."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_DIR = Path.home() / ".appbuild"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS: Dict[str, Any] = {
    "provider_type": "openai",
    "base_url": "https://openrouter.ai/api/v1",
    "api_key": None,
    "model": "cohere/north-mini-code:free",
}

VALID_PROVIDERS = {"openai", "anthropic"}


def load_config() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
    cfg = DEFAULTS.copy()
    for key, value in data.items():
        if value is not None:
            cfg[key] = value
    if cfg.get("provider_type") not in VALID_PROVIDERS:
        cfg["provider_type"] = "openai"
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def defaults_for_provider(provider: str):
    if provider == "anthropic":
        return "https://api.anthropic.com", "claude-3-5-sonnet-20241022"
    return "https://openrouter.ai/api/v1", "cohere/north-mini-code:free"


def resolve_api_key(provider: str, cli_key: Optional[str], cfg_key: Optional[str]) -> Optional[str]:
    if cli_key:
        return cli_key
    if cfg_key:
        return cfg_key
    # General env vars first.
    for var in ("APPBUILD_API_KEY",):
        val = os.getenv(var)
        if val:
            return val
    # Provider-specific env vars.
    if provider == "anthropic":
        for var in ("ANTHROPIC_API_KEY",):
            val = os.getenv(var)
            if val:
                return val
    else:
        for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY"):
            val = os.getenv(var)
            if val:
                return val
    # Last resort: any known key.
    for var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY"):
        val = os.getenv(var)
        if val:
            return val
    return None


def mask_key(key: Optional[str]) -> str:
    if not key:
        return "(not set)"
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "..." + key[-4:]
