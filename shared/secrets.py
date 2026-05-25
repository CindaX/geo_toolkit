"""Centralized secret retrieval: Streamlit Cloud secrets → .env → os.environ.

Why this module exists
----------------------
On Streamlit Cloud, secrets are set via the dashboard and exposed through
``st.secrets``. Locally, developers use a ``.env`` file at the project root.
Hardcoding either path in every caller would mean if/else everywhere; this
module hides the difference behind a single ``get_secret(key)`` call.

Lookup order
------------
1. ``st.secrets[key]`` — production (Streamlit Cloud), if running inside a
   Streamlit script context AND ``.streamlit/secrets.toml`` exists.
2. ``.env`` at project root, loaded via python-dotenv (idempotent).
3. ``os.environ[key]`` — for CI / shell-set vars.
4. ``default`` — caller's fallback if not found anywhere.

The function never raises; callers decide how to handle a missing secret
(typically by calling a wrapper that raises a domain-specific error).
"""

from __future__ import annotations

import os
from pathlib import Path

_DOTENV_LOADED = False


def _ensure_dotenv_loaded() -> None:
    """Load .env at the project root, once per process. Silent if missing."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(env_path, override=False)
    except ImportError:
        pass
    _DOTENV_LOADED = True


def get_secret(key: str, default: str | None = None) -> str | None:
    """Return ``key`` from the layered secrets sources (see module docstring)."""
    # 1. Streamlit Cloud secrets (preferred when available).
    # Accessing st.secrets without a secrets.toml file raises an exception;
    # so does indexing into it for a missing key. Catch broadly.
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass

    # 2 + 3. .env + os.environ.
    _ensure_dotenv_loaded()
    return os.environ.get(key, default)
