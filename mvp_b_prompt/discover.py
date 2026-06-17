"""Wave-1 composite: prompt discovery + real competitor snapshot, in one call.

This is the orchestrator the Shopify app hits (via the /prompt-run endpoint). It
replaces the legacy run_analysis() — which used LLM-SIMULATED answers — with a
REAL Perplexity snapshot, and adds per-question content advice.

Pipeline:
  1. generate_prompts()  → 20 prompts (5 types x 4) + AI category        [1 LLM]
  2. run_snapshot()      → real top-3 brands for the 8 key prompts   [8 search+8 LLM]
  3. find_opportunities()→ gap/comeback strategy on those 8 (real data)   [1 LLM]
  4. content_advice()    → "how to answer this" for all 20 prompts        [1 LLM]

Free vs paid gating is NOT done here — the API always returns the full result and
the Shopify app slices it server-side per the merchant's entitlement. Cost is
bounded by the hard 8-question snapshot cap (see snapshot.py).
"""

from __future__ import annotations

import logging

from shared.claude_client import ask_claude_json, get_usage_stats, reset_usage_stats

from mvp_b_prompt.logic import (
    classify_awareness,
    content_advice,
    estimate_cost,
    find_opportunities,
    generate_prompts,
)
from mvp_b_prompt.snapshot import run_snapshot, _SNAPSHOT_LIMIT

logger = logging.getLogger(__name__)


def suggest_context(shop_name: str, product_types: list[str]) -> dict:
    """Infer an industry label + 2-3 likely competitor brands from shop signals.

    One cheap (Haiku) LLM call. Used to PRE-FILL the discovery form so a
    non-technical merchant isn't stuck on a blank "competitors" field — they
    confirm or edit the suggestions rather than inventing them from scratch.
    Returns ``{"industry": str, "suggested_competitors": list[str]}``.
    """
    types = ", ".join([t for t in product_types if t][:10]) or "(unknown)"
    prompt = (
        f'A Shopify store is named "{shop_name}". Its product types include: {types}.\n\n'
        "1. Give a short industry/category label (2-5 words) for what it sells.\n"
        "2. Name 2-3 well-known competitor BRANDS a shopper would compare it against in "
        "this category. Real brands only; if unsure, pick the category leaders.\n\n"
        'Return JSON: {"industry": "...", "suggested_competitors": ["...", "..."]}'
    )
    data = ask_claude_json(prompt, model="haiku", max_tokens=400)
    if not isinstance(data, dict):
        return {"industry": "", "suggested_competitors": []}
    competitors = [
        c.strip()
        for c in (data.get("suggested_competitors") or [])
        if isinstance(c, str) and c.strip()
    ]
    return {
        "industry": (data.get("industry") or "").strip(),
        "suggested_competitors": competitors[:3],
    }


def run_prompt_discovery(
    brand_name: str,
    industry: str,
    competitors: list[str],
    perspective: str = "B2C consumer",
    snapshot_limit: int = _SNAPSHOT_LIMIT,
) -> dict:
    """Run the wave-1 prompt-discovery + snapshot pipeline. Returns one dict that
    the Shopify app stores verbatim and then slices for free/paid display.

    ``snapshot_limit`` is exposed only so local verification can run a cheaper
    2-question snapshot; production always uses the default cap (8).
    """
    reset_usage_stats()

    # 1. Generate the 20 prompts.
    print(f"[discover] step 1: generating prompts for '{brand_name}'...", flush=True)
    gen = generate_prompts(brand_name, industry, perspective, competitors)
    if gen.get("error"):
        raise RuntimeError(f"generate_prompts failed: {gen['error']}")
    prompts = gen.get("prompts", [])
    ai_category = gen.get("ai_category", "")
    print(f"[discover] generated {len(prompts)} prompts, ai_category='{ai_category}'", flush=True)

    # 2. Real competitor snapshot on the key prompts (live Perplexity search).
    print(f"[discover] step 2: snapshotting up to {snapshot_limit} prompts via Sonar...", flush=True)
    snapshot = run_snapshot(prompts, brand_name, competitors, limit=snapshot_limit)
    snapshot_present = sum(1 for r in snapshot if r.get("brand_present"))
    snapshot_sov = round(snapshot_present / len(snapshot) * 100) if snapshot else 0
    print(f"[discover] snapshot done: {len(snapshot)} rows, brand present in {snapshot_present}", flush=True)

    # 3. Opportunities / comeback strategy — on the REAL snapshot data.
    #    Skip if the brand already wins every snapshotted prompt (no gap to find).
    if snapshot and snapshot_present < len(snapshot):
        print("[discover] step 3: finding opportunities from real snapshot...", flush=True)
        opportunities = find_opportunities(brand_name, industry, snapshot)
        print(f"[discover] found {len(opportunities)} opportunities", flush=True)
    else:
        opportunities = []
        print("[discover] step 3 skipped (no gap or empty snapshot)", flush=True)

    # 4. Per-question content advice for ALL 20 prompts.
    print("[discover] step 4: generating content advice for all prompts...", flush=True)
    advice = content_advice(brand_name, industry, prompts)
    print(f"[discover] generated advice for {len(advice)} prompts", flush=True)

    usage = get_usage_stats()
    return {
        "brand_name": brand_name,
        "industry": industry,
        "competitors": list(competitors),
        "perspective": perspective,
        "ai_category": ai_category,
        "snapshot_sov": snapshot_sov,            # % of snapshot prompts where brand appears
        "ai_brand_awareness": classify_awareness(snapshot_sov),
        "prompts": prompts,                      # all 20: {id, type, text}
        "snapshot": snapshot,                    # real top-3 per snapshotted prompt
        "opportunities": opportunities,          # gap/comeback on snapshot prompts
        "content_advice": advice,                # how-to-answer for all 20
        "usage": usage,
        "estimated_cost_usd": estimate_cost(usage),
    }
