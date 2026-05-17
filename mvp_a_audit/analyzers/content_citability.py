"""Dimension 5 — Content Citability (weight 20%).

Uses Jina-cleaned text from the crawler result (homepage_text + product_pages)
to ask Claude what fraction of paragraphs are self-contained and citable by AI.

No independent HTTP fetching — all text comes from the shared crawl inputs
so Shopify cart noise is already stripped before this analyzer runs.
"""

from __future__ import annotations

from pathlib import Path

from shared.claude_client import ask_claude_json

DIMENSION = "content_citability"
WEIGHT = 0.20

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "geo_audit" / "05_content_citability.txt"
_MAX_CHARS_PER_PAGE = 2000   # per-page cap before combining
_MAX_COMBINED = 5000         # total chars sent to Claude


def analyze(
    url: str,
    brand_name: str,
    homepage_text: str,
    product_pages: list,
    **_kwargs,
) -> dict:
    print(f"[DEBUG] content_citability starting...", flush=True)
    pages_text: list[str] = []

    # Homepage: already Jina-cleaned by the crawler
    if homepage_text and homepage_text.strip():
        pages_text.append(f"[Homepage]\n{homepage_text.strip()[:_MAX_CHARS_PER_PAGE]}")

    # Product pages: use the Jina-cleaned text field from the crawl result
    for page in product_pages[:2]:
        text = (page.get("text") or "").strip()
        if text:
            label = page.get("url", "Product page")
            pages_text.append(f"[{label}]\n{text[:_MAX_CHARS_PER_PAGE]}")

    if not pages_text:
        return _error_result("No page content available for analysis.")

    combined = "\n\n".join(pages_text)[:_MAX_COMBINED]

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


def _error_result(msg: str) -> dict:
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": None,
        "description": msg,
        "details": {},
        "error": msg,
    }
