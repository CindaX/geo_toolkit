"""Lightweight website crawler used as input for GEO analysis.

Given a homepage URL, :func:`crawl_website` fetches the home page plus a
handful of likely-relevant subpages (About / Product / Service pages),
extracts their visible text, and returns a normalized dict. Results are
cached on disk for 24 hours so repeated runs during development don't keep
hitting the network.

Text extraction strategy (primary → fallback):
  1. Jina Reader API (https://r.jina.ai/) — renders JS, strips nav/cart noise,
     returns LLM-friendly Markdown. Used for *text* content only.
  2. httpx + BeautifulSoup — raw HTML fetch with tag stripping. Used when
     Jina is unavailable or returns empty content, and always used to obtain
     raw HTML (home_html / about_html) for structured-data analysis.

# TODO(future): 当 Jina 限流解除后，改用 X-Remove-Selector / X-Target-Selector
# header 在服务端剔除噪音，比客户端正则更通用。详见 Day 3 调试笔记。
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

_JINA_BASE_URL: str = "https://r.jina.ai/"
_JINA_TIMEOUT: float = 20.0

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
    text: str        # Jina Markdown when available, BeautifulSoup text as fallback


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
    home_text: str           # Jina Markdown when available, BS4 fallback
    about_html: str          # "" if none found
    product_pages: list[PageContent]
    combined_text: str       # home_text + about_text + product page texts
    metadata: CrawlMetadata
    fetch_method: dict[str, str]   # url → "jina" | "httpx"


# --- Public API ----------------------------------------------------------

def crawl_website(url: str, max_pages: int = 5) -> CrawlResult:
    """Fetch a website and return a normalized :class:`CrawlResult`.

    Text content is fetched via Jina Reader API first (LLM-friendly Markdown,
    JS-rendered), falling back to httpx + BeautifulSoup when Jina is unavailable.
    Raw HTML (home_html / about_html) is always fetched via httpx so downstream
    schema and structured-data analyzers have access to the full DOM.

    Args:
        url: Homepage URL (must include scheme).
        max_pages: Maximum total pages to fetch, including the homepage.

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


# --- Jina Reader ---------------------------------------------------------

def _fetch_via_jina(url: str) -> str | None:
    """Fetch *url* via Jina Reader API and return clean Markdown text.

    Returns ``None`` on any error or empty response so callers can fall back
    to httpx + BeautifulSoup without special-casing.
    """
    jina_url = f"{_JINA_BASE_URL}{url}"
    try:
        resp = httpx.get(
            jina_url,
            timeout=_JINA_TIMEOUT,
            follow_redirects=True,
            headers={
                "X-Return-Format": "markdown",
                "Accept": "text/plain",
                "X-Engine": "browser",
            },
        )
        if resp.status_code == 200:
            text = resp.text.strip()
            if text:
                cleaned = _strip_shopify_noise(text)
                print(f"[CRAWLER] _fetch_via_jina returning {len(cleaned)} chars from {url}", flush=True)
                return cleaned
    except Exception:
        pass
    print(f"[CRAWLER] _fetch_via_jina FAILED for {url} — falling back to httpx", flush=True)
    return None


def _strip_shopify_noise(markdown: str) -> str:
    """Remove Shopify cart-drawer / country-selector noise from Jina Markdown.

    Shopify themes inject a hidden cart drawer and flag-based currency/country
    switchers into the page body.  Jina serializes these DOM elements before
    (and sometimes between) the real product content, polluting the token window.

    Strategy:
      1. Detect noise presence in the first 3 KB via known signal phrases.
      2. Find the first real H1 heading (single '#') that sits after Jina's own
         metadata block (~first 500 chars) and is not a known noise heading.
         This is where the page body actually begins.
      3. Within the extracted body, also strip any embedded
         '## Delivery Destination' blocks (Shopify injects these per-section).
    """
    print(f"[STRIP] called with {len(markdown)} chars, first 50: {markdown[:50]!r}", flush=True)
    if not markdown or len(markdown) < 1000:
        return markdown

    _NOISE_MARKERS = [
        "Your cart is empty",
        "Cart is empty",
        "Country/Region",
        "Country / Region",
        "Choose your country",
    ]

    has_noise = any(m.lower() in markdown[:3000].lower() for m in _NOISE_MARKERS)
    if not has_noise:
        return markdown

    # Known noise H1 patterns — skip these when scanning for real content.
    _NOISE_H1 = re.compile(r"^# (?:Cart|Subtotal|\[!\[)", re.IGNORECASE)

    for m in re.finditer(r"^# .+", markdown, re.MULTILINE):
        if m.start() < 500:
            continue  # Jina always puts a metadata title here — skip it
        if _NOISE_H1.match(m.group()):
            continue
        # Found the first real H1: start output here.
        body = markdown[m.start():]
        # Also remove any embedded "## Delivery Destination" blocks that Shopify
        # injects into each page section (up to 3 KB of country-flag list each).
        body = re.sub(
            r"\n## Delivery Destination\n.{50,4000}?(?=\n## |\n# |\Z)",
            "\n",
            body,
            flags=re.DOTALL,
        )
        return body

    # Fallback: structure not recognized — return as-is.
    return markdown


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
        pass


# --- Crawl implementation -----------------------------------------------

def _do_crawl(url: str, max_pages: int) -> CrawlResult:
    fetch_method: dict[str, str] = {}
    empty: CrawlResult = {
        "url": url,
        "home_html": "",
        "home_text": "",
        "about_html": "",
        "product_pages": [],
        "combined_text": "",
        "metadata": {"crawled_at": _now_iso(), "url_count": 0},
        "fetch_method": fetch_method,
    }

    with httpx.Client(
        timeout=_REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        # Always fetch raw HTML for structured-data analyzers.
        home_html = _fetch(client, url)
        if home_html is None:
            empty["metadata"]["error"] = f"Failed to fetch home page: {url}"
            return empty

        home_soup = BeautifulSoup(home_html, "lxml")
        metadata = _extract_metadata(home_soup)

        # Prefer Jina text for the homepage; fall back to BeautifulSoup.
        jina_home = _fetch_via_jina(url)
        if jina_home:
            home_text = jina_home
            fetch_method[url] = "jina"
        else:
            home_text = _extract_text(home_soup)
            fetch_method[url] = "httpx"

        about_html, about_text, product_pages = _fetch_subpages(
            client,
            base_url=url,
            home_soup=home_soup,
            max_pages=max_pages,
            fetch_method=fetch_method,
        )

        combined_chunks = [home_text]
        if about_text:
            combined_chunks.append(about_text)
        for p in product_pages:
            combined_chunks.append(p["text"])
        combined_text = "\n\n".join(c for c in combined_chunks if c).strip()

        url_count = 1 + (1 if about_html else 0) + len(product_pages)
        metadata["crawled_at"] = _now_iso()
        metadata["url_count"] = url_count

        return {
            "url": url,
            "home_html": home_html,
            "home_text": home_text,
            "about_html": about_html,
            "product_pages": product_pages,
            "combined_text": combined_text,
            "metadata": metadata,
            "fetch_method": fetch_method,
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
    fetch_method: dict[str, str],
) -> tuple[str, str, list[PageContent]]:
    """Discover and fetch up to ``max_pages-1`` About / Product subpages.

    Returns ``(about_html, about_text, product_pages)``.
    Text for each page is sourced from Jina when available, BeautifulSoup otherwise.
    """
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
    about_text = ""

    if about_candidates and remaining > 0:
        about_url = about_candidates[0]
        html = _fetch(client, about_url)
        if html:
            about_html = html
            remaining -= 1
            jina_about = _fetch_via_jina(about_url)
            if jina_about:
                about_text = jina_about
                fetch_method[about_url] = "jina"
            else:
                about_text = _extract_text(BeautifulSoup(html, "lxml"))
                fetch_method[about_url] = "httpx"

    product_pages: list[PageContent] = []
    for candidate in product_candidates:
        if remaining <= 0:
            break
        html = _fetch(client, candidate)
        if html is None:
            continue
        jina_page = _fetch_via_jina(candidate)
        if jina_page:
            text = jina_page
            fetch_method[candidate] = "jina"
        else:
            text = _extract_text(BeautifulSoup(html, "lxml"))
            fetch_method[candidate] = "httpx"
        product_pages.append({"url": candidate, "html": html, "text": text})
        remaining -= 1

    return about_html, about_text, product_pages


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
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
