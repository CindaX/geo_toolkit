"""Stripe Payment Link URLs for the GEO Toolkit.

These are Stripe-hosted checkout pages — clicking them opens Stripe's secure
payment form (with a back link to our app). No code needed on our side
beyond a link button.

Status:
  - AUDIT_FULL ($9.90 one-time): LIVE — accepts real payments.
  - PRO_MONTHLY ($29.90/month):  still on a TEST link, AND its CTA is
    hidden across the UI via ``PRO_MONTHLY_ENABLED = False`` until the
    subscription handling (renewals, cancellations, tier-gated content) is
    properly wired up.
"""

from __future__ import annotations

# One-time purchase: $9.90 for detailed PDF report + benchmark — LIVE.
PAYMENT_LINK_AUDIT_FULL = "https://buy.stripe.com/dRm3cu8rg8BQa7J5vO48002"

# Subscription: $29.90/month for continuous monitoring — TEST link, gated off.
PAYMENT_LINK_PRO_MONTHLY = "https://buy.stripe.com/test_9B6aEWfTIaJYdjV9M448001"

# Master switch for the monthly subscription CTA. While False, every Pro
# subscription button across the 3 tools is hidden. Flip to True once
# subscription-related plumbing (verify_unlock tier="full" content gating,
# cancellation handling, dunning) is ready.
PRO_MONTHLY_ENABLED = False

# Pricing display strings (for UI consistency)
PRICE_AUDIT_FULL_DISPLAY = "$9.90"
PRICE_PRO_MONTHLY_DISPLAY = "$29.90/month"
