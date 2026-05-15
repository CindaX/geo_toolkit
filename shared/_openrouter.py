"""Shared OpenAI-SDK-pointed-at-OpenRouter client factory.

All three LLM client modules (:mod:`shared.claude_client`,
:mod:`shared.openai_client`, :mod:`shared.perplexity_client`) route through
OpenRouter using the ``openai`` SDK. Rather than each module instantiating
its own client, they share a single process-wide singleton built here.

This is an internal module (leading underscore) — MVP code should not
import from it directly.
"""

from __future__ import annotations

from openai import OpenAI

from shared.config import (
    OPENROUTER_BASE_URL,
    get_openrouter_app_name,
    get_openrouter_key,
    get_openrouter_referer,
)

_client: OpenAI | None = None


def get_openrouter_client() -> OpenAI:
    """Return a process-wide :class:`openai.OpenAI` client for OpenRouter.

    The client is created lazily on first call and cached at module level.
    OpenRouter's recommended attribution headers (``HTTP-Referer`` and
    ``X-Title``) are read from the environment at first-call time, so
    setting ``OPENROUTER_REFERER`` / ``OPENROUTER_APP_NAME`` in ``.env``
    takes effect without any code change.
    """
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=get_openrouter_key(),
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": get_openrouter_referer(),
                "X-Title": get_openrouter_app_name(),
            },
        )
    return _client


def reset_openrouter_client() -> None:
    """Clear the cached client. Useful for tests that swap credentials."""
    global _client
    _client = None
