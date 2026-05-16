"""MVP-A: GEO Audit Report — Streamlit app.

5-step state machine:
  welcome → input → scanning → free → unlocked
"""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st

from shared._openrouter import get_openrouter_client
from shared.claude_client import reset_usage_stats

from mvp_a_audit.analyzers import (
    brand_clarity,
    competitive,
    content_citability,
    crawler_access,
    eeat_signals,
    factual_density,
    llms_txt,
    schema_markup,
)
from mvp_a_audit.logic import (
    ANALYZERS,
    _WEIGHTS,
    _build_audit_result,
    _failed_result,
    compute_geo_score,
    estimate_cost,
    generate_recommendations,
    get_grade,
    prepare_inputs,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_INDUSTRIES = [
    "SaaS", "电商 / E-commerce", "服务业 / Professional Services",
    "制造业 / Manufacturing", "媒体内容 / Media & Content",
    "教育 / Education", "医疗 / Healthcare", "金融 / Finance", "其他 / Other",
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


# ── Session state init ────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "step": "welcome",
        "url": "",
        "brand_name": "",
        "industry": "SaaS",
        "audit_results": {},        # dim_key -> result dict
        "geo_score": None,
        "geo_grade": None,
        "ai_understanding": None,
        "recommendations": None,
        "errors": {},               # dim_key -> traceback string
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── Step renderers ────────────────────────────────────────────────────────────

def render_welcome() -> None:
    st.title("GEO Audit Report")
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
    if st.button("Start Free Audit →", type="primary"):
        st.session_state.step = "input"
        st.rerun()


def render_input() -> None:
    st.title("Enter Your Website Details")

    url = st.text_input(
        "Website URL *",
        value=st.session_state.url,
        placeholder="https://example.com",
        help="Include https://",
    )
    brand_name = st.text_input(
        "Brand Name *",
        value=st.session_state.brand_name,
        placeholder="Acme Corp",
    )
    industry = st.selectbox(
        "Industry *",
        options=_INDUSTRIES,
        index=_INDUSTRIES.index(st.session_state.industry)
        if st.session_state.industry in _INDUSTRIES else 0,
    )

    st.markdown("---")
    col_back, col_run = st.columns([1, 4])
    with col_back:
        if st.button("← Back"):
            st.session_state.step = "welcome"
            st.rerun()
    with col_run:
        if st.button("Run Audit →", type="primary"):
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
                st.session_state.url = url.strip()
                st.session_state.brand_name = brand_name.strip()
                st.session_state.industry = industry
                st.session_state.step = "scanning"
                st.rerun()


def render_scanning() -> None:
    st.title("Scanning Your Website…")
    st.markdown("Running 8 GEO dimensions in parallel. Usually takes 20–60 seconds.")

    url = st.session_state.url
    brand_name = st.session_state.brand_name
    industry = st.session_state.industry

    # Pre-warm OpenRouter client on the main thread before spawning workers
    get_openrouter_client()
    reset_usage_stats()

    progress_bar = st.progress(0.0)
    status_text = st.empty()

    # Step 1: Crawl (blocking, on main thread)
    status_text.markdown("Crawling website…")
    try:
        inputs = prepare_inputs(url, brand_name, industry)
    except Exception:
        st.error(f"Failed to crawl {url}. Check the URL and try again.")
        st.code(traceback.format_exc(), language="text")
        if st.button("← Try Again"):
            st.session_state.step = "input"
            st.rerun()
        return

    # Step 2: Run 8 analyzers in parallel
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

    # Build final audit result and store in session state
    final = _build_audit_result(url, brand_name, industry, audit_results)
    st.session_state.audit_results = final["results"]
    st.session_state.geo_score = final["geo_score"]
    st.session_state.geo_grade = final["geo_grade"]
    st.session_state.ai_understanding = final["ai_understanding"]
    st.session_state.errors = errors

    st.session_state.step = "free"
    st.rerun()


def render_free_report() -> None:
    score = st.session_state.geo_score
    grade = st.session_state.geo_grade
    results = st.session_state.audit_results
    ai_understanding = st.session_state.ai_understanding

    st.title("Your GEO Audit Report")

    # ── GEO Score hero ────────────────────────────────────────────────────────
    col_score, col_grade = st.columns([1, 2])
    with col_score:
        score_display = f"{score:.0f}" if score is not None else "N/A"
        st.metric("GEO Score", score_display, delta=None)
    with col_grade:
        st.markdown(f"## {grade}")
        st.caption(f"Based on {len([r for r in results.values() if r.get('score') is not None])} of 8 dimensions scored")

    st.markdown("---")

    # ── AI Understanding (viral section) ─────────────────────────────────────
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

    # ── 8-dimension score cards ───────────────────────────────────────────────
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

    # ── CTA ───────────────────────────────────────────────────────────────────
    st.markdown("### Ready to Fix These Issues?")
    st.markdown(
        "Unlock detailed diagnostics, prioritized fixes with copy-paste code snippets, "
        "and a 30-day improvement roadmap."
    )
    if st.button("🔓 Unlock Detailed Fixes — $19", type="primary"):
        st.session_state.step = "unlocked"
        st.rerun()

    st.markdown("---")
    if st.button("Audit Another Site"):
        _reset_state()
        st.rerun()


def render_unlocked() -> None:
    st.title("GEO Audit — Full Report")
    st.success("✓ Unlocked — Full diagnostics and fix plan below")

    results = st.session_state.audit_results
    brand_name = st.session_state.brand_name
    url = st.session_state.url
    industry = st.session_state.industry

    # ── Generate recommendations (once) ──────────────────────────────────────
    if st.session_state.recommendations is None:
        with st.spinner("Generating personalized fix plan…"):
            rec = generate_recommendations(results, brand_name, url, industry)
            st.session_state.recommendations = rec

    rec = st.session_state.recommendations or {}
    fixes = rec.get("fixes", [])
    roadmap = rec.get("roadmap", {})
    summary = rec.get("summary", "")

    if summary:
        st.markdown(f"**Summary:** {summary}")

    # ── Top-5 fixes ───────────────────────────────────────────────────────────
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

        with st.expander(f"#{rank} — {title} ({_DIM_LABELS.get(dim, dim)}: {dim_score}/100)", expanded=(rank == 1)):
            cols = st.columns(2)
            cols[0].markdown(f"**Impact:** {impact}")
            cols[1].markdown(f"**Difficulty:** {difficulty}")
            st.markdown(method)
            if snippet:
                st.code(snippet, language="text")

    # ── Per-dimension detailed diagnosis ─────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🔬 Detailed Dimension Diagnosis")
    for key, _ in ANALYZERS:
        result = results.get(key, {})
        dim_score = result.get("score")
        details = result.get("details", {})
        label = _DIM_LABELS.get(key, key)

        with st.expander(f"{label} — {dim_score}/100" if dim_score is not None else f"{label} — failed"):
            if dim_score is None:
                st.error(result.get("description", "Scan failed."))
            else:
                st.markdown(result.get("description", ""))
                if details:
                    st.json(details)

    # ── 30-day roadmap ────────────────────────────────────────────────────────
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

    # ── CTA to MVP-C ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.info(
        "**Next step:** Generate all 6 GEO asset files (llms.txt, brand-facts.md, "
        "schema.json, FAQ, comparison, product facts) automatically.\n\n"
        "[→ Open GEO Asset Generator](http://localhost:8502)",
        icon="🚀",
    )

    st.markdown("---")
    if st.button("Audit Another Site"):
        _reset_state()
        st.rerun()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_state() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="GEO Audit Report",
        page_icon="📊",
        layout="centered",
    )
    _init_state()

    step = st.session_state.step
    if step == "welcome":
        render_welcome()
    elif step == "input":
        render_input()
    elif step == "scanning":
        render_scanning()
    elif step == "free":
        render_free_report()
    elif step == "unlocked":
        render_unlocked()


if __name__ == "__main__":
    main()
