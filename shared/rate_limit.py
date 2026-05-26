"""In-memory per-IP rate limiter.

Default: 1 audit per hour per IP. Configurable via constants below.

Notes:
- In-memory storage resets when the Streamlit Cloud container restarts
  (deploy, idle eviction, etc). For production we'd use Redis. Day-6 MVP
  treats this as a soft floor.
- Locally, `st.context.headers` is empty → all sessions share the "unknown"
  bucket. That's fine for testing — first request succeeds, second blocked.
"""

from __future__ import annotations

import time
from collections import defaultdict

RATE_LIMIT_WINDOW_SEC = 3600   # 1 hour
RATE_LIMIT_MAX_REQUESTS = 1    # 1 request per window per IP

_ip_history: dict[str, list[float]] = defaultdict(list)


def check_and_record(ip: str) -> tuple[bool, int]:
    """Check if ``ip`` can make a request now. Side-effect: records on allow.

    Returns:
        ``(True, 0)`` if allowed (the request is recorded into history).
        ``(False, seconds_until_reset)`` if denied.
    """
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SEC

    # Drop timestamps older than the window
    _ip_history[ip] = [t for t in _ip_history[ip] if t > cutoff]

    if len(_ip_history[ip]) >= RATE_LIMIT_MAX_REQUESTS:
        oldest = min(_ip_history[ip])
        remaining = int((oldest + RATE_LIMIT_WINDOW_SEC) - now)
        return False, max(remaining, 1)

    _ip_history[ip].append(now)
    return True, 0


def _reset_for_testing() -> None:
    """Clear all IP history. Test-only helper — do not call in production."""
    _ip_history.clear()
