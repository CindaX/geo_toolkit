"""MVP-A: GEO Audit Report — backward-compatible standalone launcher.

Run with:  streamlit run mvp_a_audit/app.py --server.port 8502

The actual UI lives in mvp_a_audit/ui.py so it can also be mounted as a page
inside the unified geo_toolkit_app.py multi-page Streamlit app.
"""

from __future__ import annotations

import streamlit as st

from mvp_a_audit.ui import render_audit_page


def main() -> None:
    st.set_page_config(
        page_title="GEO Audit",
        page_icon="🔍",
        layout="centered",
    )
    render_audit_page()


if __name__ == "__main__":
    main()
