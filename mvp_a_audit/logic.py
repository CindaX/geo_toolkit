"""MVP-A audit orchestration, scoring, and recommendations."""

from __future__ import annotations

import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup

from shared._openrouter import get_openrouter_client
from shared.claude_client import ask_claude_json, get_usage_stats, reset_usage_stats
from shared.crawler import crawl_website

from mvp_a_audit.analyzers import (
    brand_clarity,
    competitive,
    content_citability,
    crawler_access,
    eeat_signals,
    factual_density,
    llms_txt,
    schema_markup,
)

logger = logging.getLogger(__name__)

# ── Cost guard ────────────────────────────────────────────────────────────────
_COST_WARNING_USD = 0.30
_INPUT_COST_PER_TOK = 3.0 / 1_000_000   # Sonnet 4.5
_OUTPUT_COST_PER_TOK = 15.0 / 1_000_000

# ── Grade thresholds ──────────────────────────────────────────────────────────
_GRADES = [
    (80, "🏆 AI-Optimized"),
    (65, "✅ Strong Foundation"),
    (50, "⚠️ Needs Work"),
    (30, "🚨 Significant Gaps"),
    (0,  "🆘 Critical"),
]

# ── Analyzer registry ─────────────────────────────────────────────────────────
# Ordered list of (dimension_key, module) used by both the programmatic API
# and the Streamlit app's progress loop.
ANALYZERS: list[tuple[str, object]] = [
    ("crawler_access",     crawler_access),
    ("llms_txt",           llms_txt),
    ("schema_markup",      schema_markup),
    ("brand_clarity",      brand_clarity),
    ("content_citability", content_citability),
    ("eeat_signals",       eeat_signals),
    ("competitive",        competitive),
    ("factual_density",    factual_density),
]

_WEIGHTS: dict[str, float] = {
    "crawler_access":     0.10,
    "llms_txt":           0.08,
    "schema_markup":      0.12,
    "brand_clarity":      0.15,
    "content_citability": 0.20,
    "eeat_signals":       0.15,
    "competitive":        0.10,
    "factual_density":    0.10,
}

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts" / "geo_audit"


# ── Input preparation ─────────────────────────────────────────────────────────

def prepare_inputs(url: str, brand_name: str, industry: str) -> dict:
    """Crawl the website and build the shared kwargs dict for all analyzers."""
    crawl = crawl_website(url, max_pages=5)
    home_html = crawl["home_html"] or ""
    # Prefer Jina-cleaned text from the crawl; fall back to BS4 extraction.
    homepage_text = crawl.get("home_text") or _html_to_text(home_html)
    site_text = crawl["combined_text"] or ""

    return {
        "url": url,
        "brand_name": brand_name,
        "industry": industry,
        "homepage_html": home_html,
        "homepage_text": homepage_text,
        "site_text": site_text,
        "product_pages": crawl.get("product_pages", []),
    }


# ── Programmatic full-audit (non-streaming) ───────────────────────────────────

def run_audit(url: str, brand_name: str, industry: str) -> dict:
    """Run all 8 analyzers in parallel and return a complete audit result dict.

    Suitable for programmatic use. The Streamlit app drives analyzers directly
    so it can update a progress bar via as_completed().
    """
    get_openrouter_client()  # pre-warm on calling thread
    reset_usage_stats()

    inputs = prepare_inputs(url, brand_name, industry)
    results: dict[str, dict] = {}

    futures_map: dict = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for key, mod in ANALYZERS:
            futures_map[executor.submit(mod.analyze, **inputs)] = key  # type: ignore[attr-defined]

        for future in as_completed(futures_map):
            key = futures_map[future]
            try:
                results[key] = future.result()
            except Exception:
                results[key] = _failed_result(key, traceback.format_exc())
            _warn_if_over_budget()

    return _build_audit_result(url, brand_name, industry, results)


# ── Score computation ─────────────────────────────────────────────────────────

def compute_geo_score(results: dict[str, dict]) -> float | None:
    """Weighted average of non-null scores, re-normalized over available weight."""
    total_weight = 0.0
    weighted_sum = 0.0
    for key, result in results.items():
        score = result.get("score")
        weight = _WEIGHTS.get(key, 0.0)
        if score is not None:
            weighted_sum += float(score) * weight
            total_weight += weight
    if total_weight == 0:
        return None
    return round(weighted_sum / total_weight, 1)


def get_grade(score: float | None) -> str:
    if score is None:
        return "⚠️ Incomplete"
    for threshold, label in _GRADES:
        if score >= threshold:
            return label
    return "🆘 Critical"


# ── Paid feature: recommendations ─────────────────────────────────────────────

def generate_recommendations(
    results: dict[str, dict],
    brand_name: str,
    url: str,
    industry: str,
) -> dict:
    """Generate top-5 prioritized fix recommendations (paid-tier feature)."""
    results_summary = {
        key: {
            "score": r.get("score"),
            "weight": _WEIGHTS.get(key, 0.0),
            "description": r.get("description", ""),
        }
        for key, r in results.items()
    }

    prompt_tmpl = (_PROMPT_DIR / "recommendations.txt").read_text(encoding="utf-8")
    prompt = (
        prompt_tmpl
        .replace("{brand_name}", brand_name)
        .replace("{url}", url)
        .replace("{industry}", industry)
        .replace("{audit_results_json}", json.dumps(results_summary, ensure_ascii=False, indent=2))
    )

    data = ask_claude_json(prompt)
    _warn_if_over_budget()
    return data


# ── Helpers ───────────────────────────────────────────────────────────────────

def estimate_cost() -> float:
    stats = get_usage_stats()
    return round(
        stats["input_tokens"] * _INPUT_COST_PER_TOK
        + stats["output_tokens"] * _OUTPUT_COST_PER_TOK,
        4,
    )


def _warn_if_over_budget() -> None:
    cost = estimate_cost()
    if cost > _COST_WARNING_USD:
        logger.warning(
            "Audit cost $%.4f exceeded $%.2f warning threshold — possible prompt bloat.",
            cost, _COST_WARNING_USD,
        )


def _build_audit_result(
    url: str, brand_name: str, industry: str, results: dict[str, dict]
) -> dict:
    geo_score = compute_geo_score(results)
    bc_details = results.get("brand_clarity", {}).get("details", {})
    return {
        "url": url,
        "brand_name": brand_name,
        "industry": industry,
        "geo_score": geo_score,
        "geo_grade": get_grade(geo_score),
        "ai_understanding": bc_details.get("ai_understanding"),
        "results": results,
        "usage": get_usage_stats(),
        "estimated_cost_usd": estimate_cost(),
    }


def _failed_result(key: str, tb: str) -> dict:
    return {
        "dimension": key,
        "weight": _WEIGHTS.get(key, 0.0),
        "score": None,
        "description": "Analysis failed — click Retry to re-run this dimension.",
        "details": {},
        "error": tb,
    }


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())
