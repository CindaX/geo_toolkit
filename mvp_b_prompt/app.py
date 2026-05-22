"""MVP-B: Prompt Opportunity Engine — backward-compatible standalone launcher.

Run with:  streamlit run mvp_b_prompt/app.py --server.port 8503

The actual UI lives in mvp_b_prompt/ui.py so it can also be mounted as a page
inside the unified geo_toolkit_app.py multi-page Streamlit app.
"""

from __future__ import annotations

import streamlit as st

from mvp_b_prompt.ui import render_prompts_page


def main() -> None:
    st.set_page_config(
        page_title="Prompt Opportunity Engine",
        page_icon="🎯",
        layout="centered",
    )
    render_prompts_page()


if __name__ == "__main__":
    main()
