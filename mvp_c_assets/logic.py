"""Asset generation logic for MVP-C."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from shared.claude_client import ask_claude, ask_claude_json  # noqa: F401

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "asset_generation"

_Q_LABELS: dict[str, str] = {
    "q1": "Brand Name(s)",
    "q2": "Tagline",
    "q3": "Official Website URL",
    "q4": "Founding Year",
    "q5": "Location",
    "q6": "Company Description",
    "q7": "Products / Services",
    "q8": "Ideal Customer",
    "q9": "NOT a Good Fit For",
    "q10": "Main Competitors",
    "q11": "Key Differentiators",
    "q12": "Pricing",
    "q13": "Common Customer Questions",
    "q14": "Common Misconceptions",
    "q15": "Proof Points / Credentials",
}


def _load_prompt(template_name: str, brand_name: str, brand_context: str) -> str:
    path = _PROMPTS_DIR / template_name
    text = path.read_text(encoding="utf-8")
    text = text.replace("{brand_name}", brand_name)
    text = text.replace("{brand_context}", brand_context)
    return text


def _primary_name(q1: str) -> str:
    """Return the first brand name from a comma-separated list."""
    return q1.split(",")[0].strip()


def format_context(answers: dict[str, str]) -> str:
    """Convert a q1–q15 answers dict into a labeled Markdown block."""
    lines: list[str] = []
    for key, label in _Q_LABELS.items():
        value = answers.get(key, "").strip()
        lines.append(f"**{label}:** {value if value else 'Not provided'}")
    return "\n".join(lines)


# ── Individual generators ──────────────────────────────────────────────────────

def generate_llms_txt(answers: dict[str, str]) -> str:
    brand_name = _primary_name(answers.get("q1", "Brand"))
    context = format_context(answers)
    prompt = _load_prompt("01_llms_txt.txt", brand_name, context)
    return ask_claude(prompt)


def generate_brand_facts(answers: dict[str, str]) -> str:
    brand_name = _primary_name(answers.get("q1", "Brand"))
    context = format_context(answers)
    prompt = _load_prompt("02_brand_facts.txt", brand_name, context)
    return ask_claude(prompt)


def generate_product_facts(answers: dict[str, str]) -> str:
    brand_name = _primary_name(answers.get("q1", "Brand"))
    context = format_context(answers)
    prompt = _load_prompt("03_product_facts.txt", brand_name, context)
    return ask_claude(prompt)


def generate_faq(answers: dict[str, str]) -> str:
    brand_name = _primary_name(answers.get("q1", "Brand"))
    context = format_context(answers)
    prompt = _load_prompt("04_faq.txt", brand_name, context)
    return ask_claude(prompt)


def generate_comparison(answers: dict[str, str]) -> str:
    brand_name = _primary_name(answers.get("q1", "Brand"))
    context = format_context(answers)
    prompt = _load_prompt("05_comparison.txt", brand_name, context)
    return ask_claude(prompt)


def generate_schema_json(answers: dict[str, str]) -> str:
    brand_name = _primary_name(answers.get("q1", "Brand"))
    context = format_context(answers)
    prompt = _load_prompt("06_schema_json.txt", brand_name, context)
    return ask_claude(prompt)


# ── Zip builder ───────────────────────────────────────────────────────────────

def build_zip(files: dict[str, str]) -> bytes:
    """Pack a {filename: content} dict into an in-memory zip and return bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files.items():
            zf.writestr(filename, content)
    return buf.getvalue()
