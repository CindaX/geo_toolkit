"""MVP-C: GEO Asset Generator — Streamlit app."""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import streamlit as st

from mvp_c_assets.logic import (
    build_zip,
    generate_brand_facts,
    generate_comparison,
    generate_faq,
    generate_llms_txt,
    generate_product_facts,
    generate_schema_json,
)
from shared._openrouter import get_openrouter_client  # pre-warm singleton

# ── Asset task registry ────────────────────────────────────────────────────────

ASSET_TASKS: list[tuple[str, Callable]] = [
    ("llms.txt", generate_llms_txt),
    ("brand-facts.md", generate_brand_facts),
    ("product-facts.json", generate_product_facts),
    ("faq.md", generate_faq),
    ("comparison.md", generate_comparison),
    ("schema.json", generate_schema_json),
]

# ── Question groups ────────────────────────────────────────────────────────────

QUESTION_GROUPS: list[dict] = [
    {
        "title": "Brand Identity",
        "questions": [
            {"key": "q1", "label": "Brand Name(s)", "help": "All names / aliases this brand uses, comma-separated.", "required": True},
            {"key": "q2", "label": "Tagline", "help": "Short slogan or value proposition (optional)."},
            {"key": "q3", "label": "Official Website URL", "help": "e.g. https://example.com", "required": True},
        ],
    },
    {
        "title": "Company Background",
        "questions": [
            {"key": "q4", "label": "Founding Year", "help": "Year the company was founded (optional)."},
            {"key": "q5", "label": "Location", "help": "City, Country (optional)."},
            {"key": "q6", "label": "Company Description", "help": "2–4 sentences describing what the company does.", "required": True, "area": True},
        ],
    },
    {
        "title": "Products & Pricing",
        "questions": [
            {"key": "q7", "label": "Products / Services", "help": "List each product/service with a brief description.", "required": True, "area": True},
            {"key": "q12", "label": "Pricing", "help": "Exact prices, tiers, or pricing model for each product.", "required": True, "area": True},
        ],
    },
    {
        "title": "Target Audience",
        "questions": [
            {"key": "q8", "label": "Ideal Customer", "help": "Who is this product perfect for? Job titles, industries, use cases.", "required": True, "area": True},
            {"key": "q9", "label": "NOT a Good Fit For", "help": "Who should NOT buy this product and why.", "area": True},
        ],
    },
    {
        "title": "Competitive Landscape",
        "questions": [
            {"key": "q10", "label": "Main Competitors", "help": "Named competitors, comma-separated."},
            {"key": "q11", "label": "Key Differentiators", "help": "What makes this brand meaningfully different from competitors?", "area": True},
        ],
    },
    {
        "title": "Proof & Clarity",
        "questions": [
            {"key": "q13", "label": "Common Customer Questions", "help": "Questions your support/sales team hears most often.", "area": True},
            {"key": "q14", "label": "Common Misconceptions", "help": "Wrong beliefs people have about your brand or product (optional).", "area": True},
            {"key": "q15", "label": "Proof Points / Credentials", "help": "Press coverage, certifications, user counts, case studies — include source URLs.", "area": True},
        ],
    },
]

_ALL_REQUIRED = {q["key"] for g in QUESTION_GROUPS for q in g["questions"] if q.get("required")}


# ── Session state init ─────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "step": "welcome",          # welcome | questionnaire | preview | generating | done
        "group_idx": 0,
        "answers": {f"q{i}": "" for i in range(1, 16)},
        "generated": {},            # filename -> content
        "errors": {},               # filename -> error message
        "zip_bytes": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── Step renderers ─────────────────────────────────────────────────────────────

def render_welcome() -> None:
    st.title("GEO Asset Generator")
    st.markdown(
        "Answer 15 questions about your brand and receive **6 AI-generated files** "
        "that help AI search engines (ChatGPT, Perplexity, Gemini) understand and "
        "recommend your brand accurately."
    )
    st.markdown("**Files you'll receive:**")
    for filename, _ in ASSET_TASKS:
        st.markdown(f"- `{filename}`")
    st.markdown("---")
    if st.button("Get Started →", type="primary"):
        st.session_state.step = "questionnaire"
        st.rerun()


def render_questionnaire() -> None:
    group_idx: int = st.session_state.group_idx
    group = QUESTION_GROUPS[group_idx]
    total = len(QUESTION_GROUPS)

    st.progress((group_idx) / total, text=f"Step {group_idx + 1} of {total}")
    st.subheader(group["title"])

    answers: dict[str, str] = st.session_state.answers

    for q in group["questions"]:
        key = q["key"]
        label = q["label"] + (" *" if q.get("required") else "")
        if q.get("area"):
            answers[key] = st.text_area(label, value=answers[key], help=q.get("help", ""), key=f"input_{key}")
        else:
            answers[key] = st.text_input(label, value=answers[key], help=q.get("help", ""), key=f"input_{key}")

    st.session_state.answers = answers

    col_back, col_next = st.columns([1, 4])
    with col_back:
        if group_idx > 0 and st.button("← Back"):
            st.session_state.group_idx -= 1
            st.rerun()
    with col_next:
        label_next = "Review →" if group_idx == total - 1 else "Next →"
        if st.button(label_next, type="primary"):
            missing = [
                q["label"]
                for q in group["questions"]
                if q.get("required") and not answers.get(q["key"], "").strip()
            ]
            if missing:
                st.error(f"Please fill in: {', '.join(missing)}")
            elif group_idx == total - 1:
                st.session_state.step = "preview"
                st.rerun()
            else:
                st.session_state.group_idx += 1
                st.rerun()


def render_preview() -> None:
    st.title("Review Your Answers")
    st.markdown("Check everything looks right before generating.")

    answers: dict[str, str] = st.session_state.answers
    for g in QUESTION_GROUPS:
        with st.expander(g["title"], expanded=False):
            for q in g["questions"]:
                val = answers.get(q["key"], "").strip() or "_Not provided_"
                st.markdown(f"**{q['label']}:** {val}")

    st.markdown("---")
    col_edit, col_go = st.columns([1, 3])
    with col_edit:
        if st.button("← Edit"):
            st.session_state.step = "questionnaire"
            st.session_state.group_idx = 0
            st.rerun()
    with col_go:
        if st.button("Generate Assets →", type="primary"):
            st.session_state.step = "generating"
            st.rerun()


def render_generating() -> None:
    st.title("Generating Your Assets…")
    st.markdown("This usually takes 30–90 seconds. Do not close this tab.")

    # Pre-warm the OpenRouter client on the main thread before spawning threads.
    get_openrouter_client()

    answers = st.session_state.answers
    generated: dict[str, str] = {}
    errors: dict[str, str] = {}

    progress_bar = st.progress(0.0)
    status_text = st.empty()
    total = len(ASSET_TASKS)

    futures_map: dict = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for filename, fn in ASSET_TASKS:
            futures_map[executor.submit(fn, answers)] = filename

        completed = 0
        for future in as_completed(futures_map):
            filename = futures_map[future]
            completed += 1
            progress_bar.progress(completed / total)
            status_text.markdown(f"Finished **{filename}** ({completed}/{total})")
            try:
                generated[filename] = future.result()
            except Exception:
                errors[filename] = traceback.format_exc()

    st.session_state.generated = generated
    st.session_state.errors = errors

    if generated:
        st.session_state.zip_bytes = build_zip(generated)

    st.session_state.step = "done"
    st.rerun()


def render_done() -> None:
    generated: dict[str, str] = st.session_state.generated
    errors: dict[str, str] = st.session_state.errors
    zip_bytes: bytes | None = st.session_state.zip_bytes

    st.title("Your GEO Assets Are Ready")

    if errors:
        st.warning(f"{len(errors)} file(s) failed to generate and are excluded from the download.")
        with st.expander("Show errors"):
            for fname, tb in errors.items():
                st.markdown(f"**{fname}**")
                st.code(tb, language="text")

    if zip_bytes:
        brand_name = st.session_state.answers.get("q1", "brand").split(",")[0].strip().lower().replace(" ", "_")
        st.download_button(
            label=f"Download {len(generated)} files as ZIP",
            data=zip_bytes,
            file_name=f"{brand_name}_geo_assets.zip",
            mime="application/zip",
            type="primary",
        )

    st.markdown("---")
    st.markdown("**Preview generated files:**")
    for filename, content in generated.items():
        with st.expander(filename):
            lang = "json" if filename.endswith(".json") else "markdown" if filename.endswith(".md") else "text"
            st.code(content, language=lang)

    st.markdown("---")
    if st.button("Start Over"):
        for key in ["step", "group_idx", "answers", "generated", "errors", "zip_bytes"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="GEO Asset Generator",
        page_icon="🌐",
        layout="centered",
    )
    _init_state()

    step = st.session_state.step
    if step == "welcome":
        render_welcome()
    elif step == "questionnaire":
        render_questionnaire()
    elif step == "preview":
        render_preview()
    elif step == "generating":
        render_generating()
    elif step == "done":
        render_done()


if __name__ == "__main__":
    main()
