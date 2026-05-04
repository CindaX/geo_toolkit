"""Thin wrapper around the Anthropic Python SDK.

Exposes two synchronous helpers — :func:`ask_claude` (free-form text) and
:func:`ask_claude_json` (forces a JSON response) — both with retry on
transient failures and module-level token-usage accounting.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import anthropic

from shared.config import CHEAP_MODEL, DEFAULT_MODEL, get_anthropic_key

logger = logging.getLogger(__name__)


# --- Errors --------------------------------------------------------------

class ClaudeError(RuntimeError):
    """Raised when Claude calls fail after retries."""


# --- Module state --------------------------------------------------------

_client: anthropic.Anthropic | None = None
_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

_MODEL_ALIASES: dict[str, str] = {
    "opus": DEFAULT_MODEL,
    "haiku": CHEAP_MODEL,
    "default": DEFAULT_MODEL,
    "cheap": CHEAP_MODEL,
}

_MAX_RETRIES: int = 3
_BACKOFF_BASE_SECONDS: float = 1.0


# --- Internal ------------------------------------------------------------

def _get_client() -> anthropic.Anthropic:
    """Return a process-wide Anthropic client, creating it lazily."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=get_anthropic_key())
    return _client


def _resolve_model(model: str) -> str:
    return _MODEL_ALIASES.get(model, model)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.RateLimitError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        # Retry on 5xx; 4xx are caller errors and shouldn't be retried.
        return 500 <= exc.status_code < 600
    return False


def _call_with_retry(
    *,
    prompt: str,
    system: str | None,
    model: str,
    max_tokens: int,
) -> tuple[str, dict[str, int]]:
    """Call ``messages.create`` with retry. Returns (text, usage_delta)."""
    client = _get_client()
    resolved = _resolve_model(model)
    last_exc: BaseException | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": resolved,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            resp = client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — we re-raise after classification
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

    # Should be unreachable, but satisfies the type checker.
    raise ClaudeError(f"Claude call failed: {last_exc}")


def _extract_text(resp: Any) -> str:
    """Concatenate text blocks from a ``messages.create`` response."""
    parts: list[str] = []
    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts).strip()


def _extract_usage(resp: Any) -> dict[str, int]:
    usage = getattr(resp, "usage", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


def _record_usage(delta: dict[str, int]) -> None:
    _usage["input_tokens"] += delta.get("input_tokens", 0)
    _usage["output_tokens"] += delta.get("output_tokens", 0)
    _usage["calls"] += 1


def _parse_json_loose(text: str) -> dict[str, Any]:
    """Try to pull a JSON object out of ``text``.

    Falls back to extracting from a fenced ``\\u0060\\u0060\\u0060json`` block if a bare parse
    fails. Raises ``ValueError`` if no parse is possible.
    """
    text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.S)
        if not match:
            # Last-ditch: find the first {...} span.
            match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise ValueError("Response did not contain JSON")
        parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict):
        # Wrap arrays/scalars so callers always get a dict.
        return {"data": parsed}
    return parsed


# --- Public API ----------------------------------------------------------

def ask_claude(
    prompt: str,
    *,
    system: str | None = None,
    model: str = "opus",
    max_tokens: int = 4096,
) -> str:
    """Send a prompt to Claude and return the text response.

    Args:
        prompt: The user message.
        system: Optional system prompt.
        model: Model id or alias (``"opus"``, ``"haiku"``, ``"default"``,
            ``"cheap"``, or a literal model id).
        max_tokens: Max tokens to generate.

    Returns:
        The model's text response (already stripped of leading/trailing
        whitespace).

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
    model: str = "opus",
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Like :func:`ask_claude` but force a JSON object response.

    A short instruction is prepended to the system prompt to coax JSON-only
    output. If the first response doesn't parse, a single re-ask is made.

    Returns:
        The parsed JSON object. On unrecoverable parse failure, returns
        ``{"error": "...", "raw": "<raw text>"}`` instead of raising — so
        callers can surface a friendly message in the UI.
    """
    json_directive = (
        "Respond with ONLY a valid JSON object. No prose, no markdown fences, "
        "no commentary. The response MUST start with '{' and end with '}'."
    )
    full_system = f"{system}\n\n{json_directive}" if system else json_directive

    try:
        text, usage = _call_with_retry(
            prompt=prompt, system=full_system, model=model, max_tokens=max_tokens
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
            prompt=retry_prompt, system=full_system, model=model, max_tokens=max_tokens
        )
        _record_usage(usage)
    except ClaudeError as exc:
        return {"error": str(exc), "raw": text}

    try:
        return _parse_json_loose(text)
    except (ValueError, json.JSONDecodeError):
        return {"error": "Claude did not return valid JSON", "raw": text}


def get_usage_stats() -> dict[str, int]:
    """Return cumulative token usage and call count for this process."""
    return dict(_usage)


def reset_usage_stats() -> None:
    """Reset the usage counters (useful for tests / per-request reporting)."""
    _usage["input_tokens"] = 0
    _usage["output_tokens"] = 0
    _usage["calls"] = 0
