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
from html import escape as _html_escape
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
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

# Brand palette (used across the redesigned PDF).
_PRIMARY     = HexColor("#2563EB")   # blue, H1 + accents
_SUCCESS     = HexColor("#10B981")   # green, Good status text
_WARNING     = HexColor("#F59E0B")   # amber, Fair status text
_DANGER      = HexColor("#EF4444")   # red, Needs-work status text
_GRAY_BG     = HexColor("#F3F4F6")   # light gray, code-block background
_GRAY_BORDER = HexColor("#D1D5DB")   # gray border for code blocks
_BAND_GOOD   = HexColor("#D4EDDA")   # pale green, status band
_BAND_FAIR   = HexColor("#FFF3CD")   # pale amber, status band
_BAND_POOR   = HexColor("#F8D7DA")   # pale red, status band
_BAND_NONE   = HexColor("#E5E7EB")   # pale gray, failed status band


def _band_for(score) -> HexColor:
    if score is None or not isinstance(score, (int, float)):
        return _BAND_NONE
    if score >= 60:
        return _BAND_GOOD
    if score >= 40:
        return _BAND_FAIR
    return _BAND_POOR


def _status_text_color(score) -> HexColor:
    if score is None or not isinstance(score, (int, float)):
        return HexColor("#6B7280")
    if score >= 60:
        return _SUCCESS
    if score >= 40:
        return _WARNING
    return _DANGER


def _difficulty_bucket(difficulty: str) -> str:
    """Return one of 'easy' / 'medium' / 'hard' from a free-form difficulty string."""
    d = (difficulty or "").lower()
    if "easy" in d:
        return "easy"
    if "hard" in d:
        return "hard"
    return "medium"  # default bucket for "Medium" or anything unrecognized


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
    fix_count = sum(
        1 for k, v in (audit_results or {}).items()
        if isinstance(v, dict) and v.get("fix")
    )
    print(
        f"[PDF_GEN_DEBUG] generating PDF with {fix_count}/{len(audit_results or {})} dims having fix",
        flush=True,
    )
    dimensions: list[dict] = []
    for key, result in (audit_results or {}).items():
        score = result.get("score")
        status_text, _ = _status_for(score)
        dimensions.append({
            "key":         key,                           # for sorting / lookup
            "name":        _DIM_LABELS.get(key, key),
            "score":       score if score is not None else "—",
            "raw_score":   score,
            "status":      status_text,
            "description": (result.get("description") or "").strip(),
            "fix":         result.get("fix"),             # unified fix schema (or None)
            "details":     result.get("details", {}) or {},
            "weight":      float(result.get("weight") or 0.0),
        })

    # Sort: failed last, lowest score first (most urgent at top).
    dimensions.sort(key=lambda d: (d["raw_score"] is None, d["raw_score"] if d["raw_score"] is not None else 0))

    return {
        "overall_score":    int(geo_score) if geo_score is not None else 0,
        "ai_understanding": (ai_understanding or "").strip(),
        "dimensions":       dimensions,
    }


def _make_styles():
    """Return the dict of ParagraphStyles used throughout the PDF."""
    base = getSampleStyleSheet()
    return {
        "base":      base,
        "score_big": ParagraphStyle(
            "ScoreBig", parent=base["Normal"],
            fontSize=72, leading=80, alignment=1,
            textColor=HexColor("#0F172A"), spaceAfter=12,
        ),
        "h1":        ParagraphStyle(
            "H1", parent=base["Heading1"],
            fontSize=24, leading=28, spaceAfter=14,
            textColor=_PRIMARY,
        ),
        "h2":        ParagraphStyle(
            "H2", parent=base["Heading2"],
            fontSize=18, leading=22, spaceAfter=10,
        ),
        "section":   ParagraphStyle(
            "Section", parent=base["Heading3"],
            fontSize=14, leading=18, spaceBefore=0, spaceAfter=0,
            textColor=_PRIMARY,
        ),
        "body":      ParagraphStyle(
            "Body", parent=base["BodyText"],
            fontSize=11, leading=15, spaceAfter=6,
        ),
        "italic":    base["Italic"],
        "code":      ParagraphStyle(
            "Code", parent=base["Code"],
            fontSize=9, leading=12, fontName="Courier",
            textColor=HexColor("#111827"),
            leftIndent=0, rightIndent=0,
            spaceBefore=0, spaceAfter=0,
        ),
        "meta":      ParagraphStyle(
            "Meta", parent=base["Normal"],
            fontSize=10, leading=13, textColor=HexColor("#4B5563"),
        ),
    }


def _build_status_band(name: str, score, status: str, styles: dict):
    """Single-row colored band: dimension name (left) + Score/Status (right)."""
    band_color = _band_for(score)
    text_color = _status_text_color(score)
    name_para = Paragraph(f"<b>{name}</b>", ParagraphStyle(
        "BandName", parent=styles["base"]["Normal"],
        fontSize=16, leading=20, textColor=HexColor("#0F172A"),
    ))
    score_para = Paragraph(
        f"<b>{score} / 100</b> — <font color='{text_color.hexval()}'>{status}</font>",
        ParagraphStyle(
            "BandScore", parent=styles["base"]["Normal"],
            fontSize=12, leading=16, alignment=2,  # right-align
        ),
    )
    t = Table([[name_para, score_para]], colWidths=[3.6 * inch, 3.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), band_color),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
        ("TOPPADDING",    (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _section_heading(text: str, styles: dict):
    """Section heading with a thick blue left-border (compensates for no emoji).

    A 1×1 Table with LINEBEFORE = 3pt _PRIMARY gives the equivalent of a CSS
    ``border-left: 3px solid blue`` band — much stronger visual marker than
    a plain heading, and reads as "this is a labelled section".
    """
    para = Paragraph(f"<b>{text}</b>", styles["section"])
    t = Table([[para]], colWidths=[6.6 * inch])
    t.setStyle(TableStyle([
        ("LINEBEFORE",    (0, 0), (0, -1), 3, _PRIMARY),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _build_code_block(code: str, styles: dict):
    """Code snippet — plain Preformatted (no Table wrapper, no gray bg).

    Tried both ``Table`` and ``LongTable`` to add a gray background + border;
    both raised ``LayoutError`` for long code (e.g. all-4-Schema.org-types
    template at 79 lines, reported as 964pt vs 679pt page). reportlab's
    LongTable docs claim cross-page splitting works, but for a 1-row table
    where the single cell exceeds page height the row is still atomic and
    the error fires before split is attempted.

    Working fallback: bare ``Preformatted`` (a Paragraph subclass that DOES
    split correctly across pages). Trade-off: no background fill / no
    border. The blue ``LINEBEFORE`` section heading above this block + the
    Courier font are still strong enough visual cues to read as "code".
    """
    escaped = _html_escape(code.rstrip(), quote=False)
    return Preformatted(escaped, styles["code"])


def _build_difficulty_label(difficulty: str, styles: dict) -> Paragraph:
    """Small inline-style difficulty badge as a Paragraph."""
    bucket = _difficulty_bucket(difficulty)
    color = {"easy": _SUCCESS, "medium": _WARNING, "hard": _DANGER}.get(bucket, _WARNING)
    return Paragraph(
        f"<font color='{color.hexval()}'><b>Difficulty:</b> {difficulty or 'Medium'}</font>",
        styles["meta"],
    )


def _build_top_3_block(dimensions: list[dict], styles: dict):
    """Top-3 priority fixes block for the cover page.

    Sort by (100 - score) * weight desc, take 3 with non-None fix.
    Returns a list of flowables (may be empty).
    """
    def _gap_x_weight(d):
        raw = d.get("raw_score")
        if raw is None:
            return -1.0
        return (100 - raw) * float(d.get("weight") or 0.0)

    candidates = [d for d in dimensions if d.get("fix") is not None]
    candidates.sort(key=_gap_x_weight, reverse=True)
    top_3 = candidates[:3]
    if not top_3:
        return []

    flow = [
        _section_heading("Top 3 Priority Fixes", styles),
        Spacer(1, 0.1 * inch),
    ]
    for rank, d in enumerate(top_3, start=1):
        fix = d["fix"]
        title = fix.get("title", "Improve this dimension")
        impact = fix.get("impact", "Medium")
        difficulty = fix.get("difficulty", "Medium")
        how_to = (fix.get("how_to_fix") or "").strip()
        snippet = how_to[:120] + ("…" if len(how_to) > 120 else "")
        body_html = (
            f"<b>{rank}. {title}</b> &nbsp;<i>({d.get('name', '')})</i><br/>"
            f"<font color='{HexColor('#4B5563').hexval()}'>"
            f"Impact: <b>{impact}</b> &nbsp;|&nbsp; Difficulty: <b>{difficulty}</b>"
            f"</font><br/>"
            f"{snippet}"
        )
        flow.append(Paragraph(body_html, styles["body"]))
        flow.append(Spacer(1, 0.08 * inch))
    return flow


def _build_dimension_page(d: dict, styles: dict) -> list:
    """Build all flowables for one dimension's detail page (no trailing PageBreak)."""
    name        = d.get("name", "Unknown")
    score       = d.get("score", "—")
    status      = d.get("status", "")
    description = d.get("description", "")
    fix         = d.get("fix")

    flow = [
        _build_status_band(name, score, status, styles),
        Spacer(1, 0.2 * inch),
    ]

    if description:
        flow.append(_section_heading("What we found", styles))
        flow.append(Spacer(1, 0.08 * inch))
        flow.append(Paragraph(description, styles["body"]))
        flow.append(Spacer(1, 0.2 * inch))

    if fix:
        why = (fix.get("why_matters") or "").strip()
        how = (fix.get("how_to_fix") or "").strip()
        difficulty = (fix.get("difficulty") or "").strip()
        snippet = (fix.get("code_snippet") or "").strip()

        if why:
            flow.append(_section_heading("Why this matters", styles))
            flow.append(Spacer(1, 0.08 * inch))
            flow.append(Paragraph(why, styles["body"]))
            flow.append(Spacer(1, 0.2 * inch))

        if how:
            flow.append(_section_heading("How to fix it", styles))
            flow.append(Spacer(1, 0.08 * inch))
            flow.append(Paragraph(how, styles["body"]))
            if difficulty:
                flow.append(_build_difficulty_label(difficulty, styles))
            flow.append(Spacer(1, 0.2 * inch))

        if snippet:
            flow.append(_section_heading("Ready-to-use code", styles))
            flow.append(Spacer(1, 0.08 * inch))
            flow.append(_build_code_block(snippet, styles))
    else:
        flow.append(Paragraph(
            "<i>Detailed fix instructions unavailable for this dimension. "
            "Email <b>hi@geotoolkit.app</b> for a manual review.</i>",
            styles["body"],
        ))

    return flow


def _build_30day_plan(dimensions: list[dict], styles: dict) -> list:
    """30-Day GEO Improvement Plan page — fixes grouped by difficulty."""
    easy, medium, hard = [], [], []
    for d in dimensions:
        fix = d.get("fix")
        if not fix:
            continue
        bucket = _difficulty_bucket(fix.get("difficulty", ""))
        entry = (d.get("name", "Unknown"), fix.get("title", "Improve this dimension"))
        if bucket == "easy":
            easy.append(entry)
        elif bucket == "hard":
            hard.append(entry)
        else:
            medium.append(entry)

    flow = [
        Paragraph("Your 30-Day GEO Improvement Plan", styles["h1"]),
        Spacer(1, 0.1 * inch),
    ]

    def _section(label: str, items: list, empty_msg: str | None):
        # Skip the section entirely when items is empty AND no empty_msg is set
        # — avoids the "No fixes in this difficulty band" awkward placeholder.
        if not items and empty_msg is None:
            return
        flow.append(Paragraph(label, styles["h2"]))
        if items:
            for name, title in items:
                flow.append(Paragraph(f"&bull; <b>{name}</b>: {title}", styles["body"]))
        else:
            flow.append(Paragraph(f"<i>{empty_msg}</i>", styles["body"]))
        flow.append(Spacer(1, 0.15 * inch))

    _section(
        "Week 1: Quick Wins (Easy fixes)", easy,
        "No quick wins identified — focus on Week 2-3 items first.",
    )
    # Medium: hide the whole section if no items (don't show empty heading)
    _section("Week 2–3: Medium-Effort Improvements", medium, None)
    _section(
        "Week 4: Strategic Investments (Hard fixes)", hard,
        "Your priority fixes are all addressed in earlier weeks.",
    )

    flow.append(Spacer(1, 0.2 * inch))
    flow.append(Paragraph(
        "<i>Re-audit your site after 30 days at "
        "https://geotoolkit.streamlit.app to track progress.</i>",
        styles["italic"],
    ))
    return flow


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
    styles = _make_styles()
    base = styles["base"]
    elements: list = []

    # ── Cover page ──────────────────────────────────────────────────────────
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(Paragraph("GEO Audit Report", styles["h1"]))
    elements.append(Paragraph(f"<b>{brand_name}</b>", styles["h2"]))
    elements.append(Paragraph(url or "", styles["italic"]))
    elements.append(Paragraph(datetime.now().strftime("%B %d, %Y"), styles["italic"]))
    elements.append(Spacer(1, 0.4 * inch))

    overall = audit_result.get("overall_score", 0)
    elements.append(Paragraph("Your GEO Score", base["Heading3"]))
    elements.append(Paragraph(f"{overall}", styles["score_big"]))
    elements.append(Paragraph("/ 100", base["Heading3"]))
    elements.append(Spacer(1, 0.25 * inch))

    ai_understanding = audit_result.get("ai_understanding", "")
    if ai_understanding:
        elements.append(Paragraph("<b>What AI Thinks You Do</b>", base["Heading4"]))
        elements.append(Paragraph(f'"{ai_understanding}"', styles["body"]))
        elements.append(Spacer(1, 0.25 * inch))

    dimensions: list[dict] = audit_result.get("dimensions", []) or []
    if dimensions:
        elements.append(Paragraph("<b>Dimension Summary</b>", base["Heading4"]))
        table_data = [["Dimension", "Score", "Status"]]
        for d in dimensions:
            table_data.append([d.get("name", ""), str(d.get("score", "—")), d.get("status", "")])
        t = Table(table_data, colWidths=[3 * inch, 0.9 * inch, 1.5 * inch])
        status_color_rows = []
        for i, d in enumerate(dimensions, start=1):
            _, color = _status_for(d.get("raw_score"))
            status_color_rows.append(("TEXTCOLOR", (2, i), (2, i), color))
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), HexColor("#F1F5F9")),
            ("GRID",          (0, 0), (-1, -1), 0.5, HexColor("#CBD5E1")),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 10),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("FONTNAME",      (2, 1), (2, -1), "Helvetica-Bold"),
            *status_color_rows,
        ]))
        elements.append(t)

    # Top 3 Priority Fixes block on the cover.
    top3 = _build_top_3_block(dimensions, styles)
    if top3:
        elements.append(Spacer(1, 0.3 * inch))
        elements.extend(top3)

    elements.append(PageBreak())

    # ── Per-dimension detail pages (one each) ──────────────────────────────
    for d in dimensions:
        elements.extend(_build_dimension_page(d, styles))
        elements.append(PageBreak())

    # ── 30-Day Action Plan page ────────────────────────────────────────────
    elements.extend(_build_30day_plan(dimensions, styles))
    elements.append(PageBreak())

    # ── Summary / next-steps page ──────────────────────────────────────────
    elements.append(Paragraph("Next Steps", styles["h1"]))
    elements.append(Paragraph(
        "Your GEO Score reflects how AI systems (ChatGPT, Claude, Perplexity) "
        "understand and recommend your brand. To improve:",
        styles["body"],
    ))
    elements.append(Spacer(1, 0.1 * inch))
    elements.append(Paragraph(
        "1. Address red-flagged dimensions first — they hurt visibility the most.",
        styles["body"],
    ))
    elements.append(Paragraph(
        "2. Use the per-dimension code snippets in the previous pages.",
        styles["body"],
    ))
    elements.append(Paragraph(
        "3. Re-audit in 2–4 weeks to track progress.",
        styles["body"],
    ))
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(Paragraph(
        "<i>Generated by GEO Toolkit — https://geotoolkit.streamlit.app</i>",
        styles["italic"],
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
