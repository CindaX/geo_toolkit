"""MVP-A GEO Audit UI — extracted for reuse across standalone and multi-page modes.

All session_state keys are prefixed with `audit_` so they don't collide with
MVP-B (`prompts_*`) or MVP-C (`assets_*`) when all three live in the same
Streamlit process.
"""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st

from shared._openrouter import get_openrouter_client
from shared.claude_client import reset_usage_stats
from shared.email_capture import save_email
from shared.pdf_generator import audit_result_to_pdf_dict, generate_audit_pdf
from shared.rate_limit import check_and_record
from shared.stripe_links import (
    PAYMENT_LINK_AUDIT_FULL,
    PAYMENT_LINK_PRO_MONTHLY,
    PRICE_AUDIT_FULL_DISPLAY,
    PRICE_PRO_MONTHLY_DISPLAY,
)
from shared.ui_components import render_footer

from mvp_a_audit.logic import (
    ANALYZERS,
    _WEIGHTS,
    _build_audit_result,
    _failed_result,
    prepare_inputs,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_INDUSTRIES = [
    "SaaS", "E-commerce", "Professional Services",
    "Manufacturing", "Media & Content",
    "Education", "Healthcare", "Finance", "Other",
]

_DIM_LABELS: dict[str, str] = {
    "crawler_access":     "AI Crawler Access",
    "llms_txt":           "llms.txt Presence",
    "schema_markup":      "Schema Markup",
    "brand_clarity":      "Brand Clarity",
    "content_citability": "Content Citability",
    "eeat_signals":       "E-E-A-T Signals",
    "competitive":        "Competitive Positioning",
    "factual_density":    "Factual Density",
}


# ── Session state ────────────────────────────────────────────────────────────

def _init_audit_state() -> None:
    defaults = {
        "audit_step":             "welcome",
        "audit_url":              "",
        "audit_brand_name":       "",
        "audit_industry":         "SaaS",
        "audit_results":          {},
        "audit_geo_score":        None,
        "audit_geo_grade":        None,
        "audit_ai_understanding": None,
        "audit_errors":           {},
        "audit_prefill_applied":  False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _reset_audit_state() -> None:
    """Delete ONLY audit_* keys — leaves prompts_* and assets_* untouched."""
    for key in [k for k in list(st.session_state.keys()) if k.startswith("audit_")]:
        del st.session_state[key]


def _prefill_audit_from_shared() -> None:
    """Pull brand/url/industry from geo_shared_* on first entry to the input page.

    - Brand / URL: prefill only when the local field is empty.
    - Industry: prefill only when the user hasn't completed an audit yet
      (audit_results empty) — protects a previous explicit dropdown choice.
    - Flag flips to True only when something was actually applied, so a later
      cross-tool write can still take effect on a return visit.
    """
    if st.session_state.audit_prefill_applied:
        return

    applied = False
    if not st.session_state.audit_brand_name and st.session_state.get("geo_shared_brand_name"):
        st.session_state.audit_brand_name = st.session_state.geo_shared_brand_name
        applied = True
    if not st.session_state.audit_url and st.session_state.get("geo_shared_url"):
        st.session_state.audit_url = st.session_state.geo_shared_url
        applied = True

    audit_not_run = not st.session_state.audit_results
    if audit_not_run and st.session_state.get("geo_shared_industry"):
        if st.session_state.audit_industry != st.session_state.geo_shared_industry:
            st.session_state.audit_industry = st.session_state.geo_shared_industry
            applied = True

    if applied:
        st.session_state.audit_prefill_applied = True


# ── Step renderers ───────────────────────────────────────────────────────────

def _render_audit_welcome() -> None:
    st.title("🔍 GEO Audit")
    st.markdown(
        "Find out how well AI search engines understand your brand. "
        "Enter your website and get an **8-dimension GEO Score** in under 60 seconds."
    )
    st.markdown("**What you get for free:**")
    st.markdown(
        "- GEO Score (0–100) with grade\n"
        "- Scores across 8 dimensions\n"
        "- **'What AI thinks you do'** — the viral diagnostic"
    )
    st.markdown("**Unlock for $19:**")
    st.markdown(
        "- Top-5 prioritized fix recommendations\n"
        "- Ready-to-use code snippets (robots.txt, JSON-LD, llms.txt)\n"
        "- 30-day improvement roadmap\n"
        "- Link to generate all 6 GEO asset files"
    )
    st.markdown("---")
    if st.button("Start Free Audit →", type="primary", key="audit_btn_start"):
        st.session_state.audit_step = "input"
        st.rerun()


def _render_audit_input() -> None:
    _prefill_audit_from_shared()

    # Hero — viral entry framing.
    st.markdown("# Is your brand visible to AI?")
    st.markdown("Find out in 60 seconds. Free for 1 audit per hour.")
    st.divider()

    # Email gate — captured once, shared across all 3 tools via geo_shared_*.
    if not st.session_state.get("geo_shared_email_captured"):
        st.markdown("### 📧 Get your free GEO Audit")
        st.markdown("Enter your email to start. We'll send you GEO tips and updates.")
        email = st.text_input(
            "Email *",
            key="audit_email_input",
            placeholder="you@example.com",
        )
        if st.button("Start Free Audit →", type="primary", key="audit_email_submit"):
            if not email or "@" not in email or "." not in email:
                st.error("Please enter a valid email address.")
                st.stop()
            st.session_state["geo_shared_email"] = email
            st.session_state["geo_shared_email_captured"] = True
            save_email(email, source="audit")
            st.rerun()
        st.stop()  # block the rest of the form until email captured

    if st.session_state.audit_prefill_applied:
        source = st.session_state.get("geo_shared_source_tool", "another tool")
        st.info(f"📥 Brand info imported from {source}. Review the details and proceed.")

    url = st.text_input(
        "Website URL *",
        value=st.session_state.audit_url,
        placeholder="https://example.com",
        help="Include https://",
        key="audit_input_url",
    )
    brand_name = st.text_input(
        "Brand Name *",
        value=st.session_state.audit_brand_name,
        placeholder="Acme Corp",
        key="audit_input_brand",
    )
    industry = st.selectbox(
        "Industry *",
        options=_INDUSTRIES,
        index=_INDUSTRIES.index(st.session_state.audit_industry)
        if st.session_state.audit_industry in _INDUSTRIES else 0,
        key="audit_input_industry",
    )

    st.markdown("---")
    col_back, col_run = st.columns([1, 4])
    with col_back:
        if st.button("← Back", key="audit_btn_back"):
            st.session_state.audit_step = "welcome"
            st.rerun()
    with col_run:
        if st.button("Run Audit →", type="primary", key="audit_btn_run"):
            errors: list[str] = []
            if not url.strip():
                errors.append("Website URL is required.")
            if not url.strip().startswith(("http://", "https://")):
                errors.append("URL must start with http:// or https://")
            if not brand_name.strip():
                errors.append("Brand Name is required.")
            if errors:
                for e in errors:
                    st.error(e)
            else:
                st.session_state.audit_url = url.strip()
                st.session_state.audit_brand_name = brand_name.strip()
                st.session_state.audit_industry = industry
                st.session_state.audit_step = "scanning"
                st.rerun()


def _render_audit_scanning() -> None:
    # Rate limit — 1 audit / hour / IP. Check before any LLM work happens.
    try:
        headers = getattr(st.context, "headers", None) or {}
        ip = headers.get("x-forwarded-for", "unknown") or "unknown"
        if "," in ip:
            ip = ip.split(",")[0].strip()
    except Exception:
        ip = "unknown"

    allowed, retry_after = check_and_record(ip)
    if not allowed:
        mins = max(retry_after // 60, 1)
        st.error(f"⏰ Rate limit reached. Please try again in {mins} minutes.")
        st.markdown("This limit protects free users. Premium tier coming soon!")
        if st.button("← Back to input", key="audit_btn_rate_back"):
            st.session_state.audit_step = "input"
            st.rerun()
        st.stop()

    st.title("Scanning Your Website…")
    st.markdown("Running 8 GEO dimensions in parallel. Usually takes 20–60 seconds.")

    url = st.session_state.audit_url
    brand_name = st.session_state.audit_brand_name
    industry = st.session_state.audit_industry

    get_openrouter_client()
    reset_usage_stats()

    progress_bar = st.progress(0.0)
    status_text = st.empty()

    status_text.markdown("Crawling website…")
    try:
        inputs = prepare_inputs(url, brand_name, industry)
    except Exception:
        st.error(f"Failed to crawl {url}. Check the URL and try again.")
        st.code(traceback.format_exc(), language="text")
        if st.button("← Try Again", key="audit_btn_crawl_retry"):
            st.session_state.audit_step = "input"
            st.rerun()
        return

    audit_results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    total = len(ANALYZERS)

    futures_map: dict = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for key, mod in ANALYZERS:
            futures_map[executor.submit(mod.analyze, **inputs)] = key  # type: ignore[attr-defined]

        completed = 0
        for future in as_completed(futures_map):
            key = futures_map[future]
            completed += 1
            progress_bar.progress(completed / total)
            status_text.markdown(
                f"✓ **{_DIM_LABELS.get(key, key)}** ({completed}/{total})"
            )
            try:
                audit_results[key] = future.result()
            except Exception as exc:
                tb = traceback.format_exc()
                debug_line = (
                    f"\n[ANALYZER ERROR] {key}: {type(exc).__name__}: {exc}\n{tb}\n"
                )
                print(debug_line, flush=True)
                with open(".audit_debug.log", "a", encoding="utf-8") as _log:
                    _log.write(debug_line)
                audit_results[key] = _failed_result(key, tb)
                errors[key] = tb

    final = _build_audit_result(url, brand_name, industry, audit_results)
    st.session_state.audit_results = final["results"]
    st.session_state.audit_geo_score = final["geo_score"]
    st.session_state.audit_geo_grade = final["geo_grade"]
    st.session_state.audit_ai_understanding = final["ai_understanding"]
    st.session_state.audit_errors = errors

    st.session_state.audit_step = "free"
    st.rerun()


def _render_audit_free_report() -> None:
    # Export to cross-tool shared namespace (idempotent; written each render).
    st.session_state.geo_shared_brand_name = st.session_state.audit_brand_name
    st.session_state.geo_shared_url = st.session_state.audit_url
    st.session_state.geo_shared_industry = st.session_state.audit_industry
    st.session_state.geo_shared_source_tool = "Audit"

    # Detect Stripe payment-success callback (Stripe success_url ends in ?paid=true).
    is_paid = (st.query_params.get("paid") == "true")

    # Post-payment block — renders above the normal report so it's the first
    # thing the user sees when redirected back from Stripe Checkout.
    if is_paid:
        if not st.session_state.get("post_payment_celebrated"):
            st.balloons()
            st.session_state["post_payment_celebrated"] = True
        st.success("✅ Payment successful — your detailed PDF report is ready.")
        if st.session_state.audit_results:
            try:
                pdf_dict = audit_result_to_pdf_dict(
                    audit_results=st.session_state.audit_results,
                    geo_score=st.session_state.audit_geo_score,
                    ai_understanding=st.session_state.audit_ai_understanding,
                )
                pdf_bytes = generate_audit_pdf(
                    pdf_dict,
                    brand_name=st.session_state.audit_brand_name,
                    url=st.session_state.audit_url,
                )
                safe_slug = (st.session_state.audit_brand_name or "brand").lower().replace(" ", "_")
                st.download_button(
                    "📄 Download Your Detailed PDF Report",
                    data=pdf_bytes,
                    file_name=f"geo_audit_{safe_slug}.pdf",
                    mime="application/pdf",
                    type="primary",
                    key="audit_btn_pdf_paid_download",
                )
            except Exception as exc:
                st.error(f"PDF generation failed: {type(exc).__name__}: {exc}")
        else:
            st.warning(
                "We couldn't find your audit result in this session. "
                "Please re-run the audit; your PDF will regenerate."
            )
        st.divider()

    score = st.session_state.audit_geo_score
    grade = st.session_state.audit_geo_grade
    results = st.session_state.audit_results
    ai_understanding = st.session_state.audit_ai_understanding

    # Hero — frames the result as a "report" rather than a raw tool output.
    st.markdown("# Your AI Visibility Report")
    st.caption(
        f"Analyzing {st.session_state.audit_url} • {st.session_state.audit_brand_name}"
    )
    st.divider()

    col_score, col_grade = st.columns([1, 2])
    with col_score:
        score_display = f"{score:.0f}" if score is not None else "N/A"
        st.metric("GEO Score", score_display, delta=None)
    with col_grade:
        st.markdown(f"## {grade}")
        st.caption(
            f"Based on {len([r for r in results.values() if r.get('score') is not None])} "
            f"of 8 dimensions scored"
        )

    st.markdown("---")

    st.markdown("### 🤖 What AI Thinks You Do")
    if ai_understanding:
        st.info(f'"{ai_understanding}"')
        st.caption(
            "This is how an AI system would describe your brand to a user who asked about you. "
            "If it's wrong or vague, your GEO score reflects that gap."
        )
    else:
        st.warning("AI understanding could not be generated — Brand Clarity scan may have failed.")

    st.markdown("---")

    st.markdown("### Dimension Scores")
    n_cols = 4
    for row_start in range(0, len(ANALYZERS), n_cols):
        row_items = ANALYZERS[row_start:row_start + n_cols]
        cols = st.columns(n_cols)
        for col_idx, (key, _) in enumerate(row_items):
            result = results.get(key, {})
            dim_score = result.get("score")
            description = result.get("description", "") or ""
            error = result.get("error")
            label = _DIM_LABELS.get(key, key)

            with cols[col_idx]:
                if dim_score is None:
                    st.metric(label, "—", delta="❌ Failed", delta_color="off")
                    if error:
                        with st.expander("Error"):
                            st.code(error[:500], language="text")
                else:
                    if dim_score >= 60:
                        badge = "🟢 Good"
                    elif dim_score >= 40:
                        badge = "🟡 Fair"
                    else:
                        badge = "🔴 Needs work"
                    st.metric(label, f"{dim_score}", delta=badge, delta_color="off")
                    short_desc = description[:80] + ("…" if len(description) > 80 else "")
                    st.caption(short_desc)

    if not is_paid:
        st.divider()
        st.markdown("### 🚀 Get more from your GEO Audit")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"#### 🎯 {PRICE_AUDIT_FULL_DISPLAY} one-time")
            st.markdown(
                "- Detailed PDF report (downloadable)\n"
                "- All 8 dimensions deep-dive\n"
                "- Specific fixes for each gap"
            )
            st.link_button(
                f"🎯 Get Full Report — {PRICE_AUDIT_FULL_DISPLAY}",
                PAYMENT_LINK_AUDIT_FULL,
                type="secondary",
                width="stretch",
            )
        with col2:
            st.markdown(f"#### 🚀 {PRICE_PRO_MONTHLY_DISPLAY}")
            st.markdown(
                "- Weekly automated re-audits\n"
                "- Continuous monitoring + alerts\n"
                "- All Prompt + Asset features"
            )
            st.link_button(
                f"🚀 Subscribe to Pro — {PRICE_PRO_MONTHLY_DISPLAY}",
                PAYMENT_LINK_PRO_MONTHLY,
                type="primary",
                width="stretch",
            )

    st.markdown("---")
    if st.button("Audit Another Site", key="audit_btn_restart_free"):
        _reset_audit_state()
        st.rerun()


# ── Public entry point ───────────────────────────────────────────────────────

def render_audit_page() -> None:
    """Render the full GEO Audit UI based on current audit_step."""
    _init_audit_state()
    step = st.session_state.audit_step
    if step == "welcome":
        _render_audit_welcome()
    elif step == "input":
        _render_audit_input()
    elif step == "scanning":
        _render_audit_scanning()
    elif step == "free":
        _render_audit_free_report()
    render_footer()
