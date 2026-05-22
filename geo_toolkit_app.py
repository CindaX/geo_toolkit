"""GEO Toolkit — unified multi-page Streamlit entry point.

Run with:  streamlit run geo_toolkit_app.py

The default landing page is the GEO Audit (the most-used tool). The sidebar
exposes Prompt Engine and Asset Generator via files in pages/.

Each tool's UI lives in mvp_*/ui.py and uses a `<prefix>_*` session_state
namespace (audit_, prompts_, assets_) so they don't collide when all three
are mounted in the same Streamlit process.
"""

from __future__ import annotations

import streamlit as st

from mvp_a_audit.ui import render_audit_page


def main() -> None:
    st.set_page_config(
        page_title="GEO Toolkit",
        page_icon="🌍",
        layout="centered",
    )
    render_audit_page()


if __name__ == "__main__":
    main()
