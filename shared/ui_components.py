"""Reusable Streamlit UI components for the three MVP apps.

These are deliberately small wrappers — they keep the visual style
consistent across MVPs without imposing a heavy abstraction. Streamlit is
imported at function-call time so this module is cheap to import in
non-UI contexts (tests, CLI tools).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Literal, TypedDict

FieldType = Literal["text", "url", "textarea", "select", "number"]


class FormField(TypedDict, total=False):
    """A field descriptor for :func:`render_input_form`."""

    name: str
    label: str
    type: FieldType
    options: list[str]   # required when type == "select"
    default: Any
    placeholder: str
    help: str


# --- Header --------------------------------------------------------------

def render_header(title: str, subtitle: str | None = None) -> None:
    """Render a page header with an optional subtitle."""
    import streamlit as st

    st.title(title)
    if subtitle:
        st.caption(subtitle)


# --- Input form ----------------------------------------------------------

def render_input_form(
    fields: list[FormField],
    *,
    submit_label: str = "Run",
    form_key: str = "main_form",
) -> dict[str, Any]:
    """Render a Streamlit form and return submitted values.

    Args:
        fields: List of :class:`FormField` descriptors.
        submit_label: Text on the submit button.
        form_key: Streamlit form key (must be unique per page).

    Returns:
        ``{}`` if the form has not been submitted yet, otherwise a dict
        keyed by each field's ``name`` with the submitted value.
    """
    import streamlit as st

    values: dict[str, Any] = {}
    with st.form(key=form_key):
        for field in fields:
            name = field["name"]
            label = field.get("label", name)
            ftype: FieldType = field.get("type", "text")  # type: ignore[assignment]
            default = field.get("default")
            placeholder = field.get("placeholder", "")
            help_text = field.get("help")

            if ftype == "textarea":
                values[name] = st.text_area(
                    label, value=default or "", placeholder=placeholder, help=help_text
                )
            elif ftype == "select":
                options = field.get("options", []) or []
                idx = 0
                if default in options:
                    idx = options.index(default)
                values[name] = st.selectbox(label, options=options, index=idx, help=help_text)
            elif ftype == "number":
                values[name] = st.number_input(
                    label, value=default if default is not None else 0, help=help_text
                )
            else:
                # text / url
                values[name] = st.text_input(
                    label, value=default or "", placeholder=placeholder, help=help_text
                )

        submitted = st.form_submit_button(submit_label)

    return values if submitted else {}


# --- Score card ----------------------------------------------------------

def render_score_card(
    score: int | float,
    label: str,
    *,
    max_score: int = 100,
    delta: str | None = None,
) -> None:
    """Render a single big score with a label.

    Color follows the same thresholds as the HTML report: ``>=75`` green,
    ``>=50`` amber, otherwise red. (Streamlit's ``metric`` doesn't take a
    raw color, so we render a small badge above it instead.)
    """
    import streamlit as st

    if score >= 75:
        emoji = "🟢"
    elif score >= 50:
        emoji = "🟡"
    else:
        emoji = "🔴"
    st.markdown(f"**{emoji} {label}**")
    st.metric(label=" ", value=f"{score} / {max_score}", delta=delta, label_visibility="collapsed")


# --- Recommendations -----------------------------------------------------

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def render_recommendations_list(items: list[dict[str, Any]]) -> None:
    """Render a list of recommendations sorted by priority.

    Each item is expected to look like::

        {"priority": "high"|"medium"|"low",
         "title": "...",
         "description": "..."}

    Missing fields are tolerated.
    """
    import streamlit as st

    if not items:
        st.info("No recommendations.")
        return

    sorted_items = sorted(
        items,
        key=lambda it: _PRIORITY_ORDER.get(str(it.get("priority", "")).lower(), 99),
    )
    for item in sorted_items:
        priority = str(item.get("priority", "")).lower()
        title = str(item.get("title") or "(untitled)")
        description = str(item.get("description") or "")
        badge = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")
        with st.expander(f"{badge} {title}"):
            if description:
                st.write(description)
            extras = {k: v for k, v in item.items() if k not in ("priority", "title", "description")}
            if extras:
                st.json(extras)


# --- Loading -------------------------------------------------------------

@contextmanager
def render_loading(message: str = "Working...") -> Iterator[None]:
    """Context manager that shows a Streamlit spinner with ``message``."""
    import streamlit as st

    with st.spinner(message):
        yield
