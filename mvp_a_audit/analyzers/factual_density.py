"""Dimension 8 — Factual Density (weight 10%).

Sends the homepage text to Claude, which classifies sentences as factual vs vague.
"""

from __future__ import annotations

from pathlib import Path

from shared.claude_client import ask_claude_json

DIMENSION = "factual_density"
WEIGHT = 0.10

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "geo_audit" / "08_factual_density.txt"
_MAX_TEXT = 3000


def analyze(url: str, brand_name: str, homepage_text: str, **_kwargs) -> dict:
    print(f"[DEBUG] factual_density starting...", flush=True)
    text = homepage_text[:_MAX_TEXT].strip()
    if not text:
        return _error_result("No homepage text available for factual density analysis.")

    prompt_tmpl = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        prompt_tmpl
        .replace("{brand_name}", brand_name)
        .replace("{homepage_text}", text)
    )

    data = ask_claude_json(prompt)

    score = int(data.get("score", 0))
    print(f"[DEBUG] factual_density done, score={score}", flush=True)
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": score,
        "description": str(data.get("description", f"Factual density score: {score}/100.")),
        "details": {
            "factual_count": data.get("factual_count"),
            "vague_count": data.get("vague_count"),
            "total_count": data.get("total_count"),
            "vague_examples": data.get("vague_examples", []),
        },
        "error": None,
    }


def _error_result(msg: str) -> dict:
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": None,
        "description": msg,
        "details": {"vague_examples": []},
        "error": msg,
    }
