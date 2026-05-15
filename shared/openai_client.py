"""Thin client for GPT-4o (via OpenRouter) with optional web search.

Calls go through OpenRouter using the ``openai`` SDK. By default the model
gets the ``:online`` suffix appended, which activates OpenRouter's
Exa-backed web search plugin transparently. Pass an explicit model id
without ``:online`` (e.g. ``"openai/gpt-4o-mini"``) to disable web search.

OpenRouter bills web searches separately from token usage (~$4 per 1k
searches at the time of writing), so consider whether each call really
needs live web access.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from openai import OpenAI, OpenAIError

from shared._openrouter import get_openrouter_client
from shared.config import OPENAI_GPT4O_MINI

logger = logging.getLogger(__name__)


class Source(TypedDict, total=False):
    url: str
    title: str


class OpenAIResponse(TypedDict):
    """Normalized response from :func:`ask_openai_with_search`."""

    answer: str
    sources: list[Source]
    used_web_search: bool
    error: str  # Empty string on success.


# Test-overridable; otherwise use the shared OpenRouter factory.
_client: OpenAI | None = None


def _client_or_default() -> OpenAI:
    return _client if _client is not None else get_openrouter_client()


def _normalize_model(model: str) -> tuple[str, bool]:
    """Resolve a friendly model name into a full OpenRouter id.

    Rules:
    - ``"gpt-4o-mini"`` (or any name without ``"/"``) → adds the
      ``openai/`` prefix and ``:online`` suffix.
    - ``"openai/gpt-4o-mini"`` (vendor-prefixed, no suffix) → left alone,
      web search disabled.
    - ``"openai/gpt-4o-mini:online"`` (already suffixed) → left alone,
      web search enabled.
    """
    if "/" not in model:
        return f"openai/{model}:online", True
    return model, model.endswith(":online")


def ask_openai_with_search(
    query: str,
    *,
    model: str = "gpt-4o-mini",
    system: str | None = None,
) -> OpenAIResponse:
    """Ask an OpenAI model a question, with web search by default.

    Args:
        query: The user question.
        model: Friendly short name (default ``"gpt-4o-mini"`` → resolves to
            ``"openai/gpt-4o-mini:online"``), or a full OpenRouter id.
            Pass ``"openai/gpt-4o-mini"`` (no ``:online``) to disable web
            search and avoid the extra search billing.
        system: Optional system prompt.

    Returns:
        An :class:`OpenAIResponse`. On failure ``answer`` is empty,
        ``error`` is populated, and the function does not raise — so
        callers can show a friendly UI message.
    """
    final_model, used_web_search = _normalize_model(model)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": query})

    try:
        resp = _client_or_default().chat.completions.create(
            model=final_model, messages=messages
        )
    except OpenAIError as exc:
        return _error_response(f"OpenAI call failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _error_response(f"OpenAI call errored: {exc}")

    answer, sources = _extract_answer_and_sources(resp)
    return {
        "answer": answer,
        "sources": sources,
        "used_web_search": used_web_search,
        "error": "" if answer else "OpenAI returned empty response",
    }


# --- Normalization -------------------------------------------------------

def _extract_answer_and_sources(resp: Any) -> tuple[str, list[Source]]:
    """Pull text + URL citations out of a chat-completions response."""
    answer = ""
    sources: list[Source] = []

    choices = getattr(resp, "choices", None) or []
    if not choices:
        return answer, sources

    message = getattr(choices[0], "message", None)
    if message is None:
        return answer, sources

    answer = (getattr(message, "content", "") or "").strip()

    annotations = getattr(message, "annotations", None) or []
    sources = _extract_url_citations(annotations)

    return answer, sources


def _extract_url_citations(annotations: list[Any]) -> list[Source]:
    """Pull ``url_citation`` annotations out of a chat message.

    OpenRouter's web plugin emits items shaped like::

        {"type": "url_citation",
         "url_citation": {"url": "...", "title": "...", ...}}

    but we also accept inlined ``url`` / ``title`` and plain dicts vs
    pydantic objects, since the exact shape varies by upstream model.
    """
    sources: list[Source] = []
    seen: set[str] = set()
    for ann in annotations:
        if _attr(ann, "type") != "url_citation":
            continue
        nested = _attr(ann, "url_citation")
        url = _attr(nested or ann, "url") or ""
        title = _attr(nested or ann, "title") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append({"url": url, "title": title})
    return sources


def _attr(obj: Any, name: str) -> Any:
    """Read ``name`` from either a dict or an object — return None if absent."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _error_response(message: str) -> OpenAIResponse:
    return {"answer": "", "sources": [], "used_web_search": False, "error": message}


__all__ = [
    "OPENAI_GPT4O_MINI",
    "OpenAIResponse",
    "Source",
    "ask_openai_with_search",
]
