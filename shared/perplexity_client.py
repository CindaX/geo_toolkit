"""Thin client for the Perplexity Chat Completions API.

Returns a normalized ``{answer, citations, sources}`` dict regardless of the
exact upstream response shape, so MVP code doesn't need to care about it.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

import httpx

from shared.config import get_perplexity_key

logger = logging.getLogger(__name__)

_API_URL: str = "https://api.perplexity.ai/chat/completions"
_REQUEST_TIMEOUT: float = 30.0
_MAX_RETRIES: int = 3
_BACKOFF_BASE_SECONDS: float = 1.0


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


def ask_perplexity(
    query: str,
    *,
    model: str = "sonar",
    system: str | None = None,
) -> PerplexityResponse:
    """Ask Perplexity a question and return a normalized response.

    Args:
        query: The user question.
        model: Perplexity model name. ``"sonar"`` is a sensible default.
        system: Optional system prompt.

    Returns:
        A :class:`PerplexityResponse`. On unrecoverable failure ``answer`` is
        empty, ``error`` is populated, and the function does not raise — so
        callers can show a friendly UI message.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": query})

    payload: dict[str, Any] = {"model": model, "messages": messages}
    headers = {
        "Authorization": f"Bearer {get_perplexity_key()}",
        "Content-Type": "application/json",
    }

    last_exc: BaseException | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
                resp = client.post(_API_URL, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            return _normalize(data)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_exc = exc
            if status < 500 or attempt == _MAX_RETRIES:
                return _error_response(f"Perplexity HTTP {status}: {exc.response.text[:200]}")
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES:
                return _error_response(f"Perplexity call failed: {exc}")
        sleep_for = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        logger.warning(
            "Perplexity attempt %d/%d failed (%s); retrying in %.1fs",
            attempt, _MAX_RETRIES, type(last_exc).__name__ if last_exc else "?", sleep_for,
        )
        time.sleep(sleep_for)

    return _error_response(f"Perplexity call failed: {last_exc}")


def _normalize(data: dict[str, Any]) -> PerplexityResponse:
    """Convert Perplexity's response shape into our normalized dict."""
    answer = ""
    choices = data.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        answer = (msg.get("content") or "").strip()

    raw_citations = data.get("citations") or []
    citations: list[str] = [c for c in raw_citations if isinstance(c, str)]

    sources: list[Source] = []
    # Newer Perplexity responses include a "search_results" array with titles.
    search_results = data.get("search_results") or []
    for r in search_results:
        if not isinstance(r, dict):
            continue
        url = r.get("url") or ""
        if not url:
            continue
        sources.append({"url": url, "title": r.get("title") or ""})

    if not sources:
        # Fall back to citations-only sources.
        sources = [{"url": url, "title": ""} for url in citations]

    return {
        "answer": answer,
        "citations": citations,
        "sources": sources,
        "error": "",
    }


def _error_response(message: str) -> PerplexityResponse:
    return {"answer": "", "citations": [], "sources": [], "error": message}
