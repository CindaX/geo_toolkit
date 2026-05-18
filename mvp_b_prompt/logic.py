"""MVP-B orchestration: prompt generation, AI-answer simulation, opportunity ranking.

Pipeline:
  1. generate_prompts()    → 20 prompts (5 types × 4) + AI categorization
  2. simulate_answers()    → top-3 brand citation per prompt (1 LLM call)
  3. find_opportunities()  → top-5 gap prompts where competitors win

All metrics (SOV, position score, category strength, direct comparisons,
control-prompt presence, awareness classification) are computed locally
from the simulation result — no extra LLM calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shared.claude_client import (
    ask_claude_json,
    get_usage_stats,
    reset_usage_stats,
)

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts" / "prompt_research"

_PROMPT_TYPES = [
    "comparison",
    "recommendation",
    "problem_solving",
    "feature_specific",
    "buying_stage",
]
_PROMPTS_PER_TYPE = 4
_TOTAL_PROMPTS = 20

# Sonnet 4.5 pricing
_INPUT_COST_PER_TOK = 3.0 / 1_000_000
_OUTPUT_COST_PER_TOK = 15.0 / 1_000_000


# ── Public orchestrator ──────────────────────────────────────────────────────

def run_analysis(
    brand_name: str,
    industry: str,
    competitors: list[str],
    perspective: str = "B2C consumer",
) -> dict:
    """Run the full 3-step MVP-B analysis pipeline."""
    reset_usage_stats()

    tracked_brands = [brand_name] + list(competitors)

    print(f"[mvp_b] step 1: generating prompts for '{brand_name}'...", flush=True)
    gen = generate_prompts(brand_name, industry, perspective, competitors)
    if gen.get("error"):
        raise RuntimeError(f"generate_prompts failed: {gen['error']}")
    prompts = gen.get("prompts", [])
    ai_category = gen.get("ai_category", "")
    print(f"[mvp_b] generated {len(prompts)} prompts, ai_category='{ai_category}'", flush=True)

    print(f"[mvp_b] step 2: simulating answers across {len(tracked_brands)} brands...", flush=True)
    sim = simulate_answers(prompts, tracked_brands, industry)
    if sim.get("error"):
        raise RuntimeError(f"simulate_answers failed: {sim['error']}")
    simulation = sim.get("results", [])
    print(f"[mvp_b] simulation done, {len(simulation)} results", flush=True)

    # Enrich each result with type + text + canonical brand match
    type_by_id = {p["id"]: p["type"] for p in prompts}
    text_by_id = {p["id"]: p["text"] for p in prompts}
    for r in simulation:
        r["type"] = type_by_id.get(r.get("id"), "unknown")
        r["text"] = text_by_id.get(r.get("id"), "")
        for entry in r.get("top_3", []):
            entry["matched_brand"] = match_brand(entry.get("brand", ""), tracked_brands)

    user_sov = compute_sov(simulation, brand_name)
    awareness = classify_awareness(user_sov)
    print(f"[mvp_b] user SOV={user_sov}%, awareness={awareness}", flush=True)

    # Step 3: only run if there's at least one gap to find
    # TODO(V2): When user_sov >= 90%, instead of returning 0 opportunities,
    # return "defensive" insights — e.g., "You win 18/20 prompts. Here are
    # the 2 you don't, and how to defend your existing wins from competitors."
    # Current behavior shows 0 opportunities which feels bad to a paying user.
    if user_sov >= 100:
        opportunities: list[dict] = []
        print("[mvp_b] user SOV=100% — skipping opportunities step", flush=True)
    else:
        print("[mvp_b] step 3: finding top-5 opportunities...", flush=True)
        opportunities = find_opportunities(brand_name, industry, simulation)
        print(f"[mvp_b] found {len(opportunities)} opportunities", flush=True)

    # All-local metrics
    sov_table = {b: compute_sov(simulation, b) for b in tracked_brands}
    position_scores = {b: compute_position_score(simulation, b) for b in tracked_brands}
    category_strength = compute_category_strength(simulation, brand_name)
    direct_comparisons = find_direct_comparisons(simulation, brand_name)
    control_check = check_control_prompt(simulation, prompts, brand_name)

    usage = get_usage_stats()
    return {
        "brand_name": brand_name,
        "industry": industry,
        "competitors": list(competitors),
        "perspective": perspective,
        "ai_category": ai_category,
        "ai_brand_awareness": awareness,
        "control_prompt": control_check,
        "prompts": prompts,
        "simulation": simulation,
        "sov": sov_table,
        "position_scores": position_scores,
        "category_strength": category_strength,
        "direct_comparisons": direct_comparisons,
        "opportunities": opportunities,
        "usage": usage,
        "estimated_cost_usd": estimate_cost(usage),
    }


# ── LLM call wrappers ────────────────────────────────────────────────────────

def generate_prompts(
    brand_name: str,
    industry: str,
    perspective: str,
    competitors: list[str],
) -> dict:
    tmpl = (_PROMPT_DIR / "01_generate_prompts.txt").read_text(encoding="utf-8")
    prompt = (
        tmpl
        .replace("{brand_name}", brand_name)
        .replace("{industry}", industry)
        .replace("{perspective}", perspective)
        .replace("{competitors_list}", ", ".join(competitors) if competitors else "(none provided)")
    )
    data = ask_claude_json(prompt)
    if "prompts" in data and len(data["prompts"]) != _TOTAL_PROMPTS:
        logger.warning("Expected %d prompts, got %d", _TOTAL_PROMPTS, len(data["prompts"]))
    return data


def simulate_answers(
    prompts: list[dict],
    brands: list[str],
    industry: str,
) -> dict:
    tmpl = (_PROMPT_DIR / "02_simulate_answers.txt").read_text(encoding="utf-8")
    prompts_for_claude = [{"id": p["id"], "text": p["text"]} for p in prompts]
    prompt = (
        tmpl
        .replace("{industry}", industry)
        .replace("{brands_list}", ", ".join(brands))
        .replace("{prompts_json}", json.dumps(prompts_for_claude, ensure_ascii=False, indent=2))
    )
    return ask_claude_json(prompt, max_tokens=6000)


def find_opportunities(
    brand_name: str,
    industry: str,
    simulation: list[dict],
) -> list[dict]:
    tmpl = (_PROMPT_DIR / "03_find_opportunities.txt").read_text(encoding="utf-8")
    sim_for_claude = [
        {
            "id": r.get("id"),
            "type": r.get("type", "unknown"),
            "prompt": r.get("text", ""),
            "top_3": [
                {
                    "rank": e.get("rank"),
                    "brand": e.get("brand"),
                    "reason": e.get("reason", ""),
                }
                for e in r.get("top_3", [])
            ],
        }
        for r in simulation
    ]
    prompt = (
        tmpl
        .replace("{brand_name}", brand_name)
        .replace("{industry}", industry)
        .replace("{simulation_json}", json.dumps(sim_for_claude, ensure_ascii=False, indent=2))
    )
    data = ask_claude_json(prompt, max_tokens=4000)
    return data.get("opportunities", []) or []


# ── Brand matching ───────────────────────────────────────────────────────────

def match_brand(raw: str, known: list[str]) -> str | None:
    """Map Claude's free-form brand name to one of the tracked canonical names.

    Strategy (in order): exact case-insensitive → normalized (strip suffixes,
    punctuation, whitespace) → substring containment in either direction.
    Returns None if no candidate matches.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw_low = raw.lower().strip()
    raw_norm = _normalize(raw_low)
    if not raw_norm:
        return None
    for k in known:
        if raw_low == k.lower():
            return k
    for k in known:
        if raw_norm == _normalize(k.lower()):
            return k
    for k in known:
        k_norm = _normalize(k.lower())
        if not k_norm:
            continue
        if k_norm in raw_norm or raw_norm in k_norm:
            return k
    return None


def _normalize(s: str) -> str:
    s = s.lower().strip()
    for suffix in (" inc.", " inc", " llc", " corp.", " corp", " co.", " co", " ltd.", " ltd", " research"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return "".join(c for c in s if c.isalnum())


# ── Metrics ──────────────────────────────────────────────────────────────────

def compute_sov(simulation: list[dict], brand: str) -> int:
    """Share of voice: % of prompts where `brand` appears anywhere in top-3."""
    if not simulation:
        return 0
    hit = sum(1 for r in simulation if _brand_in_top3(r, brand))
    return round(hit / len(simulation) * 100)


def compute_position_score(simulation: list[dict], brand: str) -> int:
    """Position score: rank-1=3, rank-2=2, rank-3=1, absent=0. Max=60."""
    total = 0
    for r in simulation:
        for entry in r.get("top_3", []):
            if entry.get("matched_brand") == brand:
                rank = entry.get("rank")
                if rank == 1:
                    total += 3
                elif rank == 2:
                    total += 2
                elif rank == 3:
                    total += 1
                break  # count once per prompt
    return total


def compute_category_strength(simulation: list[dict], brand: str) -> dict[str, int]:
    """Per-prompt-type SOV for the user brand."""
    by_type: dict[str, list] = {t: [] for t in _PROMPT_TYPES}
    for r in simulation:
        t = r.get("type")
        if t in by_type:
            by_type[t].append(r)
    return {
        t: (round(sum(1 for r in lst if _brand_in_top3(r, brand)) / len(lst) * 100) if lst else 0)
        for t, lst in by_type.items()
    }


def find_direct_comparisons(simulation: list[dict], brand: str) -> list[dict]:
    """All 'comparison' type prompts with the user's rank (or None if absent)."""
    out = []
    for r in simulation:
        if r.get("type") != "comparison":
            continue
        user_rank = None
        for entry in r.get("top_3", []):
            if entry.get("matched_brand") == brand:
                user_rank = entry.get("rank")
                break
        out.append({
            "id": r.get("id"),
            "prompt": r.get("text", ""),
            "user_rank": user_rank,
            "top_3_brands": [e.get("brand") for e in r.get("top_3", [])],
        })
    return out


def classify_awareness(sov_percent: int) -> str:
    """User SOV → categorical brand awareness label."""
    if sov_percent >= 30:
        return "strong"
    if sov_percent >= 10:
        return "moderate"
    if sov_percent >= 1:
        return "weak"
    return "unknown"


def check_control_prompt(
    simulation: list[dict],
    prompts: list[dict],
    brand: str,
) -> dict:
    """Pick the most generic recommendation prompt, report whether user appears.

    Heuristic: shortest prompt of type 'recommendation' that contains "brand" or "best".
    Falls back to shortest recommendation overall.
    """
    rec = [p for p in prompts if p.get("type") == "recommendation"]
    if not rec:
        return {"present": False, "prompt_text": None, "reason": "no recommendation prompts"}
    branded = [p for p in rec if "brand" in p.get("text", "").lower()]
    control = (min(branded, key=lambda p: len(p["text"])) if branded
               else min(rec, key=lambda p: len(p["text"])))
    sim_match = next((r for r in simulation if r.get("id") == control["id"]), None)
    if not sim_match:
        return {
            "present": False,
            "prompt_id": control["id"],
            "prompt_text": control["text"],
            "reason": "no simulation entry",
        }
    return {
        "present": _brand_in_top3(sim_match, brand),
        "prompt_id": control["id"],
        "prompt_text": control["text"],
        "top_3": [e.get("brand") for e in sim_match.get("top_3", [])],
    }


def _brand_in_top3(result: dict, brand: str) -> bool:
    return any(e.get("matched_brand") == brand for e in result.get("top_3", []))


# ── Cost ─────────────────────────────────────────────────────────────────────

def estimate_cost(usage: dict | None = None) -> float:
    if usage is None:
        usage = get_usage_stats()
    return round(
        usage["input_tokens"] * _INPUT_COST_PER_TOK
        + usage["output_tokens"] * _OUTPUT_COST_PER_TOK,
        4,
    )
