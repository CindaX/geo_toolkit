"""Supabase persistence layer for emails + audit results.

Replaces the ephemeral /tmp CSV storage with a real Postgres database so
data survives:
  - Streamlit Cloud container restarts (deploys, idle eviction)
  - The session-state loss that happens when users return from Stripe
    checkout (cross-domain redirect drops the Streamlit session cookie
    in some browsers)

All functions are graceful: if SUPABASE_URL / SUPABASE_KEY are not set,
or the network is unreachable, they return ``False`` / ``None`` rather
than raising — callers can fall back to the CSV path or show a friendly
message.

Tables (already provisioned in Supabase dashboard):
  emails:
    id, email, source, created_at
  audit_results:
    id, audit_id (unique), email, url, brand_name, industry,
    overall_score, ai_understanding, dimensions (jsonb), created_at

RLS allows anon insert on both tables + anon select on audit_results
(needed by the post-payment email-recovery flow).
"""

from __future__ import annotations

from typing import Optional

from shared.secrets import get_secret

_client = None


def _get_client():
    """Lazy-init Supabase client. Returns ``None`` if credentials missing."""
    global _client
    if _client is not None:
        return _client

    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_KEY")
    if not url or not key:
        return None

    try:
        from supabase import create_client
        _client = create_client(url, key)
        return _client
    except Exception as exc:
        import traceback
        print(f"[supabase] init failed: {exc}\n{traceback.format_exc()}", flush=True)
        return None


def save_email(email: str, source: str = "unknown") -> bool:
    """Persist an email. Returns True on success, False otherwise. Never raises."""
    client = _get_client()
    if client is None:
        return False
    try:
        client.table("emails").insert({"email": email, "source": source}).execute()
        return True
    except Exception as exc:
        print(f"[supabase] save_email failed: {exc}", flush=True)
        return False


def save_audit_result(
    audit_id: str,
    email: str,
    url: str,
    brand_name: str,
    industry: str,
    overall_score: int,
    ai_understanding: str,
    dimensions: list,
) -> bool:
    """Persist an audit result keyed by audit_id. Returns True on success.

    ``dimensions`` is a list of dicts (the PDF-friendly shape produced by
    ``audit_result_to_pdf_dict()['dimensions']``) — supabase-py serializes
    it as jsonb automatically.
    """
    client = _get_client()
    if client is None:
        return False
    try:
        client.table("audit_results").insert({
            "audit_id":         audit_id,
            "email":            email,
            "url":              url,
            "brand_name":       brand_name,
            "industry":         industry,
            "overall_score":    overall_score,
            "ai_understanding": ai_understanding,
            "dimensions":       dimensions,
        }).execute()
        return True
    except Exception as exc:
        print(f"[supabase] save_audit_result failed: {exc}", flush=True)
        return False


def get_latest_audit_by_email(email: str) -> Optional[dict]:
    """Fetch the most recent audit row for ``email``. ``None`` if not found."""
    client = _get_client()
    if client is None or not email:
        return None
    try:
        resp = (
            client.table("audit_results")
            .select("*")
            .eq("email", email)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]
        return None
    except Exception as exc:
        print(f"[supabase] get_latest_audit_by_email failed: {exc}", flush=True)
        return None


def get_audit_by_id(audit_id: str) -> Optional[dict]:
    """Fetch an audit row by audit_id. ``None`` if not found."""
    client = _get_client()
    if client is None or not audit_id:
        return None
    try:
        resp = (
            client.table("audit_results")
            .select("*")
            .eq("audit_id", audit_id)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]
        return None
    except Exception as exc:
        print(f"[supabase] get_audit_by_id failed: {exc}", flush=True)
        return None
