"""Configuration and secrets loading.

All LLM access is routed through OpenRouter, so a single
``OPENROUTER_API_KEY`` in either ``.streamlit/secrets.toml`` (Streamlit Cloud)
or ``.env`` (local dev) covers Claude, GPT-4o, and Perplexity.
:func:`get_openrouter_key` reads it lazily via :mod:`shared.secrets` and
raises a friendly :class:`ConfigError` if the key is missing — never a raw
``KeyError``.
"""

from __future__ import annotations

from shared.secrets import get_secret


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
# Current cheap tier is `perplexity/sonar` (the older llama-3.1-sonar-* ids were
# retired by OpenRouter in 2025). Used for the one-time competitor snapshot.
PERPLEXITY_SONAR: str = "perplexity/sonar"
# Back-compat alias for any older import.
PERPLEXITY_SONAR_ONLINE: str = PERPLEXITY_SONAR

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
    """Return a secret value from layered sources, or raise.

    Lookup order (via :func:`shared.secrets.get_secret`):
      1. ``st.secrets[var_name]`` — Streamlit Cloud production
      2. project-root ``.env`` (python-dotenv) — local dev
      3. ``os.environ`` — CI / shell-set

    Empty strings are treated as missing.

    Raises:
        ConfigError: If the variable is unset or empty in all sources.
    """
    value = (get_secret(var_name) or "").strip()
    if not value:
        raise ConfigError(
            f"Missing required secret {var_name!r} ({hint}). "
            f"Set it in .streamlit/secrets.toml (Streamlit Cloud) or .env (local dev)."
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

    Reads ``OPENROUTER_REFERER`` via layered secrets; falls back to the
    default GitHub URL if unset. This field is optional — OpenRouter uses
    it for its public model-usage leaderboard only.
    """
    return (get_secret("OPENROUTER_REFERER") or "").strip() or _OPENROUTER_REFERER_DEFAULT


def get_openrouter_app_name() -> str:
    """Return the X-Title header value for OpenRouter attribution.

    Reads ``OPENROUTER_APP_NAME`` via layered secrets; falls back to
    ``"geo_toolkit"`` if unset.
    """
    return (get_secret("OPENROUTER_APP_NAME") or "").strip() or _OPENROUTER_APP_NAME_DEFAULT
