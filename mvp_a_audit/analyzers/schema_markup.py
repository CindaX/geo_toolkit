"""Dimension 3 — Schema Markup (weight 12%).

Fetches the homepage HTML, extracts all JSON-LD blocks, and scores based on
the presence of core Schema.org types.
"""

from __future__ import annotations

import json
import re

import httpx

DIMENSION = "schema_markup"
WEIGHT = 0.12

_CORE_TYPES = {"Organization", "Product", "FAQPage", "BreadcrumbList"}
_TIMEOUT = 15
# Minimal browser headers for the fallback fetch (avoids the bare-httpx UA that
# WAFs 403). The orchestrator normally passes homepage_html so this rarely runs.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def analyze(url: str, **_kwargs) -> dict:
    # Reuse the HTML already fetched by the central crawler (which uses full
    # browser headers). Only fetch directly if it wasn't provided.
    html = _kwargs.get("homepage_html") or ""
    if not html:
        try:
            resp = httpx.get(
                url,
                timeout=_TIMEOUT,
                follow_redirects=True,
                headers=_BROWSER_HEADERS,
            )
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            # Could not access the page → unknown, not "no schema". Score None
            # (not 0) so it's excluded from the weighted average rather than
            # dragging it down as if schema were genuinely absent.
            return _result(None, f"Could not fetch homepage: {exc}", [], [])

    raw_blocks = _JSONLD_RE.findall(html)

    all_types: list[str] = []
    for block in raw_blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        all_types.extend(_collect_types(data))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_types: list[str] = []
    for t in all_types:
        if t not in seen:
            seen.add(t)
            unique_types.append(t)

    found_core = [t for t in _CORE_TYPES if t in seen]
    score = round(len(found_core) / len(_CORE_TYPES) * 100)

    if score == 0:
        desc = "No Schema.org JSON-LD markup found on the homepage."
    elif score == 100:
        desc = "All 4 core Schema.org types present (Organization, Product, FAQPage, BreadcrumbList)."
    else:
        missing = sorted(_CORE_TYPES - set(found_core))
        desc = f"{len(found_core)} of 4 core schema types found; missing: {', '.join(missing)}."

    return _result(score, desc, found_core, unique_types)


def _collect_types(obj: object) -> list[str]:
    """Recursively collect all @type values from a JSON-LD object."""
    types: list[str] = []
    if isinstance(obj, dict):
        t = obj.get("@type")
        if isinstance(t, str):
            types.append(t)
        elif isinstance(t, list):
            types.extend(t)
        for v in obj.values():
            types.extend(_collect_types(v))
    elif isinstance(obj, list):
        for item in obj:
            types.extend(_collect_types(item))
    return types


def _result(
    score: int | None,
    description: str,
    found_core: list[str],
    all_types: list[str],
) -> dict:
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": score,
        "description": description,
        "details": {
            "found_core_types": found_core,
            "all_types_detected": all_types,
            "core_types_checked": sorted(_CORE_TYPES),
        },
        "error": None,
    }
