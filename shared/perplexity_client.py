"""Thin client for Perplexity online (via OpenRouter).

Routes through OpenRouter's OpenAI-compatible endpoint. The default model
is ``perplexity/llama-3.1-sonar-small-128k-online``, which has Perplexity's
real-time web search built in — the ``-online`` suffix is part of
Perplexity's model name, not OpenRouter's plugin system, so no extra
search billing applies beyond Perplexity's own per-token rates.

Returns a normalized ``{answer, citations, sources, error}`` dict
regardless of the exact upstream response shape.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

import openai
from openai import OpenAI, OpenAIError

from shared._openrouter import get_openrouter_client
from shared.config import PERPLEXITY_SONAR_ONLINE

logger = logging.getLogger(__name__)


class Source(TypedDict, total=False):
    """A search result with optional title."""

    url: str
    title: str


class PerplexityResponse(TypedDict):
    """Normalized response from :func:`ask_perplexity`."""

    answer: str
    citations: list[str]
    sources: list[Source]
    error: str  # Empty string on success.


class PerplexityError(RuntimeError):
    """Raised when Perplexity calls fail after retries."""


# Test-overridable; otherwise use the shared OpenRouter factory.
_client: OpenAI | None = None

_MAX_RETRIES: int = 3
_BACKOFF_BASE_SECONDS: float = 1.0


def _client_or_default() -> OpenAI:
    return _client if _client is not None else get_openrouter_client()


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (openai.APIConnectionError, openai.RateLimitError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return 500 <= exc.status_code < 600
    return False


def ask_perplexity(
    query: str,
    *,
    model: str = PERPLEXITY_SONAR_ONLINE,
    system: str | None = None,
) -> PerplexityResponse:
    """Ask Perplexity a question and return a normalized response.

    Args:
        query: The user question.
        model: OpenRouter-formatted Perplexity model id. The default is
            ``"perplexity/llama-3.1-sonar-small-128k-online"`` (Sonar
            Small with built-in web search).
        system: Optional system prompt.

    Returns:
        A :class:`PerplexityResponse`. On unrecoverable failure ``answer``
        is empty, ``error`` is populated, and the function does not raise
        — callers can show a friendly UI message.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": query})

    last_exc: BaseException | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = _client_or_default().chat.completions.create(
                model=model, messages=messages
            )
            return _normalize(resp)
        except OpenAIError as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == _MAX_RETRIES:
                return _error_response(f"Perplexity call failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            return _error_response(f"Perplexity call errored: {exc}")
        sleep_for = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        logger.warning(
            "Perplexity attempt %d/%d failed (%s); retrying in %.1fs",
            attempt, _MAX_RETRIES,
            type(last_exc).__name__ if last_exc else "?", sleep_for,
        )
        time.sleep(sleep_for)

    return _error_response(f"Perplexity call failed: {last_exc}")


# --- Normalization -------------------------------------------------------

def _normalize(resp: Any) -> PerplexityResponse:
    """Convert OpenRouter+Perplexity's response into our normalized dict.

    Citations show up in two possible places (we accept either):
    - ``response.citations`` — Perplexity's native top-level array
      (sometimes forwarded by OpenRouter).
    - ``message.annotations`` with ``type=="url_citation"`` — OpenRouter's
      standardized annotation form.
    """
    answer = ""
    citations: list[str] = []
    sources: list[Source] = []
    seen: set[str] = set()

    # 1. Pull answer text from choices[0].message.content
    choices = getattr(resp, "choices", None) or []
    message = None
    if choices:
        message = getattr(choices[0], "message", None)
        if message is not None:
            answer = (getattr(message, "content", "") or "").strip()

    # 2. Top-level "citations" (Perplexity native passthrough)
    raw_top = getattr(resp, "citations", None) or []
    for c in raw_top:
        if isinstance(c, str) and c not in seen:
            seen.add(c)
            citations.append(c)
            sources.append({"url": c, "title": ""})

    # 3. message.annotations url_citation entries
    if message is not None:
        for ann in (getattr(message, "annotations", None) or []):
            if _attr(ann, "type") != "url_citation":
                continue
            nested = _attr(ann, "url_citation")
            url = _attr(nested or ann, "url") or ""
            title = _attr(nested or ann, "title") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            citations.append(url)
            sources.append({"url": url, "title": title})

    return {
        "answer": answer,
        "citations": citations,
        "sources": sources,
        "error": "",
    }


def _attr(obj: Any, name: str) -> Any:
    """Read ``name`` from either a dict or an object — return None if absent."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _error_response(message: str) -> PerplexityResponse:
    return {"answer": "", "citations": [], "sources": [], "error": message}
