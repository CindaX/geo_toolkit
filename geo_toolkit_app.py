"""GEO Toolkit — unified multi-page Streamlit entry point.

Uses the official st.navigation + st.Page API (Streamlit ≥1.36) to register
each tool with an explicit sidebar label and URL slug. This replaces the
older pages/ auto-discovery, which derived the main-entry label from the
filename ("geo toolkit app").

Each tool's UI lives in mvp_*/ui.py and uses a `<prefix>_*` session_state
namespace (audit_, prompts_, assets_) so they don't collide when all three
are mounted in the same Streamlit process.

Backward-compatible: streamlit run mvp_*/app.py still launches the
standalone single-page versions on their original ports.
"""

from __future__ import annotations

import streamlit as st

from mvp_a_audit.ui import render_audit_page
from mvp_b_prompt.ui import render_prompts_page
from mvp_c_assets.ui import render_assets_page

st.set_page_config(
    page_title="GEO Toolkit",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

pg = st.navigation([
    st.Page(render_audit_page,   title="GEO Audit",       icon="🔍", default=True, url_path="audit"),
    st.Page(render_prompts_page, title="Prompt Engine",   icon="📊", url_path="prompts"),
    st.Page(render_assets_page,  title="Asset Generator", icon="📦", url_path="assets"),
])
pg.run()
