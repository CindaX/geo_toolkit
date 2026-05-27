"""Stripe Payment Link URLs for the GEO Toolkit.

These are Stripe-hosted checkout pages — clicking them opens Stripe's secure
payment form (with a back link to our app). No code needed on our side
beyond a link button.

Currently in TEST mode (use card 4242 4242 4242 4242 to test).
Switch to live URLs when ready to accept real payments.
"""

from __future__ import annotations

# One-time purchase: $9.90 for detailed PDF report + benchmark
PAYMENT_LINK_AUDIT_FULL = "https://buy.stripe.com/test_5kQ3cugXM19obbN9M448000"

# Subscription: $29.90/month for continuous monitoring
PAYMENT_LINK_PRO_MONTHLY = "https://buy.stripe.com/test_9B6aEWfTIaJYdjV9M448001"

# Pricing display strings (for UI consistency)
PRICE_AUDIT_FULL_DISPLAY = "$9.90"
PRICE_PRO_MONTHLY_DISPLAY = "$29.90/month"
