"""Configuration and secrets loading.

All LLM access is routed through OpenRouter, so a single
``OPENROUTER_API_KEY`` in ``.env`` covers Claude, GPT-4o, and Perplexity.
:func:`get_openrouter_key` reads it lazily and raises a friendly
:class:`ConfigError` if the key is missing — never a raw ``KeyError``.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from shared import PROJECT_ROOT

# Load .env from the project root once, at import time. ``override=False`` so
# that real environment variables (e.g. set by CI) take precedence over .env.
_ENV_PATH: Path = PROJECT_ROOT / ".env"
load_dotenv(_ENV_PATH, override=False)


# --- OpenRouter endpoint -------------------------------------------------

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

_OPENROUTER_REFERER_DEFAULT: str = "https://github.com/CindaX/geo_toolkit"
_OPENROUTER_APP_NAME_DEFAULT: str = "geo_toolkit"


# --- Model identifiers ---------------------------------------------------

# Anthropic family (via OpenRouter) — use dot-notation version strings.
CLAUDE_HAIKU: str = "anthropic/claude-haiku-4.5"
CLAUDE_SONNET: str = "anthropic/claude-sonnet-4.5"
CLAUDE_PREMIUM: str = "anthropic/claude-opus-4.6"   # Opus 4.6, not 4.5

# Backward-compat alias so any existing import of ``CLAUDE_OPUS`` keeps working.
CLAUDE_OPUS: str = CLAUDE_PREMIUM

# OpenAI family (via OpenRouter)
OPENAI_GPT4O_MINI: str = "openai/gpt-4o-mini"

# Perplexity online (via OpenRouter) — built-in real-time web search.
PERPLEXITY_SONAR_ONLINE: str = "perplexity/llama-3.1-sonar-small-128k-online"

# --- Semantic tier aliases -----------------------------------------------

DEFAULT_MODEL: str = CLAUDE_SONNET
"""Default model for most tasks — Sonnet 4.5 (good quality / cost balance)."""

CHEAP_MODEL: str = CLAUDE_HAIKU
"""Fast, low-cost model for bulk / draft work — Haiku 4.5."""

PREMIUM_MODEL: str = CLAUDE_PREMIUM
"""Highest-capability model — Opus 4.6 (use when quality matters most)."""


# --- Errors --------------------------------------------------------------

class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


# --- Internal helpers ----------------------------------------------------

def _require_env(var_name: str, *, hint: str) -> str:
    """Return the env var value, or raise a friendly :class:`ConfigError`.

    Raises:
        ConfigError: If the variable is unset or empty.
    """
    value = os.environ.get(var_name, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required environment variable {var_name!r} ({hint}). "
            f"Add it to your .env file (see .env.example)."
        )
    return value


# --- Public accessors ----------------------------------------------------

def get_openrouter_key() -> str:
    """Return the OpenRouter API key, or raise :class:`ConfigError`."""
    return _require_env(
        "OPENROUTER_API_KEY",
        hint="OpenRouter API key — covers Claude, GPT-4o, and Perplexity",
    )


def get_openrouter_referer() -> str:
    """Return the HTTP-Referer header value for OpenRouter attribution.

    Reads ``OPENROUTER_REFERER`` from the environment; falls back to the
    default GitHub URL if unset. This field is optional — OpenRouter uses
    it for its public model-usage leaderboard only.
    """
    return os.environ.get("OPENROUTER_REFERER", "").strip() or _OPENROUTER_REFERER_DEFAULT


def get_openrouter_app_name() -> str:
    """Return the X-Title header value for OpenRouter attribution.

    Reads ``OPENROUTER_APP_NAME`` from the environment; falls back to
    ``"geo_toolkit"`` if unset.
    """
    return os.environ.get("OPENROUTER_APP_NAME", "").strip() or _OPENROUTER_APP_NAME_DEFAULT
