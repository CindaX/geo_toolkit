"""Dimension 2 — llms.txt Presence (weight 8%).

Fetches /llms.txt and scores based on existence, length, and section coverage.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx

DIMENSION = "llms_txt"
WEIGHT = 0.08

_CORE_SECTIONS = ["## about", "## products", "## use", "## do not", "## contact", "## verified"]
_TIMEOUT = 10


def analyze(url: str, **_kwargs) -> dict:
    llms_url = urljoin(url.rstrip("/") + "/", "llms.txt")
    try:
        resp = httpx.get(llms_url, timeout=_TIMEOUT, follow_redirects=True)
    except Exception as exc:
        return _result(0, "Could not fetch llms.txt.", 0, [], str(exc))

    if resp.status_code == 404:
        return _result(0, "No llms.txt found at the website root.", 0, [], None)

    text = resp.text.strip()
    word_count = len(text.split())
    text_lower = text.lower()

    found_sections = [s for s in _CORE_SECTIONS if s in text_lower]

    # Scoring
    word_points = min(40, int(word_count / 5))   # 200+ words → 40 pts
    section_points = min(60, len(found_sections) * 12)
    score = min(100, word_points + section_points)

    if score == 0:
        desc = "llms.txt exists but is empty or too minimal to be useful."
    elif score < 50:
        desc = f"llms.txt exists ({word_count} words) but is missing most core sections."
    elif score < 80:
        desc = f"llms.txt present ({word_count} words) with {len(found_sections)} of {len(_CORE_SECTIONS)} core sections."
    else:
        desc = f"llms.txt is well-structured ({word_count} words, {len(found_sections)} core sections)."

    return _result(score, desc, word_count, found_sections, None)


def _result(
    score: int,
    description: str,
    word_count: int,
    sections_found: list[str],
    error: str | None,
) -> dict:
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": score,
        "description": description,
        "details": {
            "word_count": word_count,
            "sections_found": sections_found,
            "total_core_sections": len(_CORE_SECTIONS),
        },
        "error": error,
    }
