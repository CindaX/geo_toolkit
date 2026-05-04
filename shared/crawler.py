"""Lightweight website crawler used as input for GEO analysis.

Given a homepage URL, :func:`crawl_website` fetches the home page plus a
handful of likely-relevant subpages (About / Product / Service pages),
extracts their visible text, and returns a normalized dict. Results are
cached on disk for 24 hours so repeated runs during development don't keep
hitting the network.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from urllib.parse import urljoin, urlparse, urldefrag

import httpx
from bs4 import BeautifulSoup

from shared import PROJECT_ROOT

# --- Constants -----------------------------------------------------------

_CACHE_DIR: Path = PROJECT_ROOT / "data" / "cache"
_CACHE_TTL_SECONDS: int = 24 * 60 * 60
_REQUEST_TIMEOUT: float = 10.0
_USER_AGENT: str = (
    "Mozilla/5.0 (compatible; geo-toolkit/0.1; +https://example.invalid/bot)"
)

# URL path patterns we use to classify candidate links.
_ABOUT_PATTERN = re.compile(r"/(about|company|who-we-are|team)(/|$)", re.I)
_PRODUCT_PATTERN = re.compile(
    r"/(product|products|feature|features|service|services|solution|solutions|pricing)(/|$)",
    re.I,
)


# --- Types ---------------------------------------------------------------

class PageContent(TypedDict):
    """A single fetched subpage."""

    url: str
    html: str
    text: str


class CrawlMetadata(TypedDict, total=False):
    """Metadata derived from the home page."""

    title: str
    description: str
    og_title: str
    og_description: str
    crawled_at: str
    url_count: int
    error: str  # only present on full-failure shells


class CrawlResult(TypedDict):
    """Normalized crawler output."""

    url: str
    home_html: str
    about_html: str  # "" if none found
    product_pages: list[PageContent]
    combined_text: str
    metadata: CrawlMetadata


# --- Public API ----------------------------------------------------------

def crawl_website(url: str, max_pages: int = 5) -> CrawlResult:
    """Fetch a website and return a normalized :class:`CrawlResult`.

    The crawl is best-effort: if individual subpages fail, they are skipped
    rather than aborting the whole call. If the home page itself fails to
    fetch, the function returns a result whose ``metadata.error`` describes
    the failure (instead of raising).

    Args:
        url: Homepage URL (must include scheme).
        max_pages: Maximum total pages to fetch, including the homepage.
            Must be ``>= 1``.

    Returns:
        A :class:`CrawlResult` dict.
    """
    if max_pages < 1:
        max_pages = 1

    cached = _read_cache(url, max_pages)
    if cached is not None:
        return cached

    result = _do_crawl(url, max_pages)
    _write_cache(url, max_pages, result)
    return result


# --- Cache ---------------------------------------------------------------

def _cache_path(url: str, max_pages: int) -> Path:
    key = f"{url}|{max_pages}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return _CACHE_DIR / f"crawl_{digest}.json"


def _read_cache(url: str, max_pages: int) -> CrawlResult | None:
    path = _cache_path(url, max_pages)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cached_at = float(raw.get("cached_at", 0))
    if time.time() - cached_at > _CACHE_TTL_SECONDS:
        return None
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload  # type: ignore[return-value]


def _write_cache(url: str, max_pages: int, payload: CrawlResult) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(url, max_pages)
        path.write_text(
            json.dumps({"cached_at": time.time(), "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        # Cache is best-effort; never fail the crawl because we couldn't write it.
        pass


# --- Crawl implementation -----------------------------------------------

def _do_crawl(url: str, max_pages: int) -> CrawlResult:
    empty: CrawlResult = {
        "url": url,
        "home_html": "",
        "about_html": "",
        "product_pages": [],
        "combined_text": "",
        "metadata": {"crawled_at": _now_iso(), "url_count": 0},
    }

    with httpx.Client(
        timeout=_REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        home_html = _fetch(client, url)
        if home_html is None:
            empty["metadata"]["error"] = f"Failed to fetch home page: {url}"
            return empty

        home_soup = BeautifulSoup(home_html, "lxml")
        metadata = _extract_metadata(home_soup)
        home_text = _extract_text(home_soup)

        about_html, product_pages = _fetch_subpages(
            client, base_url=url, home_soup=home_soup, max_pages=max_pages
        )

        combined_chunks = [home_text]
        if about_html:
            combined_chunks.append(_extract_text(BeautifulSoup(about_html, "lxml")))
        for p in product_pages:
            combined_chunks.append(p["text"])
        combined_text = "\n\n".join(c for c in combined_chunks if c).strip()

        url_count = 1 + (1 if about_html else 0) + len(product_pages)
        metadata["crawled_at"] = _now_iso()
        metadata["url_count"] = url_count

        return {
            "url": url,
            "home_html": home_html,
            "about_html": about_html,
            "product_pages": product_pages,
            "combined_text": combined_text,
            "metadata": metadata,
        }


def _fetch(client: httpx.Client, url: str) -> str | None:
    """Fetch ``url`` and return the body text, or ``None`` on any failure."""
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except (httpx.HTTPError, ValueError):
        return None
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype.lower() and ctype:
        return None
    return resp.text


def _fetch_subpages(
    client: httpx.Client,
    *,
    base_url: str,
    home_soup: BeautifulSoup,
    max_pages: int,
) -> tuple[str, list[PageContent]]:
    """Discover and fetch up to ``max_pages-1`` About / Product subpages."""
    base_host = urlparse(base_url).netloc
    seen: set[str] = {_normalize_url(base_url)}

    about_candidates: list[str] = []
    product_candidates: list[str] = []

    for a in home_soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        absolute = _normalize_url(absolute)
        if urlparse(absolute).netloc != base_host:
            continue
        if absolute in seen:
            continue
        path = urlparse(absolute).path or "/"
        if _ABOUT_PATTERN.search(path):
            about_candidates.append(absolute)
            seen.add(absolute)
        elif _PRODUCT_PATTERN.search(path):
            product_candidates.append(absolute)
            seen.add(absolute)

    remaining = max_pages - 1
    about_html = ""
    if about_candidates and remaining > 0:
        html = _fetch(client, about_candidates[0])
        if html:
            about_html = html
            remaining -= 1

    product_pages: list[PageContent] = []
    for candidate in product_candidates:
        if remaining <= 0:
            break
        html = _fetch(client, candidate)
        if html is None:
            continue
        text = _extract_text(BeautifulSoup(html, "lxml"))
        product_pages.append({"url": candidate, "html": html, "text": text})
        remaining -= 1

    return about_html, product_pages


def _normalize_url(url: str) -> str:
    """Strip fragment and trailing slash for dedupe purposes."""
    no_frag, _ = urldefrag(url)
    if no_frag.endswith("/") and len(no_frag) > len(urlparse(no_frag).scheme) + 3:
        no_frag = no_frag.rstrip("/")
    return no_frag


def _extract_metadata(soup: BeautifulSoup) -> CrawlMetadata:
    """Pull title / description / OG tags from the home page."""
    meta: CrawlMetadata = {}
    if soup.title and soup.title.string:
        meta["title"] = soup.title.string.strip()

    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        meta["description"] = desc["content"].strip()

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        meta["og_title"] = og_title["content"].strip()

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        meta["og_description"] = og_desc["content"].strip()

    return meta


def _extract_text(soup: BeautifulSoup) -> str:
    """Return human-visible text, stripping nav/footer/script/style noise."""
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse whitespace.
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
