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
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def analyze(url: str, **_kwargs) -> dict:
    try:
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return _result(0, f"Could not fetch homepage: {exc}", [], [])

    html = resp.text
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
    score: int,
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
