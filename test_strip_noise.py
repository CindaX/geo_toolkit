"""Validate _strip_shopify_noise against the cached viwoods.com crawl."""

import json
from pathlib import Path
from shared.crawler import _strip_shopify_noise

cache_dir = Path("data/cache")
cache_files = sorted(cache_dir.glob("crawl_*.json"))

if not cache_files:
    print("No cache files found. Run test_jina_viwoods.py first.")
    raise SystemExit(1)

# Use the most recent cache file
cache_file = max(cache_files, key=lambda p: p.stat().st_mtime)
print(f"Reading cache: {cache_file.name}\n")

raw = json.loads(cache_file.read_text(encoding="utf-8"))
home_text = raw["payload"]["home_text"]

print(f"--- BEFORE strip ---")
print(f"Length: {len(home_text):,} chars")
print(f"First 300 chars:\n{home_text[:300]}\n")

cleaned = _strip_shopify_noise(home_text)

print(f"--- AFTER strip ---")
print(f"Length: {len(cleaned):,} chars  (removed {len(home_text)-len(cleaned):,} chars of noise)")
print(f"\nFirst 800 chars:\n{cleaned[:800]}")
