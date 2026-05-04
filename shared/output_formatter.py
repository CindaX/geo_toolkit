"""Format report data as JSON, Markdown, or self-contained HTML.

All three functions accept the same ``data`` dict and produce a string. The
shape of ``data`` is not constrained — formatters walk it generically — but
top-level keys ``title``, ``score``, ``summary``, ``sections``, and
``recommendations`` get nicer rendering when present.
"""

from __future__ import annotations

import html as html_lib
import json
from typing import Any

# --- JSON ----------------------------------------------------------------

def to_json(data: dict[str, Any], *, indent: int = 2) -> str:
    """Render ``data`` as a pretty-printed JSON string."""
    return json.dumps(data, indent=indent, ensure_ascii=False, default=str)


# --- Markdown ------------------------------------------------------------

def to_markdown(data: dict[str, Any], *, title: str | None = None) -> str:
    """Render ``data`` as a Markdown document.

    Top-level keys are turned into ``##`` sections. Lists become bullet
    lists; nested dicts become nested headings.
    """
    lines: list[str] = []
    doc_title = title or data.get("title")
    if doc_title:
        lines.append(f"# {doc_title}")
        lines.append("")

    for key, value in data.items():
        if key == "title" and doc_title == value:
            continue
        lines.append(f"## {_humanize(key)}")
        lines.extend(_render_md_value(value, level=3))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_md_value(value: Any, *, level: int) -> list[str]:
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            out.append(f"{'#' * level} {_humanize(k)}")
            out.extend(_render_md_value(v, level=level + 1))
        return out
    if isinstance(value, list):
        if not value:
            return ["_(empty)_"]
        out = []
        for item in value:
            if isinstance(item, dict):
                # Render dicts as a labeled bullet block.
                first = True
                for k, v in item.items():
                    prefix = "- " if first else "  "
                    out.append(f"{prefix}**{_humanize(k)}**: {_inline(v)}")
                    first = False
            else:
                out.append(f"- {_inline(item)}")
        return out
    return [_inline(value)]


def _inline(value: Any) -> str:
    if value is None:
        return "_n/a_"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _humanize(key: str) -> str:
    return key.replace("_", " ").strip().title()


# --- HTML ----------------------------------------------------------------

_HTML_STYLE = """
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         color: #1f2328; background: #fafbfc; margin: 0; padding: 32px 16px; }
  .container { max-width: 800px; margin: 0 auto; }
  h1 { font-size: 28px; margin: 0 0 8px; }
  h2 { font-size: 20px; margin: 28px 0 8px; padding-bottom: 4px;
       border-bottom: 1px solid #e1e4e8; }
  h3 { font-size: 16px; margin: 20px 0 6px; color: #424a53; }
  .card { background: #fff; border: 1px solid #e1e4e8; border-radius: 8px;
          padding: 16px 20px; margin: 12px 0; }
  .score { display: inline-block; padding: 8px 16px; border-radius: 999px;
           font-weight: 600; font-size: 24px; color: #fff; }
  .score.good { background: #1a7f37; }
  .score.warn { background: #bf8700; }
  .score.bad  { background: #cf222e; }
  ul { padding-left: 20px; }
  li { margin: 4px 0; }
  .muted { color: #656d76; font-size: 13px; }
  pre { background: #f6f8fa; padding: 12px; border-radius: 6px; overflow-x: auto; }
"""


def to_html_report(
    data: dict[str, Any],
    *,
    title: str = "GEO Report",
) -> str:
    """Render ``data`` as a single self-contained HTML document.

    ``data`` keys are walked generically (same as :func:`to_markdown`). If
    ``data["score"]`` is an int/float, it is highlighted in a colored pill.
    """
    body_parts: list[str] = []
    doc_title = html_lib.escape(str(data.get("title") or title))
    body_parts.append(f"<h1>{doc_title}</h1>")

    score = data.get("score")
    if isinstance(score, (int, float)):
        cls = _score_class(float(score))
        body_parts.append(
            f'<div class="card"><span class="score {cls}">{score}</span>'
            f' <span class="muted">/ 100</span></div>'
        )

    for key, value in data.items():
        if key in ("title", "score"):
            continue
        body_parts.append(f"<h2>{html_lib.escape(_humanize(key))}</h2>")
        body_parts.append(f'<div class="card">{_render_html_value(value)}</div>')

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        f"<title>{doc_title}</title>"
        f"<style>{_HTML_STYLE}</style>"
        "</head><body><div class=\"container\">"
        + "".join(body_parts)
        + "</div></body></html>"
    )


def _score_class(score: float) -> str:
    if score >= 75:
        return "good"
    if score >= 50:
        return "warn"
    return "bad"


def _render_html_value(value: Any) -> str:
    if isinstance(value, dict):
        parts: list[str] = []
        for k, v in value.items():
            parts.append(f"<h3>{html_lib.escape(_humanize(k))}</h3>")
            parts.append(_render_html_value(v))
        return "".join(parts)
    if isinstance(value, list):
        if not value:
            return '<p class="muted">(empty)</p>'
        items: list[str] = []
        for item in value:
            if isinstance(item, dict):
                inner = "; ".join(
                    f"<strong>{html_lib.escape(_humanize(k))}:</strong> "
                    f"{html_lib.escape(_inline(v))}"
                    for k, v in item.items()
                )
                items.append(f"<li>{inner}</li>")
            else:
                items.append(f"<li>{html_lib.escape(_inline(item))}</li>")
        return "<ul>" + "".join(items) + "</ul>"
    return f"<p>{html_lib.escape(_inline(value))}</p>"
