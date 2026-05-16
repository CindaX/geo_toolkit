"""Dimension 6 — E-E-A-T Signals (weight 15%).

Aggregates text from crawled pages and asks Claude to score 6 trust-signal categories.
"""

from __future__ import annotations

from pathlib import Path

from shared.claude_client import ask_claude_json

DIMENSION = "eeat_signals"
WEIGHT = 0.15

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "geo_audit" / "06_eeat_signals.txt"
_MAX_TEXT = 6000


def analyze(url: str, brand_name: str, site_text: str, **_kwargs) -> dict:
    print(f"[DEBUG] eeat_signals starting...", flush=True)
    text = site_text[:_MAX_TEXT].strip()
    if not text:
        return _error_result("No site content available for E-E-A-T analysis.")

    prompt_tmpl = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        prompt_tmpl
        .replace("{brand_name}", brand_name)
        .replace("{site_text}", text)
    )

    data = ask_claude_json(prompt)

    score = int(data.get("score", 0))
    print(f"[DEBUG] eeat_signals done, score={score}", flush=True)
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": score,
        "description": str(data.get("description", f"E-E-A-T score: {score}/100.")),
        "details": {
            "signals": data.get("signals", {}),
            "missing": data.get("missing", []),
        },
        "error": None,
    }


def _error_result(msg: str) -> dict:
    return {
        "dimension": DIMENSION,
        "weight": WEIGHT,
        "score": None,
        "description": msg,
        "details": {"signals": {}, "missing": []},
        "error": msg,
    }
