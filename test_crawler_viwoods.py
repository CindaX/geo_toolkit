"""Temporary crawler diagnostic for viwoods.com."""

from pathlib import Path
from bs4 import BeautifulSoup
from shared.crawler import crawl_website

URL = "https://www.viwoods.com"

print(f"Crawling {URL}...")
result = crawl_website(URL, max_pages=5)

home_html = result["home_html"]
combined_text = result["combined_text"]

# Extract homepage-only text the same way logic.py does
def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())

home_text = html_to_text(home_html)

# Write debug files
Path(".crawl_debug_homepage.html").write_text(home_html, encoding="utf-8")
Path(".crawl_debug_text.txt").write_text(home_text, encoding="utf-8")

print(f"\n--- metadata ---")
for k, v in result["metadata"].items():
    print(f"  {k}: {v}")

print(f"\n--- sizes ---")
print(f"  home_html:      {len(home_html):,} chars")
print(f"  home_text:      {len(home_text):,} chars")
print(f"  combined_text:  {len(combined_text):,} chars")
print(f"  product_pages:  {len(result['product_pages'])}")
print(f"  about_html len: {len(result['about_html'])}")

print(f"\n--- home_text first 500 chars ---")
print(home_text[:500])
