"""Thin client for OpenAI's Responses API with web search.

The MVP apps mostly need "ask a question with the web available, get an
answer + sources back". This module hides whether we're using the new
Responses API (preferred) or falling back to a plain chat completion when
the web_search tool isn't available for the chosen model / account.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from openai import OpenAI, OpenAIError

from shared.config import get_openai_key

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


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=get_openai_key())
    return _client


def ask_openai_with_search(
    query: str,
    *,
    model: str = "gpt-4o-mini",
    system: str | None = None,
) -> OpenAIResponse:
    """Ask an OpenAI model a question, allowing it to search the web.

    Tries the Responses API with the ``web_search`` tool first. If that
    surface isn't available (older SDK / model not enabled), falls back to a
    plain chat completion with no live web access and reports
    ``used_web_search=False``.

    Returns:
        An :class:`OpenAIResponse`. On failure ``answer`` is empty,
        ``error`` is populated, and the function does not raise.
    """
    client = _get_client()

    # --- Path 1: Responses API + web_search tool ---
    responses_api = getattr(client, "responses", None)
    if responses_api is not None:
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "input": query,
                "tools": [{"type": "web_search"}],
            }
            if system:
                kwargs["instructions"] = system
            resp = responses_api.create(**kwargs)
            return _normalize_responses(resp)
        except OpenAIError as exc:
            logger.warning("OpenAI Responses API failed (%s); falling back.", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI Responses API errored (%s); falling back.", exc)

    # --- Path 2: chat completions, no web search ---
    try:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": query})
        chat = client.chat.completions.create(model=model, messages=messages)
        answer = (chat.choices[0].message.content or "").strip() if chat.choices else ""
        return {
            "answer": answer,
            "sources": [],
            "used_web_search": False,
            "error": "" if answer else "OpenAI returned empty response",
        }
    except OpenAIError as exc:
        return _error_response(f"OpenAI call failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        return _error_response(f"OpenAI call errored: {exc}")


# --- Normalization -------------------------------------------------------

def _normalize_responses(resp: Any) -> OpenAIResponse:
    """Pull text + URL citations out of a Responses-API response."""
    answer = (getattr(resp, "output_text", "") or "").strip()
    sources: list[Source] = []
    seen: set[str] = set()

    output = getattr(resp, "output", None) or []
    for item in output:
        # Each item may have a content list with annotations.
        content_list = getattr(item, "content", None) or []
        for content in content_list:
            annotations = getattr(content, "annotations", None) or []
            for ann in annotations:
                ann_type = getattr(ann, "type", "")
                if ann_type not in ("url_citation", "web_search_result"):
                    continue
                url = getattr(ann, "url", "") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                sources.append({"url": url, "title": getattr(ann, "title", "") or ""})

    return {
        "answer": answer,
        "sources": sources,
        "used_web_search": True,
        "error": "" if answer else "OpenAI returned empty response",
    }


def _error_response(message: str) -> OpenAIResponse:
    return {"answer": "", "sources": [], "used_web_search": False, "error": message}
