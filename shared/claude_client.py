"""Thin wrapper around Claude via OpenRouter.

Calls go through OpenRouter's OpenAI-compatible chat completions endpoint
using the ``openai`` SDK. Exposes two synchronous helpers —
:func:`ask_claude` (free-form text) and :func:`ask_claude_json` (forces a
JSON response) — both with retry on transient failures and module-level
token-usage accounting.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx
import openai
from openai import OpenAI

from shared._openrouter import get_openrouter_client
from shared.config import (
    CHEAP_MODEL,
    CLAUDE_HAIKU,
    CLAUDE_PREMIUM,
    CLAUDE_SONNET,
    DEFAULT_MODEL,
    PERPLEXITY_SONAR,
    PREMIUM_MODEL,
)

logger = logging.getLogger(__name__)


# --- Errors --------------------------------------------------------------

class ClaudeError(RuntimeError):
    """Raised when Claude calls fail after retries."""


# --- Module state --------------------------------------------------------

# Module-level slot used by tests to inject a mock client. When ``None``
# (production), :func:`_client_or_default` falls through to the shared
# OpenRouter factory.
_client: OpenAI | None = None

_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

_MODEL_ALIASES: dict[str, str] = {
    "opus":    CLAUDE_PREMIUM,  # Opus 4.6 — most capable
    "premium": CLAUDE_PREMIUM,  # explicit premium alias
    "sonnet":  CLAUDE_SONNET,   # Sonnet 4.5
    "haiku":   CLAUDE_HAIKU,    # Haiku 4.5 — fastest / cheapest
    "default": DEFAULT_MODEL,   # → Sonnet 4.5
    "cheap":   CHEAP_MODEL,     # → Haiku 4.5
}

_MAX_RETRIES: int = 3
_BACKOFF_BASE_SECONDS: float = 1.0
_REQUEST_TIMEOUT_SECONDS: float = 60.0   # per-call timeout; without this the
                                          # SDK has no upper bound and a slow
                                          # OpenRouter response will hang the
                                          # whole audit forever.


# --- Internal ------------------------------------------------------------

def _client_or_default() -> OpenAI:
    """Return the test-injected client if set, else the shared OpenRouter client."""
    return _client if _client is not None else get_openrouter_client()


def _resolve_model(model: str | None) -> str:
    """Map a friendly alias or ``None`` to a real OpenRouter model id."""
    if model is None:
        model = "default"
    return _MODEL_ALIASES.get(model, model)


def _is_retryable(exc: BaseException) -> bool:
    # Timeout = retry. With _REQUEST_TIMEOUT_SECONDS enforced on every call,
    # a slow OpenRouter response surfaces as openai.APITimeoutError (the SDK
    # wraps the underlying httpx.TimeoutException). Bound retries by
    # _MAX_RETRIES so a persistently-slow upstream still terminates rather
    # than hanging the audit.
    if isinstance(exc, openai.APITimeoutError):
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, (openai.APIConnectionError, openai.RateLimitError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        # Retry on 5xx; 4xx are caller errors and shouldn't be retried.
        return 500 <= exc.status_code < 600
    return False


def _build_messages(prompt: str, system: str | None) -> list[dict[str, str]]:
    """Translate a (system, prompt) pair into chat-completions message form."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _call_with_retry(
    *,
    prompt: str,
    system: str | None,
    model: str | None,
    max_tokens: int,
    response_format: dict[str, Any] | None = None,
) -> tuple[str, dict[str, int]]:
    """Call ``chat.completions.create`` with retry. Returns (text, usage_delta).

    ``response_format`` is forwarded as-is — pass ``{"type": "json_object"}``
    to enable structured-output JSON mode (model is then constrained to emit
    a well-formed JSON object — no markdown fences, no stray prose).
    """
    client = _client_or_default()
    resolved = _resolve_model(model)
    last_exc: BaseException | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            kwargs: dict[str, Any] = {
                "model":       resolved,
                "max_tokens":  max_tokens,
                "messages":    _build_messages(prompt, system),
                "timeout":     _REQUEST_TIMEOUT_SECONDS,
            }
            if response_format is not None:
                kwargs["response_format"] = response_format
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — re-raised after classification
            last_exc = exc
            if not _is_retryable(exc) or attempt == _MAX_RETRIES:
                raise ClaudeError(
                    f"Claude call failed after {attempt} attempt(s): {exc}"
                ) from exc
            sleep_for = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Claude call attempt %d/%d failed (%s); retrying in %.1fs",
                attempt, _MAX_RETRIES, type(exc).__name__, sleep_for,
            )
            time.sleep(sleep_for)
            continue

        text = _extract_text(resp)
        usage = _extract_usage(resp)
        return text, usage

    raise ClaudeError(f"Claude call failed: {last_exc}")


def _extract_text(resp: Any) -> str:
    """Pull the assistant text from a chat.completions response."""
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if message is None:
        return ""
    content = getattr(message, "content", "") or ""
    return content.strip()


def _extract_usage(resp: Any) -> dict[str, int]:
    """Map OpenAI's ``prompt/completion_tokens`` to our internal ``input/output_tokens``."""
    usage = getattr(resp, "usage", None)
    return {
        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }


def _record_usage(delta: dict[str, int]) -> None:
    _usage["input_tokens"] += delta.get("input_tokens", 0)
    _usage["output_tokens"] += delta.get("output_tokens", 0)
    _usage["calls"] += 1


def _parse_json_loose(text: str) -> dict[str, Any]:
    """Try to pull a JSON object out of ``text``.

    Falls back to a fenced code block if a bare parse fails.
    Raises ``ValueError`` if no parse is possible.
    """
    text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.S)
        if not match:
            match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise ValueError("Response did not contain JSON")
        parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict):
        return {"data": parsed}
    return parsed


# --- Public API ----------------------------------------------------------

def ask_claude(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Send a prompt to Claude (via OpenRouter) and return the text response.

    Args:
        prompt: The user message.
        system: Optional system prompt.
        model: Model id or alias. ``None`` (default) uses :data:`DEFAULT_MODEL`
            (Sonnet 4.5). Named aliases: ``"default"``, ``"sonnet"``,
            ``"haiku"``, ``"cheap"``, ``"opus"``, ``"premium"``. A full
            OpenRouter model id (e.g. ``"anthropic/claude-sonnet-4.5"``) is
            passed through unchanged.
        max_tokens: Max tokens to generate.

    Returns:
        The assistant's text response, stripped of leading/trailing whitespace.

    Raises:
        ClaudeError: If the call fails after all retries.
    """
    text, usage = _call_with_retry(
        prompt=prompt, system=system, model=model, max_tokens=max_tokens
    )
    _record_usage(usage)
    return text


def ask_claude_json(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Like :func:`ask_claude` but force a JSON object response.

    A short instruction is prepended to the system prompt to coax JSON-only
    output. If the first response doesn't parse, one re-ask is made.

    Returns:
        The parsed JSON object. On unrecoverable parse failure, returns
        ``{"error": "...", "raw": "<raw text>"}`` instead of raising.
    """
    json_directive = (
        "Respond with ONLY a valid JSON object. No prose, no markdown fences, "
        "no commentary. The response MUST start with '{' and end with '}'."
    )
    full_system = f"{system}\n\n{json_directive}" if system else json_directive

    # OpenRouter / OpenAI structured-output JSON mode — model is constrained
    # to emit a well-formed JSON object. This is the real fix for "Claude
    # occasionally wraps in markdown fences" and "long Chinese strings get
    # unescaped quotes / raw newlines" symptoms. The system message still
    # has the "respond with JSON" instruction so the model knows the shape.
    _json_response_format = {"type": "json_object"}

    try:
        text, usage = _call_with_retry(
            prompt=prompt, system=full_system, model=model, max_tokens=max_tokens,
            response_format=_json_response_format,
        )
        _record_usage(usage)
    except ClaudeError as exc:
        return {"error": str(exc)}

    try:
        return _parse_json_loose(text)
    except (ValueError, json.JSONDecodeError):
        pass

    # One re-ask attempt.
    retry_prompt = (
        f"{prompt}\n\n"
        "Your previous response could not be parsed as JSON. "
        "Reply again with ONLY a valid JSON object."
    )
    try:
        text, usage = _call_with_retry(
            prompt=retry_prompt, system=full_system, model=model, max_tokens=max_tokens,
            response_format=_json_response_format,
        )
        _record_usage(usage)
    except ClaudeError as exc:
        return {"error": str(exc), "raw": text}

    try:
        return _parse_json_loose(text)
    except (ValueError, json.JSONDecodeError):
        return {"error": "Claude did not return valid JSON", "raw": text}


def ask_perplexity(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
) -> str:
    """Ask Perplexity Sonar (web-grounded search) a question; return its text answer.

    Routed through OpenRouter like Claude, reusing the same retry/timeout/usage
    machinery. Used for the one-time competitor snapshot: we ask "who would you
    recommend for X" and later extract the cited brands with a cheap Claude call.

    Note: Sonar adds a per-request web-search fee that is NOT reflected in token
    usage, so the cost accounting here is approximate — callers MUST hard-cap the
    number of snapshot questions (we cap at 8) rather than rely on token counts.

    Args:
        prompt: The shopping question to ask, as a real user would type it.
        system: Optional system prompt.
        model: OpenRouter model id; ``None`` (default) uses
            :data:`PERPLEXITY_SONAR` (the cheap tier).
        max_tokens: Max tokens to generate (answers are short, default 1024).

    Returns:
        The assistant's text answer, stripped.

    Raises:
        ClaudeError: If the call fails after all retries.
    """
    resolved = model or PERPLEXITY_SONAR
    text, usage = _call_with_retry(
        prompt=prompt, system=system, model=resolved, max_tokens=max_tokens
    )
    _record_usage(usage)
    return text


def get_usage_stats() -> dict[str, int]:
    """Return cumulative token usage and call count for this process."""
    return dict(_usage)


def reset_usage_stats() -> None:
    """Reset the usage counters (useful for tests / per-request reporting)."""
    _usage["input_tokens"] = 0
    _usage["output_tokens"] = 0
    _usage["calls"] = 0
