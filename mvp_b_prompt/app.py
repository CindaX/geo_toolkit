"""MVP-B: Prompt Opportunity Engine — Streamlit app.

5-step state machine:
  welcome → input → scanning → free → paid
"""

from __future__ import annotations

import traceback

import pandas as pd
import streamlit as st

from shared._openrouter import get_openrouter_client
from shared.claude_client import reset_usage_stats

from mvp_b_prompt.logic import (
    find_opportunities,
    generate_prompts,
    match_brand,
    simulate_answers,
    classify_awareness,
    compute_sov,
    compute_position_score,
    compute_category_strength,
    find_direct_comparisons,
    check_control_prompt,
    estimate_cost,
)
from shared.claude_client import get_usage_stats


# ── Constants ─────────────────────────────────────────────────────────────────

_INDUSTRIES = [
    "SaaS",
    "电商 / E-commerce",
    "服务业 / Professional Services",
    "制造业 / Manufacturing",
    "消费电子 / Consumer Electronics",
    "媒体内容 / Media & Content",
    "其他 / Other",
]

_PERSPECTIVES = [
    "B2C 消费者",
    "B2B 决策者",
    "开发者",
]

_TYPE_LABELS: dict[str, str] = {
    "comparison":       "Comparison",
    "recommendation":   "Recommendation",
    "problem_solving":  "Problem-Solving",
    "feature_specific": "Feature-Specific",
    "buying_stage":     "Buying-Stage",
}

_AWARENESS_BADGE: dict[str, tuple[str, str]] = {
    "strong":   ("🟢 STRONG",   "AI recognizes your brand as a major player in this category."),
    "moderate": ("🟡 MODERATE", "AI cites your brand in some queries but inconsistently."),
    "weak":     ("🟠 WEAK",     "AI rarely mentions your brand. Significant SEO gap."),
    "unknown":  ("🔴 UNKNOWN",  "AI has no recognition of your brand. Build authority FIRST — Wikipedia entry, press coverage, named citations on industry sites."),
}


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "step":         "welcome",
        "brand_name":   "",
        "industry":     "SaaS",
        "competitors":  "",
        "perspective":  "B2C 消费者",
        "result":       None,    # dict from run_analysis
        "error":        None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _reset_state() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


# ── Step renderers ────────────────────────────────────────────────────────────

def render_welcome() -> None:
    st.title("Prompt Opportunity Engine")
    st.markdown(
        "Find out **how often AI assistants mention your brand** in answers to real user queries — "
        "and which prompts your competitors are winning that you should be in."
    )
    st.markdown("**What you get for free:**")
    st.markdown(
        "- Share of Voice (%) vs up to 3 competitors\n"
        "- AI brand awareness rating (strong / moderate / weak / unknown)\n"
        "- How AI categorizes your brand\n"
        "- Your strength across 5 query types"
    )
    st.markdown("**Unlock for $49:**")
    st.markdown(
        "- Top 5 priority gaps with specific content actions\n"
        "- Full 20-prompt simulation with reasons per citation\n"
        "- Monthly monitoring setup"
    )
    st.markdown("---")
    if st.button("Start Free Analysis →", type="primary"):
        st.session_state.step = "input"
        st.rerun()


def render_input() -> None:
    st.title("Tell Us About Your Brand")

    brand_name = st.text_input(
        "Brand Name *",
        value=st.session_state.brand_name,
        placeholder="Bambu Lab",
    )
    industry = st.selectbox(
        "Industry *",
        options=_INDUSTRIES,
        index=_INDUSTRIES.index(st.session_state.industry)
        if st.session_state.industry in _INDUSTRIES else 0,
    )
    competitors = st.text_input(
        "Main competitors * (1–3, comma-separated)",
        value=st.session_state.competitors,
        placeholder="Prusa Research, Creality, Anycubic",
    )
    perspective = st.selectbox(
        "User perspective",
        options=_PERSPECTIVES,
        index=_PERSPECTIVES.index(st.session_state.perspective)
        if st.session_state.perspective in _PERSPECTIVES else 0,
    )

    st.markdown("---")
    col_back, col_run = st.columns([1, 4])
    with col_back:
        if st.button("← Back"):
            st.session_state.step = "welcome"
            st.rerun()
    with col_run:
        if st.button("Analyze My AI Visibility →", type="primary"):
            errors = []
            if not brand_name.strip():
                errors.append("Brand Name is required.")
            comp_list = [c.strip() for c in competitors.split(",") if c.strip()]
            if not (1 <= len(comp_list) <= 3):
                errors.append("Enter 1–3 competitors (comma-separated).")
            if errors:
                for e in errors:
                    st.error(e)
            else:
                st.session_state.brand_name = brand_name.strip()
                st.session_state.industry = industry
                st.session_state.competitors = competitors.strip()
                st.session_state.perspective = perspective
                st.session_state.step = "scanning"
                st.rerun()


def render_scanning() -> None:
    st.title("Analyzing AI Visibility…")
    st.markdown("Running 3 LLM calls in sequence. Usually takes 60–90 seconds.")

    brand_name = st.session_state.brand_name
    industry = st.session_state.industry
    competitors = [c.strip() for c in st.session_state.competitors.split(",") if c.strip()]
    perspective = st.session_state.perspective

    get_openrouter_client()
    reset_usage_stats()

    try:
        with st.status("Generating 20 real-world prompts…", expanded=True) as status:
            st.write(f"Asking Claude for the queries a **{perspective}** would type when researching **{industry}**.")
            gen = generate_prompts(brand_name, industry, perspective, competitors)
            if gen.get("error"):
                raise RuntimeError(f"generate_prompts failed: {gen['error']}")
            prompts = gen.get("prompts", [])
            ai_category = gen.get("ai_category", "")
            st.write(f"✓ {len(prompts)} prompts generated · AI category: **{ai_category}**")
            status.update(label=f"✓ Generated {len(prompts)} prompts", state="complete")

        tracked_brands = [brand_name] + competitors

        with st.status("Simulating AI answers…", expanded=True) as status:
            st.write(f"Asking Claude to predict top-3 brand citations across **{len(tracked_brands)}** brands.")
            sim = simulate_answers(prompts, tracked_brands, industry)
            if sim.get("error"):
                raise RuntimeError(f"simulate_answers failed: {sim['error']}")
            simulation = sim.get("results", [])
            # Enrich
            type_by_id = {p["id"]: p["type"] for p in prompts}
            text_by_id = {p["id"]: p["text"] for p in prompts}
            for r in simulation:
                r["type"] = type_by_id.get(r.get("id"), "unknown")
                r["text"] = text_by_id.get(r.get("id"), "")
                for entry in r.get("top_3", []):
                    entry["matched_brand"] = match_brand(entry.get("brand", ""), tracked_brands)
            st.write(f"✓ Simulated answers for {len(simulation)} prompts")
            status.update(label=f"✓ Simulated {len(simulation)} answers", state="complete")

        user_sov = compute_sov(simulation, brand_name)

        with st.status("Finding your top opportunities…", expanded=True) as status:
            if user_sov >= 100:
                opportunities: list[dict] = []
                st.write("Your brand wins everywhere — no gap analysis needed.")
            else:
                st.write(f"Current SOV: **{user_sov}%**. Searching for high-value gaps…")
                opportunities = find_opportunities(brand_name, industry, simulation)
                st.write(f"✓ Found {len(opportunities)} priority opportunities")
            status.update(label=f"✓ Analysis complete", state="complete")

        # Build the final result dict (same shape run_analysis returns)
        usage = get_usage_stats()
        st.session_state.result = {
            "brand_name": brand_name,
            "industry": industry,
            "competitors": competitors,
            "perspective": perspective,
            "ai_category": ai_category,
            "ai_brand_awareness": classify_awareness(user_sov),
            "control_prompt": check_control_prompt(simulation, prompts, brand_name),
            "prompts": prompts,
            "simulation": simulation,
            "sov": {b: compute_sov(simulation, b) for b in tracked_brands},
            "position_scores": {b: compute_position_score(simulation, b) for b in tracked_brands},
            "category_strength": compute_category_strength(simulation, brand_name),
            "direct_comparisons": find_direct_comparisons(simulation, brand_name),
            "opportunities": opportunities,
            "usage": usage,
            "estimated_cost_usd": estimate_cost(usage),
        }
        st.session_state.step = "free"
        st.rerun()

    except Exception:
        tb = traceback.format_exc()
        st.session_state.error = tb
        st.error("Analysis failed.")
        st.code(tb[:1500], language="text")
        if st.button("← Try Again"):
            st.session_state.step = "input"
            st.session_state.error = None
            st.rerun()


def render_free_report() -> None:
    result = st.session_state.result
    if not result:
        st.error("No result available. Start over.")
        if st.button("← Restart"):
            _reset_state()
            st.rerun()
        return

    user = result["brand_name"]
    user_sov = result["sov"].get(user, 0)
    awareness = result["ai_brand_awareness"]
    ai_category = result["ai_category"]

    st.title("Your AI Visibility Report")

    # ── SOV hero ──────────────────────────────────────────────────────────────
    col_sov, col_aware = st.columns([1, 2])
    with col_sov:
        st.metric(f"Share of Voice — {user}", f"{user_sov}%")
        st.caption("% of 20 prompts where you appear in AI's top 3")
    with col_aware:
        badge, blurb = _AWARENESS_BADGE.get(awareness, ("⚪ —", ""))
        st.markdown(f"### {badge}")
        st.caption(blurb)

    if awareness == "unknown":
        st.error(
            "**AI doesn't know your brand exists.** This is more serious than a low SOV. "
            "Before optimizing for prompts, you need foundational presence: a Wikipedia entry, "
            "press mentions in named outlets, and citations on industry directories."
        )

    st.markdown("---")

    # ── AI's Category ─────────────────────────────────────────────────────────
    st.markdown("### 🧠 How AI Categorizes You")
    if ai_category:
        st.info(f'"{ai_category}"')
        st.caption("This is the mental category AI assistants place your brand in. If it doesn't match your positioning, you have a categorization gap.")

    st.markdown("---")

    # ── Brand comparison chart ────────────────────────────────────────────────
    st.markdown("### 📊 Share of Voice vs Competitors")
    sov_df = pd.DataFrame(
        {"SOV (%)": list(result["sov"].values())},
        index=list(result["sov"].keys()),
    )
    st.bar_chart(sov_df, horizontal=True)

    # Position score table
    st.markdown("**Position Score** (rank-1 = 3 pts, rank-2 = 2 pts, rank-3 = 1 pt; max 60)")
    ps_df = pd.DataFrame(
        [
            {"Brand": b, "Position Score": s, "SOV %": result["sov"].get(b, 0)}
            for b, s in result["position_scores"].items()
        ]
    )
    st.dataframe(ps_df, hide_index=True, use_container_width=True)

    st.markdown("---")

    # ── Category Strength ─────────────────────────────────────────────────────
    st.markdown(f"### 🎯 {user}'s Strength by Query Type")
    cat = result["category_strength"]
    cat_df = pd.DataFrame(
        [
            {"Query Type": _TYPE_LABELS.get(t, t), "Your SOV %": v}
            for t, v in cat.items()
        ]
    )
    st.dataframe(cat_df, hide_index=True, use_container_width=True)

    weakest = min(cat.items(), key=lambda kv: kv[1]) if cat else None
    strongest = max(cat.items(), key=lambda kv: kv[1]) if cat else None
    if weakest and strongest:
        st.caption(
            f"Strongest: **{_TYPE_LABELS.get(strongest[0])}** ({strongest[1]}%) · "
            f"Weakest: **{_TYPE_LABELS.get(weakest[0])}** ({weakest[1]}%)"
        )

    # ── Control prompt check ─────────────────────────────────────────────────
    cp = result["control_prompt"]
    if cp.get("prompt_text"):
        st.markdown("---")
        st.markdown("### 🧪 Quality-Control Benchmark")
        st.code(f'"{cp["prompt_text"]}"', language="text")
        if cp.get("present"):
            st.success(f"✓ {user} appears in AI's answer to this generic category query.")
        else:
            st.warning(
                f"⚠️ {user} does **NOT** appear when AI is asked the most generic category question. "
                f"Current winners: {', '.join(cp.get('top_3', []))}"
            )

    st.markdown("---")

    # ── CTA ───────────────────────────────────────────────────────────────────
    n_opp = len(result["opportunities"])
    st.markdown("### Ready to Close the Gap?")
    st.markdown(
        f"Unlock **{n_opp} priority opportunities** where competitors are winning answers you should be in — "
        "with specific page titles, comparison axes, and SEO actions you can ship this quarter."
    )
    if st.button(f"🔓 Unlock Top {n_opp} Opportunities — $49", type="primary"):
        st.session_state.step = "paid"
        st.rerun()

    st.markdown("---")
    if st.button("Analyze Another Brand"):
        _reset_state()
        st.rerun()


def render_paid_report() -> None:
    result = st.session_state.result
    if not result:
        st.error("No result available.")
        return

    user = result["brand_name"]
    opportunities = result["opportunities"]
    simulation = result["simulation"]

    st.title("Full AI Visibility Report")
    st.success(f"✓ Unlocked — top opportunities and full prompt breakdown below")

    # ── Top 5 opportunities ───────────────────────────────────────────────────
    st.markdown("### 🎯 Top Priority Opportunities")
    if not opportunities:
        st.info("No gap opportunities found — you appear in every tracked prompt.")
    for opp in opportunities:
        rank = opp.get("rank", "?")
        text = opp.get("prompt_text", "")
        winning = opp.get("currently_winning", [])
        why = opp.get("why_you_should_win", "")
        action = opp.get("specific_action", "")
        with st.expander(f"#{rank} — \"{text}\"", expanded=(rank == 1)):
            st.markdown(f"**Currently winning:** {', '.join(winning) if winning else '(no brands)'}")
            st.markdown(f"**Why you belong here:** {why}")
            st.markdown(f"**Specific action:**")
            st.info(action)

    st.markdown("---")

    # ── Direct comparison breakdown ──────────────────────────────────────────
    cmps = result["direct_comparisons"]
    if cmps:
        st.markdown("### ⚔️ Direct Brand Comparisons")
        cmp_rows = []
        for c in cmps:
            cmp_rows.append({
                "Prompt": c["prompt"],
                f"{user} rank": f"#{c['user_rank']}" if c.get("user_rank") else "ABSENT",
                "Top 3 in answer": " > ".join(c["top_3_brands"][:3]),
            })
        st.dataframe(pd.DataFrame(cmp_rows), hide_index=True, use_container_width=True)

    st.markdown("---")

    # ── Full 20-prompt table ─────────────────────────────────────────────────
    st.markdown("### 📋 Full 20-Prompt Simulation")
    rows = []
    for s in simulation:
        top_3_str = " > ".join(
            (e.get("matched_brand") or e.get("brand") or "?")
            for e in s.get("top_3", [])
        )
        rows.append({
            "ID": s.get("id"),
            "Type": _TYPE_LABELS.get(s.get("type", ""), s.get("type", "")),
            "Prompt": s.get("text", ""),
            "Top 3 (AI predicts)": top_3_str or "—",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # Expandable per-prompt reasons
    with st.expander("See per-citation reasons (why AI mentions each brand)"):
        for s in simulation:
            st.markdown(f"**#{s.get('id')} — {s.get('text', '')}**")
            top3 = s.get("top_3", [])
            if not top3:
                st.caption("(No brands cited)")
                continue
            for entry in top3:
                brand = entry.get("matched_brand") or entry.get("brand", "?")
                reason = entry.get("reason", "")
                st.markdown(f"- **{entry.get('rank', '?')}. {brand}** — {reason}")

    st.markdown("---")

    # ── Cost / usage ──────────────────────────────────────────────────────────
    usage = result["usage"]
    st.caption(
        f"Analyzed by Claude Sonnet 4.5 · {usage['calls']} LLM calls · "
        f"{usage['input_tokens']} input / {usage['output_tokens']} output tokens · "
        f"${result['estimated_cost_usd']:.4f}"
    )

    # ── CTA: monthly monitoring (demo) ────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🔁 Track This Over Time")
    st.markdown(
        "AI's view of your brand changes as new content is published. Get weekly snapshots, "
        "track SOV trends, and get alerts when new opportunities open up."
    )
    if st.button("📅 Set Up Monthly Monitoring", type="primary"):
        st.toast("Monthly monitoring is in private beta — we'll email you when it's live.")

    if st.button("Analyze Another Brand"):
        _reset_state()
        st.rerun()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="Prompt Opportunity Engine",
        page_icon="🎯",
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
    elif step == "paid":
        render_paid_report()


if __name__ == "__main__":
    main()
