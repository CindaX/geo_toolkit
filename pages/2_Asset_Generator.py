"""Multi-page mount: GEO Asset Generator.

Streamlit automatically picks up files in pages/ and shows them in the sidebar
of the unified geo_toolkit_app.py. The UI itself lives in mvp_c_assets/ui.py.
"""

from __future__ import annotations

import streamlit as st

from mvp_c_assets.ui import render_assets_page

st.set_page_config(
    page_title="Asset Generator",
    page_icon="📦",
    layout="centered",
)
render_assets_page()
