"""Dimension 1 — AI Crawler Access (weight 10%).

Fetches robots.txt and checks whether major AI crawlers are explicitly allowed.
"""

from __future__ import annotations

import urllib.robotparser
from urllib.parse import urljoin

import httpx

DIMENSION = "crawler_access"
WEIGHT = 0.10

_TARGET_BOTS = ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"]
_TIMEOUT = 15


def analyze(url: str, **_kwargs) -> dict:
    import time as _time
    robots_url = urljoin(url.rstrip("/") + "/", "robots.txt")
    print(f"[crawler_access] fetching {robots_url} (timeout={_TIMEOUT}s)", flush=True)
    t0 = _time.monotonic()
    try:
        resp = httpx.get(robots_url, timeout=_TIMEOUT, follow_redirects=True)
        elapsed_ms = int((_time.monotonic() - t0) * 1000)
        print(f"[crawler_access] {resp.status_code} in {elapsed_ms}ms", flush=True)
    except Exception as exc:
        elapsed_ms = int((_time.monotonic() - t0) * 1000)
        print(f"[crawler_access] FAILED after {elapsed_ms}ms: {exc}", flush=True)
        return _result(0, f"Could not fetch robots.txt: {exc}", [], [], _TARGET_BOTS[:])

    if resp.status_code == 404:
        return _result(0, "robots.txt not found (404).", [], [], _TARGET_BOTS[:])

    raw = resp.text

    # urllib.robotparser for per-bot checks
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    rp.parse(raw.splitlines())

    # A bot is "allowed" if it can fetch "/" (root)
    allowed: list[str] = []
    disallowed: list[str] = []
    not_mentioned: list[str] = []

    raw_lower = raw.lower()
    for bot in _TARGET_BOTS:
        if bot.lower() not in raw_lower:
            not_mentioned.append(bot)
        elif rp.can_fetch(bot, "/"):
            allowed.append(bot)
        else:
            disallowed.append(bot)

    # Score: explicitly allowed bots / total * 100
    # Not-mentioned bots get 0.5 credit (default allow, but not explicit)
    explicit_score = len(allowed) * 25
    implicit_score = len(not_mentioned) * 12  # partial credit
    score = min(100, explicit_score + implicit_score)

    if not allowed and not disallowed and not_mentioned:
        desc = "robots.txt exists but none of the major AI crawlers are explicitly mentioned."
    elif disallowed and not allowed:
        desc = f"All checked AI crawlers are blocked: {', '.join(disallowed)}."
    elif disallowed:
        desc = f"{len(allowed)} crawler(s) allowed, {len(disallowed)} blocked."
    else:
        desc = f"All {len(allowed)} checked AI crawlers are explicitly allowed."

    return _result(score, desc, allowed, disallowed, not_mentioned)


def _result(
    score: int,
    description: str,
    allowed: list[str],
    disallowed: list[str],
    not_mentioned: list[str],
) -> dict:
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": score,
        "description": description,
        "details": {
            "allowed": allowed,
            "disallowed": disallowed,
            "not_mentioned": not_mentioned,
        },
        "error": None,
    }
