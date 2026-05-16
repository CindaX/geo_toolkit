"""Dimension 4 — Brand Clarity (weight 15%).

Uses Claude to score how clearly the homepage communicates what the company does.
Also produces the "AI understanding" one-liner used as the viral display element.
"""

from __future__ import annotations

from pathlib import Path

from shared.claude_client import ask_claude_json

DIMENSION = "brand_clarity"
WEIGHT = 0.15

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "geo_audit" / "04_brand_clarity.txt"
_MAX_TEXT = 3000


def analyze(url: str, brand_name: str, industry: str, homepage_text: str, **_kwargs) -> dict:
    print(f"[DEBUG] brand_clarity starting...", flush=True)
    text = homepage_text[:_MAX_TEXT].strip()
    if not text:
        return _error_result("No homepage text available for analysis.")

    prompt_tmpl = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        prompt_tmpl
        .replace("{brand_name}", brand_name)
        .replace("{url}", url)
        .replace("{industry}", industry)
        .replace("{homepage_text}", text)
    )

    data = ask_claude_json(prompt)

    score = int(data.get("score", 0))
    ai_understanding = str(data.get("ai_understanding", ""))
    score_reason = str(data.get("score_reason", ""))
    issues = list(data.get("issues", []))

    desc = score_reason or f"Brand clarity score: {score}/100."
    print(f"[DEBUG] brand_clarity done, score={score}", flush=True)
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": score,
        "description": desc,
        "details": {
            "ai_understanding": ai_understanding,
            "issues": issues,
        },
        "error": None,
    }


def _error_result(msg: str) -> dict:
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": None,
        "description": msg,
        "details": {"ai_understanding": None, "issues": []},
        "error": msg,
    }
