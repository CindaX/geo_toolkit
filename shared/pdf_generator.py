"""Generate detailed PDF audit reports.

Usage:
    from shared.pdf_generator import generate_audit_pdf, audit_result_to_pdf_dict

    pdf_dict = audit_result_to_pdf_dict(
        audit_results=st.session_state.audit_results,
        geo_score=st.session_state.audit_geo_score,
        ai_understanding=st.session_state.audit_ai_understanding,
    )
    pdf_bytes = generate_audit_pdf(pdf_dict, brand_name, url)
    st.download_button(data=pdf_bytes, ...)

PDF layout:
  Page 1: cover (brand + date + big GEO Score + 8-dim summary table)
  Pages 2..N: one page per dimension (score + description)
  Last page: next-steps summary + product footer

English only — Helvetica (built-in, no font files needed → Cloud-safe).
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Friendly dimension labels (mirrors mvp_a_audit/ui.py::_DIM_LABELS).
_DIM_LABELS: dict[str, str] = {
    "crawler_access":     "AI Crawler Access",
    "llms_txt":           "llms.txt Presence",
    "schema_markup":      "Schema Markup",
    "brand_clarity":      "Brand Clarity",
    "content_citability": "Content Citability",
    "eeat_signals":       "E-E-A-T Signals",
    "competitive":        "Competitive Positioning",
    "factual_density":    "Factual Density",
}

# Status text + color band (no emojis — Helvetica has no glyphs for them).
_STATUS_GOOD     = ("Good",       HexColor("#16A34A"))   # green
_STATUS_FAIR     = ("Fair",       HexColor("#CA8A04"))   # amber
_STATUS_POOR     = ("Needs work", HexColor("#DC2626"))   # red
_STATUS_FAILED   = ("Failed",     HexColor("#6B7280"))   # gray


def _status_for(score: int | float | None) -> tuple[str, HexColor]:
    if score is None:
        return _STATUS_FAILED
    if score >= 60:
        return _STATUS_GOOD
    if score >= 40:
        return _STATUS_FAIR
    return _STATUS_POOR


def audit_result_to_pdf_dict(
    audit_results: dict,
    geo_score: float | None,
    ai_understanding: str | None,
) -> dict:
    """Adapt mvp_a_audit's session_state shape to the PDF input dict.

    `audit_results` is the dict-of-dicts produced by run_audit (keys =
    dimension internal names; values = {score, description, weight, ...}).
    """
    dimensions: list[dict] = []
    for key, result in (audit_results or {}).items():
        score = result.get("score")
        status_text, _ = _status_for(score)
        dimensions.append({
            "name":        _DIM_LABELS.get(key, key),
            "score":       score if score is not None else "—",
            "raw_score":   score,
            "status":      status_text,
            "description": (result.get("description") or "").strip(),
        })

    # Sort: failed last, lowest score first (most urgent at top).
    dimensions.sort(key=lambda d: (d["raw_score"] is None, d["raw_score"] if d["raw_score"] is not None else 0))

    return {
        "overall_score":    int(geo_score) if geo_score is not None else 0,
        "ai_understanding": (ai_understanding or "").strip(),
        "dimensions":       dimensions,
    }


def generate_audit_pdf(audit_result: dict, brand_name: str, url: str) -> bytes:
    """Build the PDF and return raw bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        title=f"GEO Audit — {brand_name}",
        author="GEO Toolkit",
    )

    styles = getSampleStyleSheet()
    score_big = ParagraphStyle(
        "ScoreBig", parent=styles["Normal"],
        fontSize=72, leading=80, alignment=1,
        textColor=HexColor("#0F172A"),
        spaceAfter=12,
    )
    h1 = ParagraphStyle(
        "H1", parent=styles["Heading1"],
        fontSize=20, leading=24, spaceAfter=12,
    )
    body = styles["BodyText"]
    italic = styles["Italic"]

    elements: list = []

    # ── Cover page ────────────────────────────────────────────────────────
    elements.append(Spacer(1, 0.4 * inch))
    elements.append(Paragraph("GEO Audit Report", h1))
    elements.append(Paragraph(f"<b>{brand_name}</b>", styles["Heading2"]))
    elements.append(Paragraph(url or "", italic))
    elements.append(Paragraph(datetime.now().strftime("%B %d, %Y"), italic))
    elements.append(Spacer(1, 0.5 * inch))

    overall = audit_result.get("overall_score", 0)
    elements.append(Paragraph("Your GEO Score", styles["Heading3"]))
    elements.append(Paragraph(f"{overall}", score_big))
    elements.append(Paragraph("/ 100", styles["Heading3"]))
    elements.append(Spacer(1, 0.3 * inch))

    ai_understanding = audit_result.get("ai_understanding", "")
    if ai_understanding:
        elements.append(Paragraph("<b>What AI Thinks You Do</b>", styles["Heading4"]))
        elements.append(Paragraph(f'"{ai_understanding}"', body))
        elements.append(Spacer(1, 0.3 * inch))

    dimensions: list[dict] = audit_result.get("dimensions", []) or []
    if dimensions:
        elements.append(Paragraph("<b>Dimension Summary</b>", styles["Heading4"]))
        table_data = [["Dimension", "Score", "Status"]]
        for d in dimensions:
            table_data.append([d.get("name", ""), str(d.get("score", "—")), d.get("status", "")])
        t = Table(table_data, colWidths=[3 * inch, 0.9 * inch, 1.5 * inch])
        # Per-row status text color
        status_color_rows = []
        for i, d in enumerate(dimensions, start=1):
            _, color = _status_for(d.get("raw_score"))
            status_color_rows.append(("TEXTCOLOR", (2, i), (2, i), color))
        t.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0), HexColor("#F1F5F9")),
            ("GRID",         (0, 0), (-1, -1), 0.5, HexColor("#CBD5E1")),
            ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, -1), 10),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING",   (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("FONTNAME",     (2, 1), (2, -1), "Helvetica-Bold"),
            *status_color_rows,
        ]))
        elements.append(t)

    elements.append(PageBreak())

    # ── Detail pages (one per dimension) ─────────────────────────────────
    for d in dimensions:
        name = d.get("name", "Unknown")
        score = d.get("score", "—")
        status = d.get("status", "")
        description = d.get("description", "")

        elements.append(Paragraph(name, h1))
        _, color = _status_for(d.get("raw_score"))
        status_style = ParagraphStyle(
            "DimStatus", parent=styles["Heading3"],
            textColor=color, spaceAfter=12,
        )
        elements.append(Paragraph(f"Score: {score} / 100 — {status}", status_style))

        if description:
            elements.append(Paragraph("<b>What we found</b>", styles["Heading4"]))
            elements.append(Paragraph(description, body))
            elements.append(Spacer(1, 0.15 * inch))

        elements.append(PageBreak())

    # ── Summary / next-steps page ────────────────────────────────────────
    elements.append(Paragraph("Next Steps", h1))
    elements.append(Paragraph(
        "Your GEO Score reflects how AI systems (ChatGPT, Claude, Perplexity) "
        "understand and recommend your brand. To improve:",
        body,
    ))
    elements.append(Spacer(1, 0.15 * inch))
    elements.append(Paragraph(
        "1. Address red-flagged dimensions first — they hurt visibility the most.",
        body,
    ))
    elements.append(Paragraph(
        "2. Re-audit in 2–4 weeks to track progress.",
        body,
    ))
    elements.append(Paragraph(
        "3. Subscribe to GEO Toolkit Pro for weekly automated tracking.",
        body,
    ))
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(Paragraph(
        "<i>Generated by GEO Toolkit — https://geotoolkit.streamlit.app</i>",
        italic,
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
