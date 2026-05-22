"""MVP-C: GEO Asset Generator — backward-compatible standalone launcher.

Run with:  streamlit run mvp_c_assets/app.py --server.port 8501

The actual UI lives in mvp_c_assets/ui.py so it can also be mounted as a page
inside the unified geo_toolkit_app.py multi-page Streamlit app.
"""

from __future__ import annotations

import streamlit as st

from mvp_c_assets.ui import render_assets_page


def main() -> None:
    st.set_page_config(
        page_title="GEO Asset Generator",
        page_icon="🌐",
        layout="centered",
    )
    render_assets_page()


if __name__ == "__main__":
    main()
