"""Phase-1 reuse check: prove run_audit() works in a plain Python process.

Run from the project root:  python3 api/verify_reuse.py

This imports run_audit() with NO Streamlit runtime and runs a real audit,
printing the key result fields. Reads OPENROUTER_API_KEY from .env / os.environ
via shared.secrets (no Streamlit needed).
"""

from __future__ import annotations

from mvp_a_audit.logic import run_audit

URL = "https://example.com"

result = run_audit(URL, brand_name="Example", industry="General")

print("geo_score :", result["geo_score"])
print("geo_grade :", result["geo_grade"])
print("dimensions:", list(result["results"].keys()))
print("est_cost  : $", result["estimated_cost_usd"], sep="")
