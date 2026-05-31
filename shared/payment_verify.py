"""Stripe Checkout Session verification — the gate between URL params and unlock.

Why this module exists
----------------------
Naked ``?paid=true`` redirect-detection is a critical security hole: anyone
who knows the URL pattern can grant themselves paid features by typing
``?paid=true``. Replace with: read the ``session_id`` Stripe attaches to its
redirect → call ``stripe.checkout.sessions.retrieve(session_id)`` with our
secret key → only unlock if Stripe confirms ``payment_status == "paid"``.

Trust model
-----------
- ``session_id`` in the URL is **not** trusted (any random string could be
  pasted). Stripe's API is the source of truth — it returns the canonical
  ``payment_status`` and ``amount_total`` for that session.
- ``tier`` in the URL is **not** trusted either (user could edit the URL
  to ``tier=full`` while only paying $9.90). The tier is derived from
  ``amount_total`` on Stripe's side instead:
    990  → "single"  ($9.90)
    2990 → "full"    ($29.90/month)
  Any other amount returns None (no unlock).

Caching
-------
A verified session_id is cached in ``st.session_state["verified_sessions"]``
so we don't re-call Stripe on every Streamlit rerun. The cache lives only
for the current Streamlit session — fresh on restart, which is fine because
Stripe re-verification is cheap (~100ms) and safe to repeat.

TODO (future): subscribe to the ``checkout.session.completed`` webhook +
write paid sessions to Supabase so refunds / chargebacks / missed redirects
are caught. Today's verify-on-redirect flow misses those edge cases.
"""

from __future__ import annotations

from typing import Optional

from shared.secrets import get_secret

# Amount (in cents) → tier label. Anything else = unknown tier → no unlock.
_TIER_BY_AMOUNT: dict[int, str] = {
    990:  "single",   # $9.90 one-time
    2990: "full",     # $29.90/month
}


def _get_stripe():
    """Lazy-init the Stripe SDK with the secret key. Returns ``None`` if missing."""
    key = get_secret("STRIPE_SECRET_KEY")
    if not key:
        return None
    try:
        import stripe
        stripe.api_key = key
        return stripe
    except Exception as exc:
        print(f"[payment_verify] stripe import failed: {exc}", flush=True)
        return None


def verify_unlock(session_id: str) -> Optional[dict]:
    """Verify a Stripe Checkout Session ID and return the unlock payload.

    Returns:
        ``{"paid": True, "tier": "single"|"full", "email": str|None,
           "session_id": str, "amount_total": int}``
        on successful paid verification.

        ``None`` if any of:
          - session_id is empty / not a string
          - STRIPE_SECRET_KEY is not configured
          - the session can't be retrieved from Stripe
          - the session's payment_status is not "paid"
          - the amount doesn't match a known tier (defense against new SKUs
            that haven't been wired up to the UI yet)

    Caches verified results in ``st.session_state["verified_sessions"]`` so
    a Streamlit rerun (e.g. on tab focus) doesn't burn extra API calls.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    session_id = session_id.strip()

    # 1. Cache check (only inside a real Streamlit context — outside one this
    #    silently misses, which is fine; verify_unlock is meant to be called
    #    from UI code anyway).
    try:
        import streamlit as st
        cached = st.session_state.get("verified_sessions", {}).get(session_id)
        if cached is not None:
            return cached
    except Exception:
        st = None  # type: ignore[assignment]

    # 2. Call Stripe.
    stripe = _get_stripe()
    if stripe is None:
        return None

    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["line_items"])
    except Exception as exc:
        print(f"[payment_verify] retrieve({session_id[:12]}…) failed: {exc}", flush=True)
        return None

    payment_status = getattr(session, "payment_status", None)
    if payment_status != "paid":
        print(
            f"[payment_verify] session {session_id[:12]}… not paid "
            f"(status={payment_status!r}); skipping unlock",
            flush=True,
        )
        return None

    amount = getattr(session, "amount_total", None)
    tier = _TIER_BY_AMOUNT.get(amount)
    if tier is None:
        print(
            f"[payment_verify] session {session_id[:12]}… amount={amount} "
            f"does not match a known tier — refusing to unlock",
            flush=True,
        )
        return None

    customer_details = getattr(session, "customer_details", None)
    email = getattr(customer_details, "email", None) if customer_details else None

    result = {
        "paid":         True,
        "tier":         tier,
        "email":        email,
        "session_id":   session_id,
        "amount_total": amount,
    }

    # 3. Cache for the rest of this Streamlit session.
    try:
        if st is not None:
            cache = st.session_state.setdefault("verified_sessions", {})
            cache[session_id] = result
    except Exception:
        pass

    return result
