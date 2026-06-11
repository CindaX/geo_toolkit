"""Phase-2 sync API: wrap run_audit() behind an API-key-gated HTTP endpoint,
with in-process protections (cost breaker + rate limit + result cache).

Run locally from the project root:
    uvicorn api.main:app --reload --port 8000

Auth: every request must send header  X-API-Key: <value of env GEO_API_KEY>.
A missing/wrong key returns 401.

Secrets / config:
    GEO_API_KEY          — this API's own access key (we define it).
    OPENROUTER_API_KEY   — read by run_audit's LLM client via shared.secrets
                           (.env / os.environ fallback; no Streamlit needed).
    DAILY_COST_LIMIT_USD — daily LLM-spend cap (default 20.0).
    RATE_LIMIT_PER_MINUTE / RATE_LIMIT_PER_DAY — per-key request caps (5 / 50).
    CACHE_TTL_HOURS      — result-cache lifetime (default 24).

This file is additive only — it imports and reuses run_audit() and never
modifies the existing audit logic.

NOTE: run_audit is synchronous and takes ~60-100s. This phase intentionally
blocks for the whole audit; async/job-queue is a later phase.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from mvp_a_audit.logic import run_audit

from api import protections

app = FastAPI(title="GEO Toolkit Audit API", version="0.2.0")


class AuditRequest(BaseModel):
    url: str
    brand_name: str = "Brand"
    industry: str = "General"


def _require_api_key(x_api_key: str | None) -> str:
    """Reject unless X-API-Key matches GEO_API_KEY. Returns the key on success."""
    expected = (os.environ.get("GEO_API_KEY") or "").strip()
    if not expected:
        # Server misconfigured — fail closed rather than allow open access.
        raise HTTPException(status_code=500, detail="GEO_API_KEY not configured on server")
    if not x_api_key or x_api_key.strip() != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")
    return x_api_key.strip()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "cost_spent_today_usd": protections.cost_spent_today()}


@app.post("/audit")
def audit(req: AuditRequest, x_api_key: str | None = Header(default=None)) -> dict:
    """Run a full 8-dimension GEO audit. Blocks ~60-100s (sync, by design).

    Protection order: auth → rate limit → cache → cost breaker → audit.
    Cache hits are served before the cost breaker (they cost nothing).
    """
    api_key = _require_api_key(x_api_key)

    # 1. Rate limit — reject spam early, before any work.
    allowed, reason = protections.rate_limit_check(api_key)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded: {reason}")

    # 2. Cache — same URL within CACHE_TTL_HOURS returns instantly, no LLM cost.
    cache_key = req.url.strip()
    cached = protections.cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    # 3. Cost breaker — only gate the expensive (cache-miss) path.
    if protections.cost_budget_exceeded():
        raise HTTPException(status_code=429, detail="Daily audit limit reached")

    # 4. Run the audit, then record cost + cache the result.
    result = run_audit(req.url, brand_name=req.brand_name, industry=req.industry)

    # A failed audit (e.g. homepage unreachable / blocked) ran no LLM call and
    # cost nothing — do NOT record cost and do NOT cache it (caching would pin
    # the failure for the TTL and block a later successful retry).
    if result.get("status") == "failed":
        return {**result, "cached": False}

    protections.record_cost(result.get("estimated_cost_usd", 0.0))
    protections.cache_set(cache_key, result)
    return {**result, "cached": False}
