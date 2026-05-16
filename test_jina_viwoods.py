"""Diagnostic: compare Jina vs httpx text extraction for viwoods.com."""

from pathlib import Path
from shared.crawler import crawl_website

URL = "https://www.viwoods.com"

print(f"Crawling {URL} (Jina primary, httpx fallback)...")
print("Note: Jina homepage fetch may take up to 20 seconds.\n")

result = crawl_website(URL, max_pages=5)

home_text = result["home_text"]
combined_text = result["combined_text"]
fetch_method = result.get("fetch_method", {})

Path(".crawl_jina_text.txt").write_text(home_text, encoding="utf-8")

print("--- fetch_method per page ---")
for page_url, method in fetch_method.items():
    print(f"  [{method:4}]  {page_url}")

print(f"\n--- sizes ---")
print(f"  home_text:     {len(home_text):,} chars")
print(f"  combined_text: {len(combined_text):,} chars")
print(f"  product_pages: {len(result['product_pages'])}")

print(f"\n--- home_text first 800 chars ---")
print(home_text[:800])
