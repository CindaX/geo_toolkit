"""In-process protections for the audit API: cost breaker, rate limit, cache.

All state is plain in-memory (dicts + counters), guarded by a single lock so
it's safe under FastAPI's threadpool (sync endpoints run concurrently). No
Redis / DB — process restart clears everything, which is acceptable for this
stage. Config is read lazily from env on each call so values set before
`uvicorn` starts take effect without code changes.

This module is additive and self-contained — it imports nothing from the
audit code path and modifies no existing files.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

_lock = threading.Lock()

# ── Daily cost breaker state ──────────────────────────────────────────────
# {utc_date_str: total_cost_usd}. We keep only today's bucket meaningfully;
# a date change resets the running total.
_cost_by_day: dict[str, float] = defaultdict(float)

# ── Rate limiter state ────────────────────────────────────────────────────
# identity → list[request_timestamp]. Sliding window, same pattern as
# shared/rate_limit.py but with two windows (minute + day).
_request_history: dict[str, list[float]] = defaultdict(list)

# ── Result cache state ────────────────────────────────────────────────────
# cache_key → (stored_at_epoch, result_dict)
_cache: dict[str, tuple[float, dict]] = {}


# ── Config helpers (lazy env reads) ───────────────────────────────────────

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── 1. Daily cost circuit breaker ─────────────────────────────────────────

def cost_budget_exceeded() -> bool:
    """True if today's accumulated audit cost is at/over DAILY_COST_LIMIT_USD."""
    limit = _env_float("DAILY_COST_LIMIT_USD", 20.0)
    today = _utc_today()
    with _lock:
        return _cost_by_day.get(today, 0.0) >= limit


def record_cost(cost_usd: float) -> None:
    """Add an audit's cost to today's running total (auto-resets across days)."""
    today = _utc_today()
    with _lock:
        _cost_by_day[today] += max(0.0, float(cost_usd or 0.0))


def cost_spent_today() -> float:
    with _lock:
        return round(_cost_by_day.get(_utc_today(), 0.0), 4)


# ── 2. Rate limiter (per-minute + per-day sliding windows) ────────────────

def rate_limit_check(identity: str) -> tuple[bool, str]:
    """Check & record a request for ``identity``.

    Returns ``(allowed, reason)``. On allow the request is recorded; reason is
    "". On deny nothing is recorded and reason names the window that tripped.
    """
    per_min = _env_int("RATE_LIMIT_PER_MINUTE", 5)
    per_day = _env_int("RATE_LIMIT_PER_DAY", 50)
    now = time.time()
    with _lock:
        history = [t for t in _request_history[identity] if now - t < 86400]
        in_last_minute = sum(1 for t in history if now - t < 60)
        if in_last_minute >= per_min:
            _request_history[identity] = history
            return False, f"per-minute limit ({per_min}/min) reached"
        if len(history) >= per_day:
            _request_history[identity] = history
            return False, f"per-day limit ({per_day}/day) reached"
        history.append(now)
        _request_history[identity] = history
        return True, ""


# ── 3. Result cache (TTL) ─────────────────────────────────────────────────

def cache_get(key: str) -> dict | None:
    """Return a cached result if present and within CACHE_TTL_HOURS, else None."""
    ttl_seconds = _env_float("CACHE_TTL_HOURS", 24.0) * 3600
    now = time.time()
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        stored_at, result = entry
        if now - stored_at > ttl_seconds:
            del _cache[key]
            return None
        return result


def cache_set(key: str, result: dict) -> None:
    with _lock:
        _cache[key] = (time.time(), result)


# ── Test helper ───────────────────────────────────────────────────────────

def _reset_for_testing() -> None:
    with _lock:
        _cost_by_day.clear()
        _request_history.clear()
        _cache.clear()
