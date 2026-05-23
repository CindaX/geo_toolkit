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

from mvp_a_audit.logic import (
    ANALYZERS,
    _WEIGHTS,
    _build_audit_result,
    _failed_result,
    generate_recommendations,
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
        "audit_recommendations":  None,
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


# ── Cross-tool CTA helper ────────────────────────────────────────────────────

def _render_assets_cta() -> None:
    """Link to the Asset Generator page when multi-page mode is active.

    In standalone mode (`streamlit run mvp_a_audit/app.py`), the pages/
    directory doesn't exist relative to entry → st.page_link raises and we
    fall back to a passive hint.
    """
    try:
        st.page_link(
            "assets",
            label="→ Generate AI Assets to Fix These Issues",
            icon="📦",
        )
        st.caption("Your brand details will be pre-filled automatically.")
    except Exception:
        st.info("📦 To generate AI-ready files, run Asset Generator separately.")


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

    st.title("Enter Your Website Details")

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
    cols = st.columns(2)
    for i, (key, _) in enumerate(ANALYZERS):
        result = results.get(key, {})
        dim_score = result.get("score")
        description = result.get("description", "")
        error = result.get("error")
        weight_pct = int(_WEIGHTS.get(key, 0) * 100)

        with cols[i % 2]:
            with st.container(border=True):
                label = _DIM_LABELS.get(key, key)
                if dim_score is None:
                    st.markdown(f"**{label}** _(weight: {weight_pct}%)_")
                    st.markdown("❌ Scan failed")
                    if error:
                        with st.expander("Error details"):
                            st.code(error[:500], language="text")
                else:
                    color = "🟢" if dim_score >= 70 else "🟡" if dim_score >= 40 else "🔴"
                    st.markdown(f"**{label}** _(weight: {weight_pct}%)_")
                    st.markdown(f"{color} **{dim_score}/100**")
                    st.caption(description)

    st.markdown("---")
    st.markdown("### Ready to Fix These Issues?")
    st.markdown(
        "Unlock detailed diagnostics, prioritized fixes with copy-paste code snippets, "
        "and a 30-day improvement roadmap."
    )
    if st.button("🔓 Unlock Detailed Fixes — $19", type="primary", key="audit_btn_unlock"):
        st.session_state.audit_step = "unlocked"
        st.rerun()

    st.markdown("---")
    if st.button("Audit Another Site", key="audit_btn_restart_free"):
        _reset_audit_state()
        st.rerun()


def _render_audit_unlocked() -> None:
    # Export to cross-tool shared namespace (idempotent; written each render).
    st.session_state.geo_shared_brand_name = st.session_state.audit_brand_name
    st.session_state.geo_shared_url = st.session_state.audit_url
    st.session_state.geo_shared_industry = st.session_state.audit_industry
    st.session_state.geo_shared_source_tool = "Audit"

    st.title("GEO Audit — Full Report")
    st.success("✓ Unlocked — Full diagnostics and fix plan below")

    results = st.session_state.audit_results
    brand_name = st.session_state.audit_brand_name
    url = st.session_state.audit_url
    industry = st.session_state.audit_industry

    if st.session_state.audit_recommendations is None:
        with st.spinner("Generating personalized fix plan…"):
            rec = generate_recommendations(results, brand_name, url, industry)
            st.session_state.audit_recommendations = rec

    rec = st.session_state.audit_recommendations or {}
    fixes = rec.get("fixes", [])
    roadmap = rec.get("roadmap", {})
    summary = rec.get("summary", "")

    if summary:
        st.markdown(f"**Summary:** {summary}")

    st.markdown("---")
    st.markdown("### 🎯 Top 5 Priority Fixes")
    for fix in fixes:
        rank = fix.get("rank", "?")
        title = fix.get("title", "Fix")
        impact = fix.get("impact", "")
        difficulty = fix.get("difficulty", "")
        method = fix.get("method", "")
        snippet = fix.get("code_snippet", "")
        dim_score = fix.get("current_score")
        dim = fix.get("dimension", "")

        with st.expander(
            f"#{rank} — {title} ({_DIM_LABELS.get(dim, dim)}: {dim_score}/100)",
            expanded=(rank == 1),
        ):
            cols = st.columns(2)
            cols[0].markdown(f"**Impact:** {impact}")
            cols[1].markdown(f"**Difficulty:** {difficulty}")
            st.markdown(method)
            if snippet:
                st.code(snippet, language="text")

    st.markdown("---")
    st.markdown("### 🔬 Detailed Dimension Diagnosis")
    for key, _ in ANALYZERS:
        result = results.get(key, {})
        dim_score = result.get("score")
        details = result.get("details", {})
        label = _DIM_LABELS.get(key, key)

        with st.expander(
            f"{label} — {dim_score}/100" if dim_score is not None else f"{label} — failed"
        ):
            if dim_score is None:
                st.error(result.get("description", "Scan failed."))
            else:
                st.markdown(result.get("description", ""))
                if details:
                    st.json(details)

    if roadmap:
        st.markdown("---")
        st.markdown("### 📅 30-Day Improvement Roadmap")
        for week_key in ["week1", "week2", "week3", "week4"]:
            actions = roadmap.get(week_key, [])
            if actions:
                week_num = week_key.replace("week", "Week ")
                st.markdown(f"**{week_num}**")
                for action in actions:
                    st.markdown(f"- {action}")

    st.markdown("---")
    st.markdown(
        "**Next step:** Generate all 6 GEO asset files (llms.txt, brand-facts.md, "
        "schema.json, FAQ, comparison, product facts) automatically."
    )
    _render_assets_cta()

    st.markdown("---")
    if st.button("Audit Another Site", key="audit_btn_restart_unlocked"):
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
    elif step == "unlocked":
        _render_audit_unlocked()
