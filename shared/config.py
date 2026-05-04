"""Configuration and secrets loading.

All API keys live in the project's ``.env`` file (see ``.env.example``).
Functions in this module read them lazily and raise a ``ConfigError`` with
a clear, actionable message if a key is missing — never a raw ``KeyError``.
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


# --- Model identifiers ---------------------------------------------------

DEFAULT_MODEL: str = "claude-opus-4-7"
"""Default Anthropic model — high-quality reasoning."""

CHEAP_MODEL: str = "claude-haiku-4-5-20251001"
"""Lower-cost Anthropic model for bulk / draft work."""


# --- Errors --------------------------------------------------------------

class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


# --- Internal helpers ----------------------------------------------------

def _require_env(var_name: str, *, hint: str) -> str:
    """Return the env var value, or raise a friendly :class:`ConfigError`.

    Args:
        var_name: Name of the environment variable to look up.
        hint: Short human-readable description of what the variable is for,
            used to build the error message.

    Returns:
        The non-empty value of the environment variable.

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

def get_anthropic_key() -> str:
    """Return the Anthropic API key, or raise :class:`ConfigError`."""
    return _require_env("ANTHROPIC_API_KEY", hint="Anthropic / Claude API key")


def get_openai_key() -> str:
    """Return the OpenAI API key, or raise :class:`ConfigError`."""
    return _require_env("OPENAI_API_KEY", hint="OpenAI API key")


def get_perplexity_key() -> str:
    """Return the Perplexity API key, or raise :class:`ConfigError`."""
    return _require_env("PERPLEXITY_API_KEY", hint="Perplexity API key")
