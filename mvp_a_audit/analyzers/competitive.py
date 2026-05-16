"""Dimension 7 — Competitive Positioning (weight 10%).

Checks the homepage and any compare/vs/pricing pages for competitive content.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from shared.claude_client import ask_claude_json

DIMENSION = "competitive"
WEIGHT = 0.10

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "geo_audit" / "07_competitive.txt"
_COMPARE_KEYWORDS = re.compile(r"/(compare|vs|versus|alternatives?|why-us|competitor)", re.IGNORECASE)
_MAX_CHARS_PER_PAGE = 3000
_TIMEOUT = 12


def analyze(url: str, brand_name: str, homepage_html: str, **_kwargs) -> dict:
    print(f"[DEBUG] competitive starting...", flush=True)
    pages_text: list[str] = []

    home_text = _extract_text(homepage_html)
    if home_text:
        pages_text.append(f"[Homepage]\n{home_text[:_MAX_CHARS_PER_PAGE]}")

    compare_urls = _find_compare_urls(url, homepage_html)
    for curl in compare_urls[:2]:
        page_text = _fetch_text(curl)
        if page_text:
            pages_text.append(f"[{curl}]\n{page_text[:_MAX_CHARS_PER_PAGE]}")

    if not pages_text:
        return _error_result("No page content available for competitive analysis.")

    combined = "\n\n".join(pages_text)
    prompt_tmpl = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        prompt_tmpl
        .replace("{brand_name}", brand_name)
        .replace("{page_texts}", combined)
    )

    data = ask_claude_json(prompt)

    score = int(data.get("score", 0))
    print(f"[DEBUG] competitive done, score={score}", flush=True)
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": score,
        "description": str(data.get("description", f"Competitive positioning score: {score}/100.")),
        "details": {
            "has_comparison_page": data.get("has_comparison_page", False),
            "has_named_competitors": data.get("has_named_competitors", False),
            "has_differentiators": data.get("has_differentiators", False),
            "found_content": data.get("found_content", ""),
        },
        "error": None,
    }


def _find_compare_urls(base_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(base_url).netloc
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])
        if urlparse(full).netloc != base_domain:
            continue
        if _COMPARE_KEYWORDS.search(urlparse(full).path) and full not in found:
            found.append(full)
        if len(found) >= 2:
            break
    return found


def _fetch_text(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        if resp.status_code == 200:
            return _extract_text(resp.text)
    except Exception:
        pass
    return ""


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


def _error_result(msg: str) -> dict:
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": None,
        "description": msg,
        "details": {},
        "error": msg,
    }
