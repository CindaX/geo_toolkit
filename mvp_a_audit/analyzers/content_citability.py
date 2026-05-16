"""Dimension 5 — Content Citability (weight 20%).

Crawls the homepage + About + up to 2 product pages, then asks Claude to
evaluate what fraction of paragraphs are self-contained and citable by AI.

Product page selection strategy:
  1. Extract nav links from the homepage containing product-related path keywords.
  2. Fallback: parse sitemap.xml if no nav links found.
  Take the first 2 matching URLs.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from shared.claude_client import ask_claude_json

DIMENSION = "content_citability"
WEIGHT = 0.20

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "geo_audit" / "05_content_citability.txt"
_PRODUCT_KEYWORDS = re.compile(
    r"/(product|service|feature|pricing|solutions?|platform|how-it-works)",
    re.IGNORECASE,
)
_MAX_CHARS_PER_PAGE = 2500
_TIMEOUT = 12


def analyze(url: str, brand_name: str, homepage_html: str, **_kwargs) -> dict:
    print(f"[DEBUG] content_citability starting...", flush=True)
    pages_text: list[str] = []

    # Homepage text
    home_text = _extract_text(homepage_html)
    if home_text:
        pages_text.append(f"[Homepage]\n{home_text[:_MAX_CHARS_PER_PAGE]}")

    # Find product-related pages
    product_urls = _find_product_urls(url, homepage_html)
    for purl in product_urls[:2]:
        page_text = _fetch_text(purl)
        if page_text:
            pages_text.append(f"[{purl}]\n{page_text[:_MAX_CHARS_PER_PAGE]}")

    if not pages_text:
        return _error_result("No page content available for analysis.")

    combined = "\n\n".join(pages_text)
    prompt_tmpl = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        prompt_tmpl
        .replace("{brand_name}", brand_name)
        .replace("{page_texts}", combined)
    )

    data = ask_claude_json(prompt)

    score = int(data.get("score", 0))
    print(f"[DEBUG] content_citability done, score={score}", flush=True)
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": score,
        "description": str(data.get("description", f"Citability score: {score}/100.")),
        "details": {
            "citable_count": data.get("citable_count"),
            "total_count": data.get("total_count"),
            "examples_good": data.get("examples_good", []),
            "examples_bad": data.get("examples_bad", []),
            "pages_analyzed": len(pages_text),
        },
        "error": None,
    }


def _find_product_urls(base_url: str, homepage_html: str) -> list[str]:
    soup = BeautifulSoup(homepage_html, "html.parser")
    base_domain = urlparse(base_url).netloc
    found: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_url, href)
        if urlparse(full).netloc != base_domain:
            continue
        path = urlparse(full).path
        if _PRODUCT_KEYWORDS.search(path) and full not in found:
            found.append(full)
        if len(found) >= 2:
            return found

    if not found:
        found = _from_sitemap(base_url)

    return found[:2]


def _from_sitemap(base_url: str) -> list[str]:
    sitemap_url = urljoin(base_url.rstrip("/") + "/", "sitemap.xml")
    try:
        resp = httpx.get(sitemap_url, timeout=_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return []
        urls = re.findall(r"<loc>(https?://[^<]+)</loc>", resp.text)
        return [u for u in urls if _PRODUCT_KEYWORDS.search(urlparse(u).path)][:2]
    except Exception:
        return []


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
