"""Real competitor snapshot via Perplexity Sonar (one-time, NOT monitoring).

For the most decision-relevant prompts (recommendation + comparison), ask
Perplexity Sonar — which does live web search — "who would you recommend?",
then extract the brands it cites with a cheap Claude (Haiku) call. The output
shape matches the ``simulation`` that :func:`mvp_b_prompt.logic.find_opportunities`
consumes, so the existing opportunity logic runs on REAL data instead of the
LLM-simulated answers the legacy pipeline used.

Cost discipline: Sonar's per-request web-search fee is not captured in token
usage, so the number of questions is HARD-CAPPED here (``_SNAPSHOT_LIMIT = 8``),
never driven by a config knob. This is a one-shot snapshot, never a recurring
monitor — that distinction is a project-constitution rule (§3.5).
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from shared.claude_client import ask_claude_json, ask_perplexity

from mvp_b_prompt.logic import match_brand

logger = logging.getLogger(__name__)

# Only these prompt types are worth a (paid) live search: "who do you recommend"
# (recommendation) and head-to-head (comparison). Problem/feature/buying-stage
# prompts rarely surface a clean brand ranking, so we don't spend search calls.
_SNAPSHOT_TYPES = {"recommendation", "comparison"}
_SNAPSHOT_LIMIT = 8

_EXTRACT_TMPL = """You are analyzing an AI assistant's answer to a shopping question, to see which BRANDS it recommends.

Question asked: {question}

The assistant answered:
\"\"\"
{answer}
\"\"\"

List the specific product or company BRANDS the answer recommends or names, ordered by prominence (most strongly recommended first). For each brand give a one-line reason the answer gave for it (or "" if none). Ignore generic words, categories, and features that are not brand names.

Return JSON of this exact shape (at most 6 brands):
{"brands": [{"brand": "<name>", "reason": "<one line>"}]}"""


def select_snapshot_prompts(prompts: list[dict], limit: int = _SNAPSHOT_LIMIT) -> list[dict]:
    """Pick the up-to-``limit`` prompts worth a live search (recommendation/comparison)."""
    picked = [p for p in prompts if p.get("type") in _SNAPSHOT_TYPES]
    return picked[:limit]


def _extract_brands(
    question: str,
    answer: str,
    brand_name: str,
    competitors: list[str],
) -> tuple[list[dict], bool]:
    """Pull a ranked top-3 brand list out of a Sonar answer with a cheap Claude call.

    Returns ``(top_3, brand_present)`` where each top_3 entry is
    ``{rank, brand, reason, matched_brand}`` and ``matched_brand`` is the
    canonical tracked name (or ``None``). ``brand_present`` is True iff the
    merchant's own brand appears anywhere in the top 3.
    """
    known = [brand_name] + list(competitors)
    prompt = _EXTRACT_TMPL.replace("{question}", question or "").replace(
        "{answer}", answer or ""
    )
    data = ask_claude_json(prompt, model="haiku", max_tokens=800)
    raw = data.get("brands", []) if isinstance(data, dict) else []

    top_3: list[dict] = []
    for i, entry in enumerate(raw[:3], start=1):
        if not isinstance(entry, dict):
            continue
        name = (entry.get("brand") or "").strip()
        if not name:
            continue
        top_3.append(
            {
                "rank": i,
                "brand": name,
                "reason": (entry.get("reason") or "").strip(),
                "matched_brand": match_brand(name, known),
            }
        )

    brand_present = any(t["matched_brand"] == brand_name for t in top_3)
    return top_3, brand_present


def snapshot_one(
    prompt_obj: dict,
    brand_name: str,
    competitors: list[str],
) -> dict:
    """Run one prompt through Sonar + extraction. Shape matches the simulation rows."""
    question = prompt_obj.get("text", "")
    answer = ask_perplexity(question)
    top_3, brand_present = _extract_brands(question, answer, brand_name, competitors)
    return {
        "id": prompt_obj.get("id"),
        "type": prompt_obj.get("type", "unknown"),
        "text": question,
        "top_3": top_3,
        "brand_present": brand_present,
    }


def run_snapshot(
    prompts: list[dict],
    brand_name: str,
    competitors: list[str],
    limit: int = _SNAPSHOT_LIMIT,
) -> list[dict]:
    """Snapshot the top-``limit`` recommendation/comparison prompts via live search.

    Each call is one Sonar search + one Haiku extraction. The chains run
    CONCURRENTLY in a threadpool — serial execution of ~8 live searches could
    exceed the caller's request timeout on a cold start. Returns a list whose
    rows are directly consumable by find_opportunities() (re-sorted to the input
    order). A single prompt that errors is skipped (logged) rather than failing
    the whole snapshot.
    """
    picked = select_snapshot_prompts(prompts, limit)
    if not picked:
        return []

    results: list[dict] = []
    # I/O-bound (network) work → threads give real concurrency despite the GIL.
    with ThreadPoolExecutor(max_workers=min(8, len(picked))) as pool:
        futures = {
            pool.submit(snapshot_one, p, brand_name, competitors): p for p in picked
        }
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001 — one bad prompt shouldn't kill the run
                logger.warning("snapshot failed for prompt id=%s: %s", p.get("id"), exc)

    # as_completed yields out of order; restore the original prompt order.
    order = {p.get("id"): i for i, p in enumerate(picked)}
    results.sort(key=lambda r: order.get(r.get("id"), len(picked)))
    return results
