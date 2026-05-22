"""Multi-page mount: Prompt Opportunity Engine.

Streamlit automatically picks up files in pages/ and shows them in the sidebar
of the unified geo_toolkit_app.py. The UI itself lives in mvp_b_prompt/ui.py.
"""

from __future__ import annotations

import streamlit as st

from mvp_b_prompt.ui import render_prompts_page

st.set_page_config(
    page_title="Prompt Engine",
    page_icon="📊",
    layout="centered",
)
render_prompts_page()
