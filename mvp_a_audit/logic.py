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

    # Merge per-dimension fixes (static templates + 1 LLM call for 5 dims).
    _merge_fixes_into_results(results, brand_name, url, industry)

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
) -> dict[str, dict]:
    """Generate one fix per LLM-handled dimension (brand_clarity / content_citability /
    eeat_signals / competitive / factual_density).

    Returns a ``{dimension_key: fix_dict}`` map for direct merging into
    ``run_audit``'s results. The fix_dict is normalized to the unified fix
    schema (see :mod:`shared.fix_templates`): keys ``title``, ``why_matters``,
    ``how_to_fix``, ``code_snippet``, ``difficulty``, ``impact``. The 3
    deterministic dimensions are handled by static templates and are NOT
    included here.

    Never raises — returns ``{}`` if the LLM call fails or returns bad JSON.
    """
    from shared.fix_templates import WHY_MATTERS

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
    if not isinstance(data, dict) or data.get("error") or not data.get("fixes"):
        return {}

    out: dict[str, dict] = {}
    for fix in data.get("fixes", []) or []:
        dim = fix.get("dimension")
        if not dim:
            continue
        # Normalize to the unified schema used by both static + LLM fixes so
        # the PDF renderer can treat them identically.
        out[dim] = {
            "title":          str(fix.get("title", "")).strip(),
            "why_matters":    WHY_MATTERS.get(dim, ""),
            "how_to_fix":     str(fix.get("method", "")).strip(),
            "code_snippet":   str(fix.get("code_snippet", "") or "").strip(),
            "difficulty":     str(fix.get("difficulty", "")).strip(),
            "impact":         str(fix.get("impact", "")).strip(),
            "where_to_apply": str(fix.get("where_to_apply", "") or "").strip(),
        }
    return out


def _merge_fixes_into_results(
    results: dict[str, dict],
    brand_name: str,
    url: str,
    industry: str,
) -> None:
    """Mutate ``results`` in place to add a ``fix`` field to each dimension.

    - Static templates (3 deterministic dimensions) — instant, no LLM cost.
    - LLM recommendations (5 dimensions) — one Claude call covers all 5.
    - Failed analyzers (score is None / error set) — ``fix`` stays ``None``.

    All paths are graceful: if Claude is unreachable, static-template
    dimensions still get fixes and the LLM ones get ``None``. The PDF
    renderer treats ``None`` as "skip the fix sections for this dimension".
    """
    from shared.fix_templates import generate_static_fix

    # Phase 1: static fixes (instant, deterministic)
    for dim_key, result in results.items():
        if result.get("error") or result.get("score") is None:
            result["fix"] = None
            continue
        static_fix = generate_static_fix(
            dim_key,
            result.get("details", {}) or {},
            brand_name,
            url,
        )
        result["fix"] = static_fix  # may be None if dim_key is LLM-handled

    # Phase 2: LLM fixes for the 5 dimensions that don't have a static template
    try:
        llm_fixes = generate_recommendations(results, brand_name, url, industry)
    except Exception as exc:
        logger.warning("Per-dimension LLM fixes failed: %s", exc)
        llm_fixes = {}

    for dim_key, fix in llm_fixes.items():
        if dim_key in results and results[dim_key].get("fix") is None and not results[dim_key].get("error"):
            results[dim_key]["fix"] = fix


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
