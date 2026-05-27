"""MVP-C GEO Asset Generator UI — extracted for reuse.

All session_state keys are prefixed with `assets_` so they don't collide
with MVP-A (`audit_*`) or MVP-B (`prompts_*`).
"""

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
from shared._openrouter import get_openrouter_client
from shared.stripe_links import PAYMENT_LINK_PRO_MONTHLY, PRICE_PRO_MONTHLY_DISPLAY
from shared.ui_components import render_footer

# ── Asset task registry ───────────────────────────────────────────────────────

ASSET_TASKS: list[tuple[str, Callable]] = [
    ("llms.txt", generate_llms_txt),
    ("brand-facts.md", generate_brand_facts),
    ("product-facts.json", generate_product_facts),
    ("faq.md", generate_faq),
    ("comparison.md", generate_comparison),
    ("schema.json", generate_schema_json),
]

# ── Question groups ───────────────────────────────────────────────────────────

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


# ── Session state ────────────────────────────────────────────────────────────

def _init_assets_state() -> None:
    defaults = {
        "assets_step":      "welcome",
        "assets_group_idx": 0,
        "assets_answers":   {f"q{i}": "" for i in range(1, 16)},
        "assets_generated": {},
        "assets_errors":    {},
        "assets_zip_bytes": None,
        "assets_prefill_applied": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _reset_assets_state() -> None:
    """Delete ONLY assets_* keys — leaves audit_* and prompts_* untouched."""
    for key in [k for k in list(st.session_state.keys()) if k.startswith("assets_")]:
        del st.session_state[key]


def _prefill_assets_from_shared() -> None:
    """Pull brand (q1) / url (q3) from geo_shared_* on first questionnaire entry.

    Gated by the prefill_applied flag + assets_generated empty (don't blow away
    answers if user has already generated assets in this session).
    """
    if st.session_state.assets_prefill_applied:
        return
    if st.session_state.assets_generated:
        return  # user already ran the generator — protect their answers

    answers = st.session_state.assets_answers
    applied = False
    if not answers.get("q1") and st.session_state.get("geo_shared_brand_name"):
        answers["q1"] = st.session_state.geo_shared_brand_name
        applied = True
    if not answers.get("q3") and st.session_state.get("geo_shared_url"):
        answers["q3"] = st.session_state.geo_shared_url
        applied = True

    if applied:
        st.session_state.assets_prefill_applied = True


def _render_prompts_cta() -> None:
    """Link to the Prompt Engine page when multi-page mode is active.

    In standalone mode (`streamlit run mvp_c_assets/app.py`), pages/ isn't in
    scope → st.page_link raises and we fall back to a passive hint.
    """
    try:
        st.page_link(
            "prompts",
            label="📊 See Where AI Recommends You (vs Competitors)",
            icon="📊",
        )
        st.caption("Your brand details will be pre-filled automatically.")
    except Exception:
        st.info("📊 To see your AI visibility, open Prompt Engine separately.")


# ── Step renderers ───────────────────────────────────────────────────────────

def _render_assets_welcome() -> None:
    # Email gate — must complete an Audit first (which is where we capture it).
    if not st.session_state.get("geo_shared_email_captured"):
        st.warning("Please complete the GEO Audit first to access this tool.")
        try:
            st.page_link("audit", label="← Go to GEO Audit", icon="🔍")
        except Exception:
            st.info("🔍 Open the GEO Audit to get started.")
        st.stop()

    # Hero — viral entry framing.
    st.markdown("# Make your brand AI-ready.")
    st.markdown("Generate 6 AI-optimized files in 5 minutes.")
    st.divider()

    st.markdown(
        "Answer 15 questions about your brand and receive **6 AI-generated files** "
        "that help AI search engines (ChatGPT, Perplexity, Gemini) understand and "
        "recommend your brand accurately."
    )
    st.markdown("**Files you'll receive:**")
    for filename, _ in ASSET_TASKS:
        st.markdown(f"- `{filename}`")
    st.markdown("---")
    if st.button("Get Started →", type="primary", key="assets_btn_start"):
        st.session_state.assets_step = "questionnaire"
        st.rerun()


def _render_assets_questionnaire() -> None:
    _prefill_assets_from_shared()

    group_idx: int = st.session_state.assets_group_idx
    group = QUESTION_GROUPS[group_idx]
    total = len(QUESTION_GROUPS)

    st.progress((group_idx) / total, text=f"Step {group_idx + 1} of {total}")
    st.subheader(group["title"])

    # Banner only on the first group (where the prefilled q1/q3 actually live).
    if group_idx == 0 and st.session_state.assets_prefill_applied:
        source = st.session_state.get("geo_shared_source_tool", "another tool")
        st.info(f"📥 Brand info imported from {source}. Review the details and proceed.")

    answers: dict[str, str] = st.session_state.assets_answers

    for q in group["questions"]:
        key = q["key"]
        label = q["label"] + (" *" if q.get("required") else "")
        if q.get("area"):
            answers[key] = st.text_area(
                label, value=answers[key], help=q.get("help", ""),
                key=f"assets_input_{key}",
            )
        else:
            answers[key] = st.text_input(
                label, value=answers[key], help=q.get("help", ""),
                key=f"assets_input_{key}",
            )

    st.session_state.assets_answers = answers

    col_back, col_next = st.columns([1, 4])
    with col_back:
        if group_idx > 0 and st.button("← Back", key="assets_btn_q_back"):
            st.session_state.assets_group_idx -= 1
            st.rerun()
    with col_next:
        label_next = "Review →" if group_idx == total - 1 else "Next →"
        if st.button(label_next, type="primary", key=f"assets_btn_next_{group_idx}"):
            missing = [
                q["label"]
                for q in group["questions"]
                if q.get("required") and not answers.get(q["key"], "").strip()
            ]
            if missing:
                st.error(f"Please fill in: {', '.join(missing)}")
            elif group_idx == total - 1:
                st.session_state.assets_step = "preview"
                st.rerun()
            else:
                st.session_state.assets_group_idx += 1
                st.rerun()


def _render_assets_preview() -> None:
    st.title("Review Your Answers")
    st.markdown("Check everything looks right before generating.")

    answers: dict[str, str] = st.session_state.assets_answers
    for g in QUESTION_GROUPS:
        with st.expander(g["title"], expanded=False):
            for q in g["questions"]:
                val = answers.get(q["key"], "").strip() or "_Not provided_"
                st.markdown(f"**{q['label']}:** {val}")

    st.markdown("---")
    col_edit, col_go = st.columns([1, 3])
    with col_edit:
        if st.button("← Edit", key="assets_btn_edit"):
            st.session_state.assets_step = "questionnaire"
            st.session_state.assets_group_idx = 0
            st.rerun()
    with col_go:
        if st.button("Generate Assets →", type="primary", key="assets_btn_generate"):
            st.session_state.assets_step = "generating"
            st.rerun()


def _render_assets_generating() -> None:
    st.title("Generating Your Assets…")
    st.markdown("This usually takes 30–90 seconds. Do not close this tab.")

    get_openrouter_client()

    answers = st.session_state.assets_answers
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

    st.session_state.assets_generated = generated
    st.session_state.assets_errors = errors

    if generated:
        st.session_state.assets_zip_bytes = build_zip(generated)

    st.session_state.assets_step = "done"
    st.rerun()


def _render_assets_done() -> None:
    generated: dict[str, str] = st.session_state.assets_generated
    errors: dict[str, str] = st.session_state.assets_errors
    zip_bytes: bytes | None = st.session_state.assets_zip_bytes

    # Export to cross-tool shared namespace (idempotent).
    answers = st.session_state.assets_answers
    primary_brand = (answers.get("q1", "") or "").split(",")[0].strip()
    primary_url = (answers.get("q3", "") or "").strip()
    if primary_brand:
        st.session_state.geo_shared_brand_name = primary_brand
    if primary_url:
        st.session_state.geo_shared_url = primary_url
    st.session_state.geo_shared_source_tool = "Asset Generator"

    st.title("Your GEO Assets Are Ready")

    if errors:
        st.warning(f"{len(errors)} file(s) failed to generate and are excluded from the download.")
        with st.expander("Show errors"):
            for fname, tb in errors.items():
                st.markdown(f"**{fname}**")
                st.code(tb, language="text")

    if zip_bytes:
        brand_name = st.session_state.assets_answers.get("q1", "brand").split(",")[0].strip().lower().replace(" ", "_")
        st.download_button(
            label=f"Download {len(generated)} files as ZIP",
            data=zip_bytes,
            file_name=f"{brand_name}_geo_assets.zip",
            mime="application/zip",
            type="primary",
            key="assets_btn_download",
        )

    st.markdown("---")
    st.markdown("**Preview generated files:**")
    for filename, content in generated.items():
        with st.expander(filename):
            lang = "json" if filename.endswith(".json") else "markdown" if filename.endswith(".md") else "text"
            st.code(content, language=lang)

    st.markdown("---")
    st.markdown("**Now find out where AI recommends you — and where it doesn't:**")
    _render_prompts_cta()

    st.divider()
    st.markdown(f"### 🚀 Get monthly updates — {PRICE_PRO_MONTHLY_DISPLAY}")
    st.markdown(
        "AI models update every month. Your assets should too.\n"
        "- Monthly regeneration based on latest AI behavior\n"
        "- Optimized for ChatGPT, Claude, Perplexity, Gemini\n"
        "- Email delivery (no need to login)\n"
        "- All Audit + Prompts features included"
    )
    st.link_button(
        f"🚀 Subscribe to Pro — {PRICE_PRO_MONTHLY_DISPLAY}",
        PAYMENT_LINK_PRO_MONTHLY,
        type="primary",
    )

    st.markdown("---")
    if st.button("Start Over", key="assets_btn_restart"):
        _reset_assets_state()
        st.rerun()


# ── Public entry point ───────────────────────────────────────────────────────

def render_assets_page() -> None:
    """Render the full GEO Asset Generator UI based on current assets_step."""
    _init_assets_state()
    step = st.session_state.assets_step
    if step == "welcome":
        _render_assets_welcome()
    elif step == "questionnaire":
        _render_assets_questionnaire()
    elif step == "preview":
        _render_assets_preview()
    elif step == "generating":
        _render_assets_generating()
    elif step == "done":
        _render_assets_done()
    render_footer()
